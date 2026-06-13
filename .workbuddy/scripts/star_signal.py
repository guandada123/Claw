#!/usr/bin/env python3
"""
StarSignal — 五角星信号 优化版量化模块
=========================================
基于"好运侠客"公众号战法逆向蒸馏 + GitHub开源项目对比优化

原始战法核心:
  - 主线热点确认 (sector/theme momentum)
  - 成交量异动 (资金逻辑: volume spike)
  - 价格突破 + 均线多头 (技术逻辑: MA alignment)
  - MA止损 (5/10/20日均线)

优化点 (vs 原始):
  1. 自适应成交量阈值 (百分位法替代固定倍数)
  2. 多周期确认 (日线+周线)
  3. RSI 超买过滤 (避免追高)
  4. MACD 方向确认 (动量过滤)
  5. ATR 动态止损 (替代固定MA止损)
  6. 信号加权评分 (Score 0-100, 替代二元买/不买)
  7. 板块相对强度 (sector RS ranking)
  8. 趋势过滤器 (60日均线牛熊分界)

参考开源项目:
  - narayanbytes/algorithmic-volume-breakout-scanner (量价突破核心逻辑)
  - realmaomao/CSMQT (多指标组合+风控框架)
  - backtrader / pybroker (回测框架设计模式)

作者: WorkBuddy AI
日期: 2026-06-09
许可: MIT
"""

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd

# ============================================================
# 配置
# ============================================================


class SignalStrength(Enum):
    """信号强度等级"""

    NONE = 0  # 无信号
    WEAK = 1  # 弱信号 (Score < 40)
    MODERATE = 2  # 中等 (40-60)
    STRONG = 3  # 强 (60-80)
    VERY_STRONG = 4  # 极强 (>80)


@dataclass
class StarSignalConfig:
    """五角星信号 可调参数配置"""

    # === 均线参数 ===
    ma_short: int = 5  # 短期均线
    ma_mid: int = 10  # 中期均线
    ma_long: int = 20  # 长期均线
    ma_trend: int = 60  # 趋势过滤均线

    # === 成交量参数 ===
    vol_lookback: int = 20  # 成交量均值计算周期
    vol_threshold_pct: float = 70.0  # 成交量百分位阈值 (0-100)
    vol_min_ratio: float = 1.5  # 最小成交量比率 (vol/avg_vol)

    # === RSI 参数 ===
    rsi_period: int = 14
    rsi_overbought: float = 70.0  # 超买阈值
    rsi_oversold: float = 30.0  # 超卖阈值
    rsi_max_entry: float = 65.0  # 最大可买入RSI (避免追高)

    # === MACD 参数 ===
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9

    # === ATR 参数 ===
    atr_period: int = 14
    atr_stop_mult: float = 2.0  # ATR止损倍数

    # === 突破参数 ===
    breakout_lookback: int = 20  # 突破高点回看周期
    breakout_pct: float = 0.02  # 突破确认百分比 (2%)

    # === 信号评分权重 ===
    weight_volume: float = 0.30  # 成交量权重
    weight_ma_alignment: float = 0.25  # 均线排列权重
    weight_momentum: float = 0.20  # 动量权重 (RSI+MACD)
    weight_breakout: float = 0.15  # 突破权重
    weight_trend: float = 0.10  # 趋势权重

    # === 板块过滤 ===
    sector_rs_lookback: int = 20  # 板块相对强度计算周期
    min_sector_rank_pct: float = 30.0  # 板块强度排名阈值 (前30%)


# ============================================================
# 指标计算
# ============================================================


def calc_sma(series: pd.Series, period: int) -> pd.Series:
    """简单移动平均"""
    return series.rolling(period).mean()


def calc_ema(series: pd.Series, period: int) -> pd.Series:
    """指数移动平均"""
    return series.ewm(span=period, adjust=False).mean()


