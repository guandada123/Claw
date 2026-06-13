#!/usr/bin/env python3
"""
star_signal_adapter.py — star_signal.py 与现有项目的适配层
============================================================
将 star_signal.py 的标准化接口适配到 Claw 项目各脚本的现有调用方式。

用途:
  1. 为 cron_monitor.py / sim_trade.py 提供 ATR 动态止损
  2. 为 expert_team_analyst.py 提供技术面评分
  3. 为 backtest.py 提供信号生成策略
  4. 为 generate_daily_report.py 提供实时量化信号
"""

import json

# 确保可以导入同目录下的 star_signal
import sys
import urllib.request
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from star_signal import (
    SignalStrength,
    StarSignalConfig,
    calc_atr,
    generate_signals,
    scan_stocks,
)


def fetch_kline_df(code: str, market: str = None, days: int = 200) -> pd.DataFrame:
    """
    从腾讯财经API获取K线DataFrame，适配star_signal的输入格式

    参数:
        code: 股票代码 (6位)
        market: 'sh' 或 'sz' (自动推断)
        days: 获取天数
    """
    if market is None:
        market = "sh" if code.startswith(("6", "68")) else "sz"

    prefix = "sh" if market == "sh" else "sz"
    url = (
        f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code},day,,,{days},qfq"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})

    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())

    klines = data.get("data", {}).get(f"{prefix}{code}", {}).get("qfqday", [])
    if not klines:
        klines = data.get("data", {}).get(f"{prefix}{code}", {}).get("day", [])

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


def get_star_signal(code: str) -> dict:
    """
    获取单只股票的五角星信号综合评分（适配 cron_monitor 和 sim_trade 调用）

    返回:
        {
            "score": float,           # 0-100 综合评分
            "strength": int,          # 1-5 信号强度
            "strength_name": str,     # NONE/WEAK/MODERATE/STRONG/VERY_STRONG
            "atr_stop": float,        # ATR动态止损价
            "atr_stop_pct": float,    # 止损距离百分比
            "rsi": float,
            "vol_ratio": float,
            "trend": str,             # "多头"/"震荡"/"空头"
            "ma5": float,
            "ma10": float,
            "ma20": float,
            "ma60": float,
            "close": float,
        }
    """
    try:
        df = fetch_kline_df(code)
        result = generate_signals(df)
        latest = result.iloc[-1]

        strength = SignalStrength(latest["signal_strength"])
        trend_val = latest.get("trend_signal", 0.5)
        trend = "多头" if trend_val > 0.7 else ("震荡" if trend_val > 0.3 else "空头")

        return {
            "score": round(latest["signal_score"], 1),
            "strength": strength.value,
            "strength_name": strength.name,
            "atr_stop": round(latest["stop_loss"], 2),
            "atr_stop_pct": round(latest["stop_loss_pct"], 1),
            "rsi": round(latest["rsi"], 1),
            "vol_ratio": round(latest.get("vol_ratio", 1.0), 2),
            "trend": trend,
            "ma5": round(latest["ma5"], 2),
            "ma10": round(latest["ma10"], 2),
            "ma20": round(latest["ma20"], 2),
            "ma60": round(latest["ma60"], 2),
            "close": round(latest["close"], 2),
        }
    except Exception as e:
        return {"error": str(e), "score": 0, "strength": 0, "strength_name": "ERROR"}


def get_dynamic_stop_loss(code: str, current_price: float) -> dict:
    """
    获取ATR动态止损建议（替代 sim_trade.py 中固定 -8% 止损）

    返回:
        {
            "stop_price": float,     # 建议止损价
            "stop_pct": float,       # 止损距离 (%)
            "method": "ATR",         # 方法标识
        }
    """
    try:
        df = fetch_kline_df(code)
        atr = calc_atr(df, 14)
        latest_atr = atr.iloc[-1]

        # 使用2倍ATR作为止损距离
        stop_price = current_price - latest_atr * 2.0
        stop_pct = (stop_price - current_price) / current_price * 100

        return {
            "stop_price": round(stop_price, 2),
            "stop_pct": round(stop_pct, 1),
            "method": "ATR",
            "atr": round(latest_atr, 2),
        }
    except Exception as e:
        # 降级：固定 -8% 止损
        return {
            "stop_price": round(current_price * 0.92, 2),
            "stop_pct": -8.0,
            "method": "FIXED",
            "error": str(e),
        }


