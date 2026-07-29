"""
Wind 万得高级分析工具 — 超越基础行情的投研能力

涵盖：
- 公司新闻 / 公告查询
- 技术指标（MACD, KDJ, RSI 等）
- 股东信息 / 股本结构
- 公司事件（增发、并购、分红等）
- 风险指标（Beta, Sharpe, VaR）
- 指数基本面（PE/PB 历史分位）
- 宏观 EDB 数据（GDP, CPI, PMI 等）

用法:
    from claw.feeds.wind_analytics import WindAnalytics
    wa = WindAnalytics()
    news = wa.get_news("贵州茅台")
    technicals = wa.get_technicals("600519")
    events = wa.get_events("600519")
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from claw.feeds.risk_beta import AKSHARE_RISK_ENABLED  # 可选兜底开关（默认关闭）
from claw.feeds.wind_utils import (
    call_wind_cli_as_rows,
    plain_code_to_windcode,
    wind_available,
)

logger = logging.getLogger("wind_analytics")


def _coerce_float(v: Any) -> float | None:
    """将 Wind 返回的字段安全转为 float，None / 空 / 非数字返回 None"""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


class WindAnalytics:
    """Wind 万得高级分析工具集

    所有方法返回 list[dict] 或 None。
    支持 A 股主板/中小板，创业板/科创板数据可能不完整。
    """

    @property
    def available(self) -> bool:
        return wind_available()  # 每次实时检查，支持长生命周期进程

    # ── 新闻与公告 ──

    def get_news(self, query: str, top_k: int = 5) -> list[dict[str, Any]] | None:
        """获取财经新闻

        Args:
            query: 查询关键词（不得含空格）
            top_k: 返回条数
        """
        return call_wind_cli_as_rows(
            "financial_docs",
            "get_financial_news",
            {"query": query.strip().replace(" ", ""), "top_k": top_k},
        )

    def get_announcements(
        self, query: str, top_k: int = 5
    ) -> list[dict[str, Any]] | None:
        """获取公司公告 / 年报 / 季报

        Args:
            query: 公司名称或代码 + 查询内容（不得含空格）
            top_k: 返回条数
        """
        return call_wind_cli_as_rows(
            "financial_docs",
            "get_company_announcements",
            {"query": query.strip().replace(" ", ""), "top_k": top_k},
        )

    # ── 技术指标 ──

    def get_technicals(
        self, code: str, period: str = "近60日MACD走势"
    ) -> list[dict[str, Any]] | None:
        """获取技术指标（MACD, KDJ, RSI, BOLL 等）

        Args:
            code: 股票代码（裸 6 位）
            period: 分析周期描述，如 "近60日MACD走势"
        """
        windcode = plain_code_to_windcode(code)
        return call_wind_cli_as_rows(
            "stock_data",
            "get_stock_technicals",
            {"question": f"{windcode} {period}"},
            timeout=20,
        )

    def get_risk_metrics(
        self, code: str, period: str = "过去1年Beta和波动率"
    ) -> list[dict[str, Any]] | None:
        """获取风险指标（Beta, 波动率 等）

        Args:
            code: 股票代码
            period: 分析周期描述

        Returns:
            list[dict]，每行含原始字段 + 防御性标注：
            - ``beta_suspect``: 当 Beta 不在合理区间 [-1, 2] 时置 True
              （Wind 当前口径偶发返回负值/失真，见下方说明）
            - ``vol_suspect``: 当年化波动率不在 [0.05, 1.5] 合理范围时置 True
            - ``risk_note``: 人类可读的口径提示

        说明：Wind ``get_risk_metrics`` 接口返回的 Beta / 波动率存在口径不稳定问题
        （实测茅台/招行/平安等蓝筹 Beta 为负、波动率恒在 1.0~1.7 区间失真）。
        本方法对原始值做合理性校验并打标，调用方**不得**将 ``*_suspect=True``
        的值直接用于仓位/风险预算计算，仅作参考。

        注意：曾尝试用 akshare 历史日线回归做兜底校正，但验证发现**当前沙箱环境的
        历史日线接口（akshare / 腾讯 web 日线）均返回失真数据**（如腾讯日线 600036
        在 2026-07-01 返回 34.5，而实时快照实际为 38.x，偏差 ~25%）。仅 ``qt.gtimg.cn``
        实时快照接口可靠。因此**暂不自动用历史日线源替换 Wind 值**——suspect=True 时
        应人工核验，而非信任任一自动源。
        """
        windcode = plain_code_to_windcode(code)
        rows = call_wind_cli_as_rows(
            "stock_data",
            "get_risk_metrics",
            {"question": f"{windcode} {period}"},
            timeout=15,
        )
        if not rows:
            return None

        BETA_MIN, BETA_MAX = 0.0, 2.0
        VOL_MIN, VOL_MAX = 0.05, 1.5  # 年化波动率常规上限 ~150%

        for r in rows:
            if not isinstance(r, dict):
                continue
            beta = _coerce_float(r.get("过去1年BETA") or r.get("过去1年Beta"))
            vol = _coerce_float(
                r.get("过去1年波动率") or r.get("过去1年年化波动率")
            )
            r["beta_suspect"] = (beta is not None and not (BETA_MIN <= beta <= BETA_MAX))
            r["vol_suspect"] = (vol is not None and not (VOL_MIN <= vol <= VOL_MAX))
            notes = []
            if r["beta_suspect"]:
                notes.append("Beta 超出合理区间[0,2]，Wind 口径失真，须人工核验")
            if r["vol_suspect"]:
                notes.append("波动率超出合理年化区间[5%,150%]，Wind 口径失真，须人工核验")

            # 可选兜底：akshare 历史日线回归（默认关闭，见 risk_beta.AKSHARE_RISK_ENABLED）
            # 当前沙箱历史日线源失真，启用也不产生可信值；仅正常网络环境启用有效
            if (r["beta_suspect"] or r["vol_suspect"]) and AKSHARE_RISK_ENABLED:
                try:
                    from claw.feeds.risk_beta import get_beta_vol_akshare
                    ak_beta, ak_vol = get_beta_vol_akshare(code)
                    corrected = []
                    if r["beta_suspect"] and ak_beta is not None:
                        r["过去1年BETA"] = ak_beta
                        r["beta"] = ak_beta
                        r["beta_suspect"] = False
                        corrected.append(f"Beta 已用 akshare 校正={ak_beta}")
                    if r["vol_suspect"] and ak_vol is not None:
                        r["过去1年波动率"] = ak_vol
                        r["volatility"] = ak_vol
                        r["vol_suspect"] = False
                        corrected.append(f"波动率已用 akshare 校正={ak_vol}")
                    notes = corrected  # 校正成功后覆盖原失真提示
                except Exception as _e:  # noqa: BLE001
                    logger.warning(f"akshare 兜底校正失败 ({code}): {_e}")

            r["risk_note"] = "；".join(notes) if notes else "数值在合理区间内"

        return rows

    # ── 股东与事件 ──

    def get_shareholders(
        self, code: str
    ) -> list[dict[str, Any]] | None:
        """获取前十大股东

        Args:
            code: 股票代码
        """
        windcode = plain_code_to_windcode(code)
        return call_wind_cli_as_rows(
            "stock_data",
            "get_stock_equity_holders",
            {"question": f"{windcode}前十大股东"},
            timeout=15,
        )

    def get_events(
        self, code: str, event_type: str = "增发和并购事件"
    ) -> list[dict[str, Any]] | None:
        """获取公司事件（分红、增发、并购等）

        Args:
            code: 股票代码
            event_type: 事件类型描述
        """
        windcode = plain_code_to_windcode(code)
        return call_wind_cli_as_rows(
            "stock_data",
            "get_stock_events",
            {"question": f"{windcode} {event_type}"},
            timeout=15,
        )

    # ── 指数基本面 ──

    def get_index_fundamentals(
        self, index_name: str = "沪深300"
    ) -> list[dict[str, Any]] | None:
        """获取指数 PE/PB 等基本面数据

        Args:
            index_name: 指数名称或代码
        """
        return call_wind_cli_as_rows(
            "index_data",
            "get_index_fundamentals",
            {"question": f"{index_name}PE/PB历史分位"},
            timeout=15,
        )

    # ── 宏观数据 ──

    def get_macro_data(
        self,
        indicator: str,
        begin_date: str | None = None,
        end_date: str | None = None,
        observation: str | None = None,
    ) -> list[dict[str, Any]] | None:
        """获取宏观经济指标（GDP, CPI, PMI 等）

        Args:
            indicator: 指标描述，如 "中国CPI同比"
            begin_date: 起始日期 yyyyMMdd
            end_date: 结束日期 yyyyMMdd
            observation: 近 N 期，如 "10"。与 begin_date/end_date 互斥
        """
        params: dict[str, Any] = {
            "executionMode": "searchFetch",
            "question": indicator,
        }
        if observation:
            params["observation"] = observation
        elif begin_date and end_date:
            params["beginDate"] = begin_date
            params["endDate"] = end_date
        else:
            # 默认近 5 年
            end = datetime.now()
            start = end - timedelta(days=365 * 5)
            params["beginDate"] = start.strftime("%Y%m%d")
            params["endDate"] = end.strftime("%Y%m%d")

        return call_wind_cli_as_rows(
            "economic_data",
            "natural_language_get_edb_data",
            params,
            timeout=25,
        )

    # ── 选股与筛选 ──

    def search_stocks(
        self, condition: str
    ) -> list[dict[str, Any]] | None:
        """条件选股

        Args:
            condition: 自然语言筛选条件，如 "沪深市场市值超500亿且连续5日上涨"
        """
        return call_wind_cli_as_rows(
            "stock_data",
            "search_stocks",
            {"question": condition.strip()},
            timeout=20,
        )
