#!/usr/bin/env python3
"""
回测引擎 — 基于东方财富公开API获取历史K线，对指定策略进行回测

用法:
  python3 backtest.py <策略名> <股票代码> [起始日期] [结束日期]

策略:
  ma-cross    — 均线交叉策略（5日MA上穿20日MA买入，下穿卖出）
  breakout    — 突破策略（突破20日最高价买入，跌破20日均线卖出）
  star-signal — 五角星信号策略（star_signal综合评分，STRONG买入，ATR止损卖出）⭐新增

输出: JSON格式回测报告
"""

import json
import logging
import ssl
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta

logger = logging.getLogger(__name__)

API_BASE = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
_SSL_CONTEXT = ssl.create_default_context()


def fetch_kline(symbol: str, start_date: str, end_date: str, period: str = "daily") -> list:
    """获取历史K线数据"""
    market = "1" if symbol.startswith(("6", "68")) else "0"
    secid = f"{market}.{symbol}"

    url = (
        f"{API_BASE}?"
        f"fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&"
        f"ut=2887a9128e9d96a09a7f33fe1e6097c7&"
        f"secid={secid}&klt=101&fqt=1&"
        f"beg={start_date.replace('-', '')}&end={end_date.replace('-', '')}&"
        f"lmt=500"
    )

    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, context=_SSL_CONTEXT, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        if data.get("data") and data["data"].get("klines"):
            klines = []
            for line in data["data"]["klines"]:
                parts = line.split(",")
                klines.append(
                    {
                        "date": parts[0],
                        "open": float(parts[1]),
                        "close": float(parts[2]),
                        "high": float(parts[3]),
                        "low": float(parts[4]),
                        "volume": float(parts[5]),
                        "amount": float(parts[6]),
                    }
                )
            return klines
    except urllib.error.URLError as e:
        logger.error("fetch_kline_network_error: symbol=%s error=%s", symbol, str(e.reason)[:200])
        return []
    except json.JSONDecodeError as e:
        logger.error("fetch_kline_parse_error: symbol=%s error=%s", symbol, str(e)[:200])
        return []
    except Exception:
        logger.exception("fetch_kline_unexpected_error: symbol=%s", symbol)
        return []
    return []


def calc_ma(data: list, period: int) -> list:
    """计算移动平均线（优化实现：O(n) 滑动窗口累加）"""
    n = len(data)
    if n < period:
        return [None] * n
    result: list = [None] * n
    # 滑动窗口累加 — O(n) 而非 O(n*period)
    window_sum = sum(d["close"] for d in data[:period])
    result[period - 1] = window_sum / period
    for i in range(period, n):
        window_sum += data[i]["close"] - data[i - period]["close"]
        result[i] = window_sum / period
    return result


def calc_highest(data: list, period: int) -> list:
    """计算N日最高价（滑动窗口优化）"""
    from collections import deque

    n = len(data)
    result = [0.0] * n
    # 单调递减队列 — O(n) 复杂度
    dq: deque = deque()  # 存储 (index, high) 对
    for i in range(n):
        h = data[i]["high"]
        # 移除窗口外的元素
        while dq and dq[0][0] < i - period + 1:
            dq.popleft()
        # 移除队尾所有小于当前值的元素
        while dq and dq[-1][1] <= h:
            dq.pop()
        dq.append((i, h))
        result[i] = dq[0][1]
    return result


# ══════════════════════════════════════════════════
#  策略实现
# ══════════════════════════════════════════════════


def backtest_ma_cross(data: list, init_capital: float = 30000):
    """
    均线交叉策略：
    - 5日MA上穿20日MA → 全仓买入
    - 5日MA下穿20日MA → 全部卖出
    """
    ma5 = calc_ma(data, 5)
    ma20 = calc_ma(data, 20)

    capital = init_capital
    shares = 0
    trades = []
    daily_values = []

    for i in range(len(data)):
        if i < 20:  # 需要20天数据才能计算指标
            daily_values.append(capital)
            continue

        signal = None
        prev_diff = (ma5[i - 1] - ma20[i - 1]) if ma5[i - 1] and ma20[i - 1] else 0
        curr_diff = (ma5[i] - ma20[i]) if ma5[i] and ma20[i] else 0

        # 金叉
        if prev_diff <= 0 and curr_diff > 0 and shares == 0:
            price = data[i]["close"]
            buy_shares = int(capital / price / 100) * 100  # A股100股整数倍
            if buy_shares >= 100:  # 至少买100股
                cost = buy_shares * price
                capital -= cost
                shares = buy_shares
                signal = "BUY"
                trades.append(
                    {
                        "date": data[i]["date"],
                        "action": "BUY",
                        "price": round(price, 2),
                        "shares": shares,
                        "cost": round(cost, 2),
                        "reason": "5日MA上穿20日MA(金叉)",
                    }
                )

        # 死叉
        elif prev_diff >= 0 and curr_diff < 0 and shares > 0:
            price = data[i]["close"]
            capital += shares * price
            signal = "SELL"
            trades.append(
                {
                    "date": data[i]["date"],
                    "action": "SELL",
                    "price": round(price, 2),
                    "shares": shares,
                    "proceeds": round(shares * price, 2),
                    "reason": "5日MA下穿20日MA(死叉)",
                }
            )
            shares = 0

        current_value = capital + shares * data[i]["close"]
        daily_values.append(current_value)

    # 以最后收盘价平仓
    if shares > 0:
        capital += shares * data[-1]["close"]

    return _calc_report(data, daily_values, trades, init_capital, capital, "MA均线交叉(5/20)")