def get_technical_score(code: str) -> dict:
    """
    获取技术面评分（适配 expert_team_analyst.py 的技术分析维度）

    返回:
        {
            "score": float,            # 0-10 技术评分
            "signal_score": float,     # 原始信号评分 (0-100)
            "ma_alignment": float,     # 均线排列评分 (0-1)
            "momentum": float,         # 动量评分 (0-1)
            "volume_signal": float,    # 成交量信号 (0-1)
            "breakout_signal": float,  # 突破信号 (0-1)
            "trend_signal": float,     # 趋势信号 (0-1)
            "rsi": float,
            "macd_hist": float,
            "conclusion": str,         # "买入"/"持有"/"卖出"
            "strength_name": str,
        }
    """
    try:
        df = fetch_kline_df(code)
        result = generate_signals(df)
        latest = result.iloc[-1]

        # 将 0-100 信号评分映射到 0-10 专家评分
        expert_score = latest["signal_score"] / 10.0

        strength = SignalStrength(latest["signal_strength"])

        # 结论判断
        if strength in (SignalStrength.STRONG, SignalStrength.VERY_STRONG):
            conclusion = "买入"
        elif strength == SignalStrength.MODERATE:
            conclusion = "持有"
        else:
            conclusion = "卖出"

        return {
            "score": round(expert_score, 1),
            "signal_score": round(latest["signal_score"], 1),
            "ma_alignment": round(latest["ma_signal"], 3),
            "momentum": round(latest["momentum_signal"], 3),
            "volume_signal": round(latest["vol_signal"], 3),
            "breakout_signal": round(latest["breakout_signal"], 3),
            "trend_signal": round(latest["trend_signal"], 3),
            "rsi": round(latest["rsi"], 1),
            "macd_hist": round(latest.get("macd_hist", 0), 4),
            "conclusion": conclusion,
            "strength_name": strength.name,
        }
    except Exception as e:
        return {"error": str(e), "score": 5.0, "conclusion": "持有"}


def generate_backtest_signals(df: pd.DataFrame, strategy: str = "star_signal") -> dict:
    """
    为 backtest.py 生成买卖信号（兼容现有回测接口）

    策略:
        star_signal — 使用五角星信号评分生成买卖信号
            STRONG/VERY_STRONG → 买入
            跌破ATR止损 → 卖出

    返回:
        {
            "buy_signals": [(date, price), ...],
            "sell_signals": [(date, price, reason), ...],
        }
    """
    result = generate_signals(df)

    buy_signals = []
    sell_signals = []
    holding = False
    entry_price = 0

    for idx in range(len(result)):
        row = result.iloc[idx]
        strength = SignalStrength(row["signal_strength"])

        if not holding and strength in (SignalStrength.STRONG, SignalStrength.VERY_STRONG):
            # 买入信号
            buy_signals.append((idx, row["close"]))
            holding = True
            entry_price = row["close"]

        elif holding:
            # 检查卖出条件
            current_price = row["close"]
            atr_stop = row["stop_loss"]

            # ATR止损
            if current_price <= atr_stop:
                sell_signals.append((idx, current_price, f"ATR止损 (止损价{atr_stop:.2f})"))
                holding = False
            # 信号消失
            elif strength in (SignalStrength.NONE, SignalStrength.WEAK):
                sell_signals.append((idx, current_price, f"信号减弱 ({strength.name})"))
                holding = False

    return {
        "buy_signals": buy_signals,
        "sell_signals": sell_signals,
    }


def batch_scan_watchlist(codes: list[str], top_n: int = 10) -> pd.DataFrame:
    """
    批量扫描选股池，返回排名（适配 sim_watchlist.py）

    参数:
        codes: 股票代码列表
        top_n: 返回前N只

    返回:
        DataFrame with columns: code, score, strength, close, vol_ratio, rsi, trend
    """
    stock_data = {}
    for code in codes:
        try:
            stock_data[code] = fetch_kline_df(code, days=200)
        except Exception:
            continue

    config = StarSignalConfig()
    ranking = scan_stocks(stock_data, config, top_n=top_n)
    return ranking


# ============================================================
# 命令行接口（兼容各脚本的子进程调用）
# ============================================================
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法:")
        print("  python3 star_signal_adapter.py signal <股票代码>      — 获取综合信号")
        print("  python3 star_signal_adapter.py stop <股票代码> <价格>  — 获取动态止损")
        print("  python3 star_signal_adapter.py score <股票代码>        — 获取技术面评分")
        print("  python3 star_signal_adapter.py scan <代码1,代码2,...>  — 批量扫描")
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "signal":
        code = sys.argv[2]
        result = get_star_signal(code)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif cmd == "stop":
        code = sys.argv[2]
        price = float(sys.argv[3])
        result = get_dynamic_stop_loss(code, price)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif cmd == "score":
        code = sys.argv[2]
        result = get_technical_score(code)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif cmd == "scan":
        codes = sys.argv[2].split(",")
        ranking = batch_scan_watchlist(codes)
        print(ranking.to_string(index=False))

    else:
        print(f"未知命令: {cmd}")
