"""风险指标兜底源 — akshare 历史日线回归 Beta / 年化波动率

⚠️ 重要环境说明（2026-07-23 验证）：
    本沙箱环境的**历史日线接口（akshare / 腾讯 web 日线）返回的数据被污染**
    （腾讯日线 600036 @2026-07-01 返 34.5，而 qt.gtimg.cn 实时快照实际为 38.x，
    偏差 ~25%；akshare stock_zh_a_hist 同期返 43.10，同样失真）。
    仅 ``qt.gtimg.cn`` 实时快照接口可靠。

    因此本模块**默认关闭**（`AKSHARE_RISK_ENABLED = False`）。
    仅在「网络环境正常、历史日线接口可用」的机器上，将其设为 True 才能拿到
    可信的 Beta / 波动率，用于校正 Wind get_risk_metrics 的失真值。

用法:
    from claw.feeds.risk_beta import get_beta_vol_akshare, AKSHARE_RISK_ENABLED
    if AKSHARE_RISK_ENABLED:
        beta, vol = get_beta_vol_akshare("600036")
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ⚠️ 默认关闭：当前沙箱历史日线源失真，启用只会产生错误校正。
# 在有正常网络（能访问 akshare 真实历史日线）的机器上改为 True。
AKSHARE_RISK_ENABLED = False

_BENCHMARK = "sh000300"  # 沪深300 作为 Beta 基准


def _safe_close(code: str, index: bool = False, start: str = "20250723", end: str = "20260723"):
    """抓取个股/指数前复权收盘价序列，返回 (日期列表, 收盘价列表) 或 None"""
    import akshare as ak  # 延迟导入：仅在启用时依赖
    import pandas as pd

    if index:
        d = ak.index_zh_a_hist(symbol=code, period="daily",
                               start_date=start, end_date=end)
    else:
        d = ak.stock_zh_a_hist(symbol=code, period="daily",
                              start_date=start, end_date=end, adjust="qfq")
    d = d[["日期", "收盘"]].copy()
    d["日期"] = pd.to_datetime(d["日期"])
    d = d.sort_values("日期").dropna()
    return d["日期"].tolist(), d["收盘"].tolist()


def get_beta_vol_akshare(
    code: str,
    benchmark: str = _BENCHMARK,
    window: int = 252,
) -> tuple[float | None, float | None]:
    """用 akshare 历史日线回归计算 Beta（对沪深300）与年化波动率

    Args:
        code: 个股裸 6 位代码（如 "600036"）
        benchmark: 基准指数 symbol（默认 sh000300 沪深300）
        window: 回溯交易日数（默认 252 ≈ 1 年）

    Returns:
        (beta, annualized_volatility) 或 (None, None) 失败时

    计算：个股/基准日收益按日期 inner join 对齐后，
        beta = Cov(stock, bench) / Var(bench)
        vol  = Std(stock daily ret) * sqrt(250)
    """
    if not AKSHARE_RISK_ENABLED:
        logger.debug("akshare 风险兜底已关闭（AKSHARE_RISK_ENABLED=False），跳过")
        return None, None
    try:
        import numpy as np
        import pandas as pd

        prefix = "sh" if code.startswith("6") else "sz"
        s_dates, s_close = _safe_close(code, index=False)
        b_dates, b_close = _safe_close(benchmark, index=True)
        if not s_dates or not b_dates:
            return None, None

        s = pd.Series(s_close, index=s_dates, dtype=float)
        b = pd.Series(b_close, index=b_dates, dtype=float)
        # 先按日期对齐（关键：必须对齐后再算收益，否则错位）
        j = pd.concat([s, b], axis=1, keys=["s", "m"]).dropna()
        j = j.tail(window)
        rs = j["s"].pct_change().dropna()
        rm = j["m"].pct_change().dropna()
        aligned = pd.concat([rs, rm], axis=1, join="inner").dropna()
        if len(aligned) < 60:
            logger.warning(f"akshare Beta 样本不足 ({len(aligned)})，跳过")
            return None, None
        cov = np.cov(aligned.iloc[:, 0], aligned.iloc[:, 1])
        beta = cov[0, 1] / cov[1, 1] if cov[1, 1] != 0 else None
        vol = aligned.iloc[:, 0].std() * (250 ** 0.5)
        return (round(float(beta), 2) if beta is not None else None,
                round(float(vol), 2))
    except Exception as e:
        logger.warning(f"akshare Beta/波动率计算失败 ({code}): {e}", exc_info=True)
        return None, None