def backtest_breakout(data: list, init_capital: float = 30000):
    """
    突破策略：
    - 收盘价突破20日最高价 → 全仓买入
    - 收盘价跌破20日均线 → 全部卖出
    """
    ma20 = calc_ma(data, 20)
    highest20 = calc_highest(data, 20)

    capital = init_capital
    shares = 0
    trades = []
    daily_values = []
    prev_highest = None

    for i in range(len(data)):
        if i < 20:
            daily_values.append(capital)
            prev_highest = highest20[i]
            continue

        signal = None
        price = data[i]["close"]

        # 突破买入
        if shares == 0 and prev_highest and price > prev_highest:
            buy_shares = int(capital / price / 100) * 100
            if buy_shares >= 100:
                cost = buy_shares * price
                capital -= cost
                shares = buy_shares
                signal = "BUY"
                trades.append(
                    {
                        "date": data[i]["date"],
                        "action": "BUY",
                        "price": round(price, 2),
                        "shares": shares,
                        "cost": round(cost, 2),
                        "reason": f"收盘价{price:.2f}突破20日最高价{prev_highest:.2f}",
                    }
                )

        # 破位卖出
        elif shares > 0 and ma20[i] and price < ma20[i]:
            capital += shares * price
            signal = "SELL"
            trades.append(
                {
                    "date": data[i]["date"],
                    "action": "SELL",
                    "price": round(price, 2),
                    "shares": shares,
                    "proceeds": round(shares * price, 2),
                    "reason": f"收盘价{price:.2f}跌破20日MA{ma20[i]:.2f}",
                }
            )
            shares = 0

        prev_highest = highest20[i]
        current_value = capital + shares * data[i]["close"]
        daily_values.append(current_value)

    if shares > 0:
        capital += shares * data[-1]["close"]

    return _calc_report(
        data, daily_values, trades, init_capital, capital, "突破策略(20日最高价/20日MA)"
    )


def _calc_report(data, daily_values, trades, init_capital, final_capital, strategy_name):
    """计算回测报告指标"""
    total_return = (final_capital - init_capital) / init_capital * 100
    total_return_abs = final_capital - init_capital

    # 胜率
    sell_trades = [t for t in trades if t["action"] == "SELL"]
    buy_prices = {}
    win_count = 0
    for t in trades:
        if t["action"] == "BUY":
            buy_prices[t["date"]] = t["price"]
        elif t["action"] == "SELL":
            buy_date = None
            buy_price = None
            # 找最近一次买入
            for bt in reversed(
                [b for b in trades if b["action"] == "BUY" and b["date"] < t["date"]]
            ):
                buy_price = bt["price"]
                break
            if buy_price and t["price"] > buy_price:
                win_count += 1

    win_rate = (win_count / len(sell_trades) * 100) if sell_trades else 0

    # 最大回撤
    peak = init_capital
    max_drawdown = 0
    for v in daily_values:
        peak = max(peak, v)
        dd = (peak - v) / peak * 100
        max_drawdown = max(max_drawdown, dd)

    # 夏普比率简化版（假设无风险利率=0）
    daily_returns = []
    for i in range(1, len(daily_values)):
        if daily_values[i - 1] > 0:
            daily_returns.append((daily_values[i] - daily_values[i - 1]) / daily_values[i - 1])

    avg_daily_ret = sum(daily_returns) / len(daily_returns) if daily_returns else 0
    std_daily_ret = (
        (sum((r - avg_daily_ret) ** 2 for r in daily_returns) / len(daily_returns)) ** 0.5
        if daily_returns
        else 0
    )
    sharpe = (avg_daily_ret / std_daily_ret * (252**0.5)) if std_daily_ret > 0 else 0

    # 年化收益
    trading_days = len(daily_values)
    ann_return = (
        ((1 + total_return / 100) ** (252 / trading_days) - 1) * 100 if trading_days > 0 else 0
    )

    return {
        "ok": True,
        "strategy": strategy_name,
        "period": f"{data[0]['date']} ~ {data[-1]['date']}",
        "trading_days": trading_days,
        "init_capital": init_capital,
        "final_capital": round(final_capital, 2),
        "total_return_pct": round(total_return, 2),
        "total_return_abs": round(total_return_abs, 2),
        "annualized_return_pct": round(ann_return, 2),
        "sharpe_ratio": round(sharpe, 2),
        "max_drawdown_pct": round(max_drawdown, 2),
        "total_trades": len(trades),
        "buy_count": sum(1 for t in trades if t["action"] == "BUY"),
        "sell_count": len(sell_trades),
        "win_count": win_count,
        "loss_count": len(sell_trades) - win_count,
        "win_rate": round(win_rate, 2),
        "trades": trades,
    }


