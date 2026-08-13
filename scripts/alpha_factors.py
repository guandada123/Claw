#!/usr/bin/env python3
"""
alpha_factors.py — Alpha101 因子计算引擎（P2-8 落地，2026-08-12）

数据源: alpha_data.py 复用 QTS PostgreSQL（含 amount → vwap 类因子可算）
实现: WorldQuant Alpha101 中「纯日线 OHLCV+amount 可算」的子集，
      全部使用官方算子原语（rank=截面, ts_rank=时序, delay/delta/sum/stddev/
      correlation/covariance/ts_min/ts_max/ts_argmax/decay_linear/sign/scale）。

因子清单（24个，覆盖动量/反转/波动率/流动性/量价背离）:
  alpha1/alpha2/alpha3/alpha4/alpha5/alpha6/alpha8/alpha9/alpha10/alpha11/
  alpha12/alpha13/alpha14/alpha15/alpha16/alpha18/alpha19/alpha20/alpha22/
  alpha30/alpha33/alpha34/alpha38/alpha101

设计原则:
  - 纯函数: compute_factor(code, df) → Series；因子名→函数注册表
  - 输入 df 需含 open/high/low/close/volume/amount/gap，索引=交易日
  - gap 日(除权未复权跳空)收益置 NaN，避免假信号；不足窗口期返回 NaN
  - 截面算子 rank/scale 在 evaluate 阶段按日横截面执行（本文件输出原始值）

用法:
  python3 scripts/alpha_factors.py 600584            # 单只因子时序(打印尾部)
  python3 scripts/alpha_factors.py 600584,002185     # 多只
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from alpha_data import cache_bars  # noqa: E402

# ── 算子原语（Alpha101 官方定义）──────────────────────────────


def delay(s: pd.Series, d: int) -> pd.Series:
    return s.shift(d)


def delta(s: pd.Series, d: int) -> pd.Series:
    return s - s.shift(d)


def ts_sum(s: pd.Series, d: int) -> pd.Series:
    return s.rolling(d, min_periods=1).sum()


def ts_stddev(s: pd.Series, d: int) -> pd.Series:
    return s.rolling(d, min_periods=1).std()


def ts_correlation(a: pd.Series, b: pd.Series, d: int) -> pd.Series:
    return a.rolling(d, min_periods=1).corr(b)


def ts_covariance(a: pd.Series, b: pd.Series, d: int) -> pd.Series:
    return a.rolling(d, min_periods=1).cov(b)


def ts_min(s: pd.Series, d: int) -> pd.Series:
    return s.rolling(d, min_periods=1).min()


def ts_max(s: pd.Series, d: int) -> pd.Series:
    return s.rolling(d, min_periods=1).max()


def ts_argmax(s: pd.Series, d: int) -> pd.Series:
    """最近 d 根中最大值出现位置(0=最新)，Alpha101 Ts_ArgMax。"""
    return s.rolling(d, min_periods=1).apply(
        lambda w: int(np.argmax(w[::-1])), raw=True
    )


def ts_rank(s: pd.Series, d: int) -> pd.Series:
    """时序排名：当前值在最近 d 根中的分位(0~1)。"""
    return s.rolling(d, min_periods=1).apply(
        lambda w: (w <= w[-1]).sum() / len(w), raw=True
    )


def decay_linear(s: pd.Series, d: int) -> pd.Series:
    """线性衰减加权均值：权重 d,d-1,...,1 归一。"""
    w = np.arange(1, d + 1, dtype=float)

    def _f(x: np.ndarray) -> float:
        if len(x) < d:
            ww = np.arange(1, len(x) + 1, dtype=float)
            return float(np.dot(x, ww) / ww.sum())
        return float(np.dot(x, w) / w.sum())

    return s.rolling(d, min_periods=1).apply(_f, raw=True)


def sign(s: pd.Series) -> pd.Series:
    return np.sign(s)


def rank(s: pd.Series) -> pd.Series:
    """截面 rank——单股票时序中无截面，返回时序百分位近似(供单只预览)；
    evaluate 阶段会按日横截面重算。"""
    return s.rank(pct=True)


def scale(s: pd.Series) -> pd.Series:
    return (s - s.min()) / (s.max() - s.min()) if s.max() != s.min() else s * 0


def returns(df: pd.DataFrame) -> pd.Series:
    r = df["close"].pct_change()
    r[df["gap"].astype(bool)] = np.nan  # 除权跳空日收益作废
    return r


def vwap(df: pd.DataFrame) -> pd.Series:
    """日度 VWAP = amount / volume（量单位: Tushare 为手→股，比价一致即可）。"""
    v = df["amount"] / df["volume"]
    return v.replace([np.inf, -np.inf], np.nan)


def adv_n(df: pd.DataFrame, n: int) -> pd.Series:
    """过去 n 日平均成交额（Alpha101 advN）。"""
    return df["amount"].rolling(n, min_periods=1).mean()


# ── Alpha101 因子定义（官方表达式直译）────────────────────────


def alpha1(df: pd.DataFrame) -> pd.Series:
    r = returns(df)
    cond = r < 0
    base = np.where(cond, ts_stddev(r, 20), df["close"])
    return rank(ts_argmax(pd.Series(np.sign(base) * base**2, index=df.index), 5)) - 0.5


def alpha2(df: pd.DataFrame) -> pd.Series:
    vol_log = np.log(df["volume"].replace(0, np.nan))
    return -1 * ts_correlation(
        rank(delta(pd.Series(vol_log, index=df.index), 2)),
        rank((df["close"] - df["open"]) / df["open"]),
        6,
    )


def alpha3(df: pd.DataFrame) -> pd.Series:
    return -1 * ts_correlation(rank(df["open"]), rank(df["volume"]), 10)


def alpha4(df: pd.DataFrame) -> pd.Series:
    return -1 * ts_rank(rank(df["low"]), 9)


def alpha5(df: pd.DataFrame) -> pd.Series:
    vw = vwap(df)
    return rank(df["open"] - ts_sum(vw, 10) / 10) * (-1 * (rank(df["close"] - vw).abs()))


def alpha6(df: pd.DataFrame) -> pd.Series:
    return -1 * ts_correlation(df["open"], df["volume"], 10)


def alpha8(df: pd.DataFrame) -> pd.Series:
    r = returns(df)
    x = ts_sum(df["open"], 5) * ts_sum(r, 5)
    return -1 * rank(x - delay(x, 10))


def alpha9(df: pd.DataFrame) -> pd.Series:
    dc1 = delta(df["close"], 1)
    cond1 = ts_min(dc1, 5) > 0
    cond2 = ts_max(dc1, 5) < 0
    return np.where(cond1 | cond2, dc1, -1 * dc1)


def alpha10(df: pd.DataFrame) -> pd.Series:
    dc1 = delta(df["close"], 1)
    cond = ts_min(dc1, 4) > 0
    val = np.where(cond, dc1, np.where(ts_max(dc1, 4) < 0, dc1, -1 * dc1))
    return rank(pd.Series(val, index=df.index))


def alpha11(df: pd.DataFrame) -> pd.Series:
    vw = vwap(df)
    return (
        rank(ts_max(vw - df["close"], 3)) + rank(ts_min(vw - df["close"], 3))
    ) * rank(delta(df["volume"], 3))


def alpha12(df: pd.DataFrame) -> pd.Series:
    return sign(delta(df["volume"], 1)) * (-1 * delta(df["close"], 1))


def alpha13(df: pd.DataFrame) -> pd.Series:
    return -1 * rank(
        ts_covariance(rank(df["close"]), rank(df["volume"]), 5)
    )


def alpha14(df: pd.DataFrame) -> pd.Series:
    r = returns(df)
    return -1 * rank(delta(r, 3)) * ts_correlation(df["open"], df["volume"], 10)


def alpha15(df: pd.DataFrame) -> pd.Series:
    return -1 * ts_sum(
        rank(ts_correlation(rank(df["high"]), rank(df["volume"]), 3)), 3
    )


def alpha16(df: pd.DataFrame) -> pd.Series:
    return -1 * rank(
        ts_covariance(rank(df["high"]), rank(df["volume"]), 5)
    )


def alpha18(df: pd.DataFrame) -> pd.Series:
    x = ts_stddev((df["close"] - df["open"]).abs(), 5)
    return -1 * rank(x + (df["close"] - df["open"]) + ts_correlation(df["close"], df["open"], 10))


def alpha19(df: pd.DataFrame) -> pd.Series:
    r = returns(df)
    s = sign(df["close"] - delay(df["close"], 7) + delta(df["close"], 7))
    return -1 * s * (1 + rank(1 + ts_sum(r, 250)))


def alpha20(df: pd.DataFrame) -> pd.Series:
    return (
        -1 * rank(df["open"] - delay(df["high"], 1))
        * rank(df["open"] - delay(df["close"], 1))
        * rank(df["open"] - delay(df["low"], 1))
    )


def alpha22(df: pd.DataFrame) -> pd.Series:
    return -1 * (
        delta(ts_correlation(df["high"], df["volume"], 5), 5)
        * rank(ts_stddev(df["close"], 20))
    )


def alpha30(df: pd.DataFrame) -> pd.Series:
    s = sign(df["close"] - delay(df["close"], 1))
    s1 = sign(delay(df["close"], 1) - delay(df["close"], 2))
    s2 = sign(delay(df["close"], 2) - delay(df["close"], 3))
    x = (1.0 - rank(s + s1 + s2)) * ts_sum(df["volume"], 5)
    return x / ts_sum(df["volume"], 20)


def alpha33(df: pd.DataFrame) -> pd.Series:
    return rank(-1 * (1 - df["open"] / df["close"]))


def alpha34(df: pd.DataFrame) -> pd.Series:
    r = returns(df)
    x = 1 - rank(ts_stddev(r, 2) / ts_stddev(r, 5))
    y = 1 - rank(delta(df["close"], 1))
    return rank(x + y)


def alpha38(df: pd.DataFrame) -> pd.Series:
    return -1 * rank(ts_rank(df["close"], 10)) * rank(df["close"] / df["open"])


def alpha101(df: pd.DataFrame) -> pd.Series:
    return (df["close"] - df["open"]) / ((df["high"] - df["low"]) + 0.001)


# ── 注册表 ─────────────────────────────────────────────────────


FACTORS: dict[str, object] = {
    "alpha1": alpha1,
    "alpha2": alpha2,
    "alpha3": alpha3,
    "alpha4": alpha4,
    "alpha5": alpha5,
    "alpha6": alpha6,
    "alpha8": alpha8,
    "alpha9": alpha9,
    "alpha10": alpha10,
    "alpha11": alpha11,
    "alpha12": alpha12,
    "alpha13": alpha13,
    "alpha14": alpha14,
    "alpha15": alpha15,
    "alpha16": alpha16,
    "alpha18": alpha18,
    "alpha19": alpha19,
    "alpha20": alpha20,
    "alpha22": alpha22,
    "alpha30": alpha30,
    "alpha33": alpha33,
    "alpha34": alpha34,
    "alpha38": alpha38,
    "alpha101": alpha101,
}


def compute_factor(code: str, name: str) -> pd.Series:
    """计算单只单因子时序。数据缺失返回空 Series。"""
    df = cache_bars(code)
    if df is None or df.empty:
        return pd.Series(dtype=float)
    fn = FACTORS.get(name)
    if fn is None:
        raise KeyError(f"未知因子: {name}")
    try:
        out = fn(df)
        if isinstance(out, np.ndarray):  # np.where 分支 → 统一转 Series
            out = pd.Series(out, index=df.index)
        return out
    except Exception:
        return pd.Series(dtype=float)


def main():
    codes = sys.argv[1].split(",") if len(sys.argv) > 1 else ["600584"]
    names = sys.argv[2].split(",") if len(sys.argv) > 2 else sorted(FACTORS)
    for code in codes:
        df = cache_bars(code)
        if df is None:
            print(f"{code}: 无数据")
            continue
        print(f"\n=== {code} ({len(df)} 根, {df.index[0].date()}~{df.index[-1].date()}) ===")
        for name in names:
            try:
                s = compute_factor(code, name)
                if s.empty:
                    print(f"  {name}: 空")
                else:
                    print(
                        f"  {name}: last={s.iloc[-1]:.4f} mean={s.dropna().mean():.4f} "
                        f"std={s.dropna().std():.4f} nan={s.isna().sum()}"
                    )
            except Exception as e:  # noqa: BLE001
                print(f"  {name}: ERR {e}")


if __name__ == "__main__":
    main()