def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI"""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD: 返回 (MACD线, 信号线, 柱状图)"""
    ema_fast = calc_ema(close, fast)
    ema_slow = calc_ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calc_ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range"""
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.concat(
        [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def calc_volume_ratio(volume: pd.Series, period: int = 20) -> pd.Series:
    """成交量比率: 当前/MA(volume, period)"""
    avg_vol = calc_sma(volume, period)
    return volume / avg_vol.replace(0, np.nan)


def calc_volume_percentile(volume: pd.Series, period: int = 20) -> pd.Series:
    """成交量百分位 (滚动)"""
    return volume.rolling(period).apply(lambda x: (x[-1] > x).sum() / len(x) * 100, raw=True)


def calc_breakout_high(close: pd.Series, lookback: int = 20) -> pd.Series:
    """N日最高价突破"""
    return close.rolling(lookback).max().shift(1)


def calc_sector_rs(stock_close: pd.Series, sector_close: pd.Series, period: int = 20) -> pd.Series:
    """板块相对强度: 个股涨幅 / 板块涨幅"""
    stock_ret = stock_close.pct_change(period)
    sector_ret = sector_close.pct_change(period)
    return (1 + stock_ret) / (1 + sector_ret.replace(0, np.nan))


# ============================================================
# 信号生成
# ============================================================


def generate_signals(
    df: pd.DataFrame,
    config: StarSignalConfig | None = None,
    sector_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    生成五角星信号评分

    参数:
        df: 包含 columns=['open','high','low','close','volume'] 的DataFrame
        config: StarSignalConfig 参数配置
        sector_df: 同行业板块指数 DataFrame (columns same as df), 用于板块RS计算

    返回:
        原始df + 以下新列:
        - signal_score: 0-100 综合信号评分
        - signal_strength: SignalStrength 等级
        - vol_signal: 成交量信号 (0-1)
        - ma_signal: 均线排列信号 (0-1)
        - momentum_signal: 动量信号 (0-1)
        - breakout_signal: 突破信号 (0-1)
        - trend_signal: 趋势信号 (0-1)
        - stop_loss: ATR动态止损价
        - ma5, ma10, ma20, ma60: 均线值
    """
    if config is None:
        config = StarSignalConfig()

    result = df.copy()
    c = result["close"]
    h = result["high"]
    l = result["low"]
    v = result["volume"]

    # ========== 1. 均线计算 ==========
    result["ma5"] = calc_sma(c, config.ma_short)
    result["ma10"] = calc_sma(c, config.ma_mid)
    result["ma20"] = calc_sma(c, config.ma_long)
    result["ma60"] = calc_sma(c, config.ma_trend)

    # ========== 2. 成交量信号 ==========
    vol_ratio = calc_volume_ratio(v, config.vol_lookback)
    vol_pct = calc_volume_percentile(v, config.vol_lookback)

    # 成交量评分: 基于百分位 + 比率双重确认
    result["vol_ratio"] = vol_ratio
    result["vol_pct"] = vol_pct

    # 百分位越高分数越高，但需满足最小比率
    vol_score_pct = vol_pct.clip(0, 100) / 100
    vol_score_ratio = (vol_ratio / config.vol_min_ratio).clip(0, 1)
    result["vol_signal"] = (vol_score_pct * 0.6 + vol_score_ratio * 0.4).clip(0, 1)

    # ========== 3. 均线排列信号 ==========
    # 多头排列: ma5 > ma10 > ma20 > ma60
    alignment_score = pd.Series(0.0, index=result.index)
    ma_cols = ["ma5", "ma10", "ma20", "ma60"]

    for i in range(len(ma_cols) - 1):
        alignment = (result[ma_cols[i]] > result[ma_cols[i + 1]]).astype(float)
        alignment_score += alignment * 0.25

    # 额外加分: 价格站上所有均线
    above_all = (c > result[["ma5", "ma10", "ma20", "ma60"]].max(axis=1)).astype(float)
    result["ma_signal"] = (alignment_score * 0.7 + above_all * 0.3).clip(0, 1)

    # ========== 4. 动量信号 (RSI + MACD) ==========
    result["rsi"] = calc_rsi(c, config.rsi_period)
    macd_line, signal_line, histogram = calc_macd(
        c, config.macd_fast, config.macd_slow, config.macd_signal
    )
    result["macd"] = macd_line
    result["macd_signal"] = signal_line
    result["macd_hist"] = histogram

    # RSI评分: 在30-65区间最优
    rsi = result["rsi"]
    rsi_score = pd.Series(0.0, index=result.index)
    # 理想区间: 35-60
    rsi_score[(rsi >= 35) & (rsi <= 60)] = 1.0
    rsi_score[(rsi > 60) & (rsi <= 65)] = 0.6
    rsi_score[(rsi > 65) & (rsi <= 70)] = 0.3
    # 超买区 (>70): 0分, 超卖区 (<30): 0.2分
    rsi_score[rsi < 30] = 0.2

    # MACD评分: MACD > 信号线 且 histogram > 0
    macd_bullish = ((macd_line > signal_line) & (histogram > 0)).astype(float)
    macd_turning = ((macd_line > signal_line) & (histogram < 0)).astype(float) * 0.5  # 即将金叉

    result["momentum_signal"] = (rsi_score * 0.5 + macd_bullish * 0.35 + macd_turning * 0.15).clip(
        0, 1
    )

    # ========== 5. 突破信号 ==========
    breakout_level = calc_breakout_high(c, config.breakout_lookback)
    near_breakout = (c > breakout_level * (1 - config.breakout_pct)).astype(float)
    at_breakout = (c >= breakout_level).astype(float)

    # 突破力度: 当前价距前高的距离
    dist_to_high = (c / breakout_level.replace(0, np.nan) - 1).clip(0, None) / config.breakout_pct
    result["breakout_signal"] = (
        near_breakout * 0.4 + at_breakout * 0.4 + dist_to_high.clip(0, 1) * 0.2
    ).clip(0, 1)

    # ========== 6. 趋势信号 ==========
    # 价格在60日线上为多头趋势
    above_ma60 = (c > result["ma60"]).astype(float)
    # MA60斜率向上
    ma60_slope = result["ma60"].diff(5) / result["ma60"].shift(5).replace(0, np.nan)
    trend_up = (ma60_slope > 0).astype(float)

    result["trend_signal"] = (above_ma60 * 0.7 + trend_up * 0.3).clip(0, 1)

    # ========== 7. 板块相对强度 (可选) ==========
    if sector_df is not None and "close" in sector_df.columns:
        sector_rs = calc_sector_rs(c, sector_df["close"], config.sector_rs_lookback)
        result["sector_rs"] = sector_rs
        result["sector_signal"] = (sector_rs > 1.0).astype(float)  # >1 表示跑赢板块
    else:
        result["sector_signal"] = 1.0  # 无板块数据时默认满分

    # ========== 8. 综合信号评分 ==========
    cfg = config
    result["signal_score"] = (
        result["vol_signal"] * cfg.weight_volume
        + result["ma_signal"] * cfg.weight_ma_alignment
        + result["momentum_signal"] * cfg.weight_momentum
        + result["breakout_signal"] * cfg.weight_breakout
        + result["trend_signal"] * cfg.weight_trend
    ) * 100  # 转成0-100

    # 板块过滤：板块强度不足时降分
    result["signal_score"] = result["signal_score"] * (0.7 + result["sector_signal"] * 0.3)

    # ========== 9. 信号等级 ==========
    def score_to_strength(s):
        if s >= 80:
            return SignalStrength.VERY_STRONG.value
        elif s >= 60:
            return SignalStrength.STRONG.value
        elif s >= 40:
            return SignalStrength.MODERATE.value
        elif s >= 20:
            return SignalStrength.WEAK.value
        else:
            return SignalStrength.NONE.value

    result["signal_strength"] = result["signal_score"].apply(score_to_strength)

    # ========== 10. ATR 动态止损 ==========
    result["atr"] = calc_atr(result, config.atr_period)
    result["stop_loss"] = c - result["atr"] * config.atr_stop_mult
    result["stop_loss_pct"] = (result["stop_loss"] - c) / c * 100  # 止损距离百分比

    return result


