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

from claw.feeds.wind_utils import (
    call_wind_cli_as_rows,
    plain_code_to_windcode,
    wind_available,
)

logger = logging.getLogger("wind_analytics")


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
        """获取风险指标（Beta, Sharpe, VaR 等）

        Args:
            code: 股票代码
            period: 分析周期描述
        """
        windcode = plain_code_to_windcode(code)
        return call_wind_cli_as_rows(
            "stock_data",
            "get_risk_metrics",
            {"question": f"{windcode} {period}"},
            timeout=15,
        )

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