# ══════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════

STRATEGIES = {
    "ma-cross": backtest_ma_cross,
    "breakout": backtest_breakout,
}

# star_signal 策略集成
try:
    import pandas as pd
    from star_signal_adapter import fetch_kline_df, generate_backtest_signals

    def backtest_star_signal(data: list, init_capital: float = 30000):
        """
        star_signal 五角星信号回测策略
        - 买入: signal_strength >= STRONG 时全仓买入
        - 卖出: ATR动态止损 或 信号减弱至NONE/WEAK
        """
        # 转换数据格式
        df = pd.DataFrame(data)
        if "date" not in df.columns:
            return {"ok": False, "error": "数据格式错误"}
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)

        signals = generate_backtest_signals(df, "star_signal")
        buy_list = signals["buy_signals"]
        sell_list = signals["sell_signals"]

        capital = init_capital
        position = 0
        trades = []
        daily_values = []

        for i in range(len(df)):
            row = df.iloc[i]
            date_str = str(df.index[i].date())
            price = row["close"]

            # 处理卖出
            sells_today = [s for s in sell_list if s[0] == i]
            for s in sells_today:
                if position > 0:
                    sell_price = s[1]
                    pnl = (sell_price - trades[-1]["price"]) * position
                    capital += position * sell_price
                    trades[-1]["sell_date"] = date_str
                    trades[-1]["sell_price"] = sell_price
                    trades[-1]["pnl"] = round(pnl, 2)
                    trades[-1]["pnl_pct"] = round((sell_price / trades[-1]["price"] - 1) * 100, 2)
                    trades[-1]["reason"] = s[2] if len(s) > 2 else ""
                    position = 0

            # 处理买入
            buys_today = [b for b in buy_list if b[0] == i]
            for b in buys_today:
                if position == 0 and capital > price * 100:
                    shares = int(capital / price / 100) * 100
                    if shares > 0:
                        position = shares
                        capital -= shares * price
                        trades.append(
                            {
                                "buy_date": date_str,
                                "buy_price": price,
                                "shares": shares,
                                "sell_date": None,
                                "sell_price": None,
                                "pnl": 0,
                                "pnl_pct": 0,
                                "reason": "",
                            }
                        )

            # 日终净值
            total_value = capital + position * price
            daily_values.append({"date": date_str, "value": round(total_value, 2)})

        # 清仓未平仓
        if position > 0 and len(df) > 0:
            last_price = df.iloc[-1]["close"]
            capital += position * last_price
            trades[-1]["sell_date"] = str(df.index[-1].date())
            trades[-1]["sell_price"] = last_price
            trades[-1]["pnl"] = round((last_price - trades[-1]["buy_price"]) * position, 2)
            trades[-1]["pnl_pct"] = round((last_price / trades[-1]["buy_price"] - 1) * 100, 2)
            trades[-1]["reason"] = "回测结束清仓"
            position = 0

        return _calc_report(data, daily_values, trades, init_capital, capital, "star-signal")

    STRATEGIES["star-signal"] = backtest_star_signal
except ImportError:
    import sys

    print("[backtest] star-signal 策略跳过（pandas/star_signal 未安装）", file=sys.stderr)


def main():
    if len(sys.argv) < 3:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "用法: backtest.py <策略> <股票代码> [起始日期] [结束日期]",
                    "strategies": list(STRATEGIES.keys()),
                    "example": "backtest.py ma-cross 600519 2025-01-01 2026-06-01",
                },
                ensure_ascii=False,
            )
        )
        sys.exit(1)

    strategy_name = sys.argv[1]
    symbol = sys.argv[2]
    start_date = (
        sys.argv[3] if len(sys.argv) > 3 else (date.today() - timedelta(days=365)).isoformat()
    )
    end_date = sys.argv[4] if len(sys.argv) > 4 else date.today().isoformat()

    if strategy_name not in STRATEGIES:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"未知策略: {strategy_name}",
                    "available": list(STRATEGIES.keys()),
                }
            )
        )
        sys.exit(1)

    # 获取数据
    data = fetch_kline(symbol, start_date, end_date)
    if not data:
        print(json.dumps({"ok": False, "error": f"无法获取 {symbol} 的历史K线数据"}))
        sys.exit(1)

    # 执行回测
    result = STRATEGIES[strategy_name](data)
    result["symbol"] = symbol

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