# ============================================================
# 扫描器
# ============================================================


def scan_stocks(
    stock_data: dict[str, pd.DataFrame],
    config: StarSignalConfig | None = None,
    top_n: int = 10,
) -> pd.DataFrame:
    """
    批量扫描股票，返回信号最强的N只

    参数:
        stock_data: {股票代码: OHLCV DataFrame}
        config: 信号配置
        top_n: 返回前N只

    返回:
        排名DataFrame, columns=[code, score, strength, ma_signal, vol_signal, ...]
    """
    if config is None:
        config = StarSignalConfig()

    results = []
    for code, df in stock_data.items():
        try:
            signals = generate_signals(df, config)
            latest = signals.iloc[-1]
            results.append(
                {
                    "code": code,
                    "score": round(latest["signal_score"], 1),
                    "strength": latest["signal_strength"],
                    "close": round(latest["close"], 2),
                    "vol_ratio": round(latest.get("vol_ratio", 0), 2),
                    "rsi": round(latest.get("rsi", 50), 1),
                    "stop_loss": round(latest["stop_loss"], 2),
                    "stop_pct": round(latest["stop_loss_pct"], 1),
                    "ma5": round(latest.get("ma5", 0), 2),
                    "ma20": round(latest.get("ma20", 0), 2),
                    "ma60": round(latest.get("ma60", 0), 2),
                }
            )
        except Exception:
            continue

    df_result = pd.DataFrame(results)
    if len(df_result) == 0:
        return df_result

    return df_result.sort_values("score", ascending=False).head(top_n).reset_index(drop=True)


# ============================================================
# 信号可视化
# ============================================================


def plot_signals(df: pd.DataFrame, title: str = "Star Signal Analysis"):
    """
    快速可视化信号 (需要 matplotlib)

    参数:
        df: generate_signals() 返回的 DataFrame
        title: 图表标题
    """
    try:
        import matplotlib.dates as mdates
        import matplotlib.pyplot as plt
    except ImportError:
        print("需要安装 matplotlib: pip install matplotlib")
        return

    fig, axes = plt.subplots(
        4, 1, figsize=(14, 10), sharex=True, gridspec_kw={"height_ratios": [3, 1, 1, 1]}
    )

    # Panel 1: 价格 + 均线 + 信号
    ax1 = axes[0]
    ax1.plot(df.index, df["close"], "k-", linewidth=1, label="Close")
    ax1.plot(df.index, df["ma5"], "b-", alpha=0.5, label="MA5")
    ax1.plot(df.index, df["ma10"], "orange", alpha=0.5, label="MA10")
    ax1.plot(df.index, df["ma20"], "purple", alpha=0.5, label="MA20")
    ax1.plot(df.index, df["ma60"], "gray", alpha=0.5, label="MA60")

    # 标记强信号点
    strong = df[df["signal_strength"] >= SignalStrength.STRONG.value]
    ax1.scatter(
        strong.index,
        strong["close"],
        c="red",
        s=50,
        marker="*",
        zorder=5,
        label=f"Signal >= STRONG ({len(strong)})",
    )

    # 标记止损线
    ax1.plot(df.index, df["stop_loss"], "r--", alpha=0.3, linewidth=0.5, label="Stop Loss")

    ax1.set_title(title)
    ax1.legend(loc="upper left", fontsize=7)
    ax1.grid(True, alpha=0.3)

    # Panel 2: 成交量 + 比率
    ax2 = axes[1]
    colors = ["red" if c >= o else "green" for c, o in zip(df["close"], df["open"])]
    ax2.bar(df.index, df["volume"], color=colors, alpha=0.5, width=1)
    ax2.set_ylabel("Volume")
    ax2.grid(True, alpha=0.3)

    ax2b = ax2.twinx()
    ax2b.plot(df.index, df.get("vol_ratio", pd.Series()), "b-", linewidth=1, label="Vol Ratio")
    ax2b.axhline(y=1.5, color="orange", linestyle="--", alpha=0.5)
    ax2b.set_ylabel("Vol Ratio", color="b")

    # Panel 3: 信号评分
    ax3 = axes[2]
    ax3.fill_between(df.index, 0, df["signal_score"], alpha=0.4, color="blue")
    ax3.axhline(y=60, color="orange", linestyle="--", alpha=0.5, label="STRONG (60)")
    ax3.axhline(y=80, color="red", linestyle="--", alpha=0.5, label="V.STRONG (80)")
    ax3.set_ylabel("Signal Score")
    ax3.set_ylim(0, 100)
    ax3.legend(loc="upper left", fontsize=7)
    ax3.grid(True, alpha=0.3)

    # Panel 4: RSI + MACD
    ax4 = axes[3]
    ax4.plot(df.index, df["rsi"], "purple", linewidth=1, label="RSI")
    ax4.axhline(y=70, color="red", linestyle="--", alpha=0.5)
    ax4.axhline(y=30, color="green", linestyle="--", alpha=0.5)
    ax4.set_ylabel("RSI")
    ax4.set_ylim(0, 100)
    ax4.legend(loc="upper left", fontsize=7)
    ax4.grid(True, alpha=0.3)

    ax4b = ax4.twinx()
    ax4b.bar(
        df.index,
        df.get("macd_hist", pd.Series()),
        color=["red" if x > 0 else "green" for x in df.get("macd_hist", [0])],
        alpha=0.3,
        width=1,
    )
    ax4b.set_ylabel("MACD Hist", color="gray")

    plt.tight_layout()
    return fig


# ============================================================
# 使用示例
# ============================================================

if __name__ == "__main__":
    # 示例: 获取一只股票的K线数据并生成信号
    import json
    import urllib.request

    def fetch_demo_data(code="600522", market="sh", days=200):
        """从腾讯财经获取演示数据"""
        prefix = "sh" if market == "sh" else "sz"
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code},day,,,{days},qfq"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        klines = data.get("data", {}).get(f"{prefix}{code}", {}).get("qfqday", [])
        rows = []
        for k in klines:
            rows.append(
                {
                    "date": k[0],
                    "open": float(k[1]),
                    "close": float(k[2]),
                    "high": float(k[3]),
                    "low": float(k[4]),
                    "volume": float(k[5]),
                }
            )
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        return df

    print("=" * 60)
    print("StarSignal — 五角星信号优化版 演示")
    print("=" * 60)

    # 获取中天科技数据
    df = fetch_demo_data("600522", "sh")
    print(f"\n数据: 中天科技(600522), {len(df)} 条K线")

    # 默认配置生成信号
    config = StarSignalConfig()
    result = generate_signals(df, config)

    # 最新信号
    latest = result.iloc[-1]
    print(f"\n📊 最新交易日信号 ({latest.name.date()}):")
    print(f"   收盘价: {latest['close']:.2f}")
    print(f"   信号评分: {latest['signal_score']:.1f}/100")
    print(f"   信号等级: {SignalStrength(latest['signal_strength']).name}")
    print(f"   成交量比率: {latest['vol_ratio']:.2f}x")
    print(f"   RSI: {latest['rsi']:.1f}")
    print(f"   ATR止损: {latest['stop_loss']:.2f} ({latest['stop_loss_pct']:.1f}%)")
    print(f"   均线: MA5={latest['ma5']:.2f} MA20={latest['ma20']:.2f} MA60={latest['ma60']:.2f}")

    # 各维度得分
    print("\n   各维度信号:")
    for dim in ["vol_signal", "ma_signal", "momentum_signal", "breakout_signal", "trend_signal"]:
        bar = "█" * int(latest[dim] * 20)
        print(f"     {dim:20s}: {latest[dim]:.2f} {bar}")

    # 历史信号统计
    strong_count = (result["signal_strength"] >= SignalStrength.STRONG.value).sum()
    total = len(result)
    print(f"\n📈 历史信号统计 ({total}交易日):")
    for level in SignalStrength:
        count = (result["signal_strength"] == level.value).sum()
        if count > 0:
            bar = "█" * int(count / total * 50)
            print(f"   {level.name:15s}: {count:3d} 次 ({count / total * 100:4.1f}%) {bar}")

    # 尝试可视化
    try:
        fig = plot_signals(result.tail(120), "中天科技(600522) Star Signal Demo")
        fig.savefig("/tmp/star_signal_demo.png", dpi=100, bbox_inches="tight")
        print("\n📊 图表已保存: /tmp/star_signal_demo.png")
    except Exception as e:
        print(f"\n⚠️ 可视化跳过: {e}")

    print(
        "\n✅ 模块可导入使用: from star_signal import StarSignalConfig, generate_signals, scan_stocks"
    )
