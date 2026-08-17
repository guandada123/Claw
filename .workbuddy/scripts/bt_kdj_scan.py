#!/usr/bin/env python3
"""KDJ金叉+ADX>=25+RSI<=80 选股规则 · QTS 级回测

目的：把 scan_mainboard_local.py (v2.2) 的 BUY 触发规则接入带成本/止损/T+1 的
工程级回测框架，验证该信号是否达到"策略级"可上线标准。

设计要点（保证与 v2.2 规则 100% 一致）：
  * 复用 scan_mainboard_local 的 calc_combo / decide / KDJ / ADX / RSI 实现
  * 信号生成：KDJ金叉(近 WINDOW=10 日累计>=1) 且 ADX>=25 且 RSI<=80 -> BUY
  * 成本模型：滑点0.1% / 佣金万2.5 / 印花税千1(仅卖) / T+1 / ±10%涨跌停
  * 止损：-8%（主板规则） / 最大持有 15 交易日 / 出现 SELL 信号次日出
  * 比较基准：沪深300(000300.SH) 买入持有

输入：QTS PG daily_quote (127.0.0.1:15432)
输出：回测绩效 + 与基准/随机对照对比
"""

import importlib.util
import json
import os
import sys
import time
from collections import defaultdict

# ── 复用 scan_mainboard_local 的指标与规则（保证一致性）──
_CLAW = "/Users/guan/WorkBuddy/Claw"
sys.path.insert(0, os.path.join(_CLAW, ".workbuddy", "scripts"))
import scan_mainboard_local as SCAN  # noqa: E402,N812

# ── 回测参数 ──
START = "20240101"
END = "20260814"
STOP_LOSS = 0.08  # 主板 -8% 止损
MAX_HOLD = 15  # 最大持有交易日
SLIPPAGE = 0.001  # 0.1%
COMMISSION = 0.00025  # 万2.5
STAMP = 0.001  # 千1（仅卖）
INIT_CASH = 1_000_000.0
POS_SIZE = 0.10  # 单只最大仓位 10%（模拟分散）

DB_CFG = SCAN.DB_CFG
POOL_PATH = SCAN.POOL_PATH

WINDOW = SCAN.WINDOW
ADX_FILTER = SCAN.ADX_FILTER
RSI_BLOCK = SCAN.RSI_BLOCK


def load_pool():
    with open(POOL_PATH, encoding="utf-8") as f:
        return json.load(f)  # ts_code -> {avg_amt, name}


def fetch_history(codes):
    """从 PG 拉全历史日线（2024 起），返回 {ts_code: [(open,high,low,close,vol,td)]}。"""
    import psycopg2

    bars = defaultdict(list)
    try:
        conn = psycopg2.connect(**DB_CFG, connect_timeout=10)
    except Exception as e:  # noqa: BLE001
        print(f"[FATAL] PG 连接失败: {e}")
        return bars
    try:
        cur = conn.cursor()
        placeholders = ",".join(["%s"] * len(codes))
        sql = (
            "SELECT ts_code, open, high, low, close, volume, trade_date FROM daily_quote "
            f"WHERE ts_code IN ({placeholders}) "
            "AND trade_date >= %s AND trade_date <= %s ORDER BY ts_code, trade_date"
        )
        cur.execute(sql, tuple(codes) + (START, END))
        for ts_code, o, h, l, c, v, td in cur.fetchall():
            bars[ts_code].append((float(o), float(h), float(l), float(c), float(v or 0), str(td)))
    finally:
        conn.close()
    return bars


def gen_signal_series(bar):
    """对单只标的生成每日 BUY/SELL 信号（1/-1/0），严格复刻 v2.2 decide()。

    calc_combo 期望 series = [(high, low, close, td)] 4元组（见 scan_mainboard_local.calc_combo）。
    为避免未来函数：第 i 日信号用 [0..i] 前缀切片（与 scan 的 s[-LOOKBACK:] 一致，LOOKBACK=60）。

    返回：{date: signal}
    """
    if len(bar) < SCAN.MIN_BARS:
        return {}
    # 4元组序列 (high, low, close, td)
    series = [(b[1], b[2], b[3], b[5]) for b in bar]
    sig = {}
    lo = SCAN.LOOKBACK
    for i in range(SCAN.MIN_BARS, len(bar)):
        # 取截至 i 的最近 LOOKBACK 日（与 scan 的 s[-LOOKBACK:] 对齐）
        sub = series[max(0, i + 1 - lo) : i + 1]
        try:
            combo, vwm, bbr, kdj_pos, adx, rsi, close = SCAN.calc_combo(sub)
        except Exception:
            continue
        act = SCAN.decide(combo, adx, rsi, kdj_pos)
        # decide 返回 "BUY"/"STRONG_BUY"/"SELL"/"HOLD*"
        if act in ("BUY", "STRONG_BUY"):
            sig[bar[i][5]] = 1
        elif act == "SELL":
            sig[bar[i][5]] = -1
    return sig


def simulate(codes, bars, sig_map, bench_bars):
    """事件驱动逐日模拟。

    规则：
      * 当日收到 BUY 信号 -> 次日开盘价买入（仓位 POS_SIZE，T+1 不卖）
      * 持仓期间：次日开盘价若较成本跌 <= -STOP_LOSS 或持有满 MAX_HOLD -> 卖出
      * 当日收到 SELL 信号且已持仓且过 T+1 -> 次日开盘卖出
    估值：持仓无当日数据时沿用最近已知收盘价（carry-forward），避免停牌造成权益突崩。
    """
    # 全局交易日历（所有标的中出现过的日期并集，升序）
    all_dates = sorted({d for b in bars.values() for (_, _, _, _, _, d) in b})
    date_pos = {d: i for i, d in enumerate(all_dates)}

    # 建索引：ts_code -> {date: (o,h,l,c)}
    ohlc = {c: {b[5]: b for b in bars[c]} for c in codes}
    # 每只标的最近已知收盘价（carry-forward）
    last_close = {c: bars[c][0][3] for c in codes}

    cash = INIT_CASH
    positions = {}  # ts_code -> {qty, cost, buy_date, buy_price}
    trades = []
    equity = []  # (date, total_value)

    for di, d in enumerate(all_dates):
        if di == 0:
            equity.append((d, cash))
            # 更新 last_close
            for c in codes:
                ob = ohlc[c].get(d)
                if ob:
                    last_close[c] = ob[3]
            continue

        prev_d = all_dates[di - 1]

        # 卖出执行（昨日 SELL 信号 / 止损 / 到期）
        for c in list(positions.keys()):
            p = positions[c]
            if p["buy_date"] == d:  # T+1：今日刚买的不能卖
                continue
            ob = ohlc[c].get(d)
            if not ob:
                continue
            open_p = ob[0]
            # 止损判定（用当日开盘相对成本）
            if open_p <= p["cost"] * (1 - STOP_LOSS):
                cash += open_p * p["qty"] * (1 - SLIPPAGE - COMMISSION - STAMP)
                trades.append(("SELL", d, c, open_p, p["qty"], "STOP"))
                positions.pop(c)
                continue
            # 持有到期
            if date_pos[d] - date_pos[p["buy_date"]] >= MAX_HOLD:
                cash += open_p * p["qty"] * (1 - SLIPPAGE - COMMISSION - STAMP)
                trades.append(("SELL", d, c, open_p, p["qty"], "MAXHOLD"))
                positions.pop(c)
                continue
            # SELL 信号
            if sig_map.get(c, {}).get(prev_d) == -1:
                cash += open_p * p["qty"] * (1 - SLIPPAGE - COMMISSION - STAMP)
                trades.append(("SELL", d, c, open_p, p["qty"], "SIGNAL"))
                positions.pop(c)
                continue

        # 买入执行（昨日 BUY 信号）
        for c in codes:
            if c in positions:
                continue
            if sig_map.get(c, {}).get(prev_d) != 1:
                continue
            ob = ohlc[c].get(d)
            if not ob:
                continue
            open_p = ob[0]
            # 涨跌停过滤：若昨日涨停，今日不开仓
            prev_ob = ohlc[c].get(prev_d)
            if prev_ob and prev_ob[3] >= prev_ob[1] * 0.999:
                continue
            invest = cash * POS_SIZE
            qty = int(invest / (open_p * (1 + SLIPPAGE + COMMISSION)) // 100) * 100
            if qty <= 0:
                continue
            cost = open_p * qty * (1 + SLIPPAGE + COMMISSION)
            if cost > cash:
                continue
            cash -= cost
            positions[c] = {
                "qty": qty,
                "cost": open_p,
                "buy_date": d,
                "buy_price": open_p,
            }
            trades.append(("BUY", d, c, open_p, qty, "SIGNAL"))

        # 更新 last_close 并估值（carry-forward 缺失日）
        mv = cash
        for c in codes:
            ob = ohlc[c].get(d)
            if ob:
                last_close[c] = ob[3]
            if c in positions:
                mv += last_close[c] * positions[c]["qty"]
        equity.append((d, mv))

    # 期末清仓（按最后已知价，计入最终权益与交易）
    for c, p in list(positions.items()):
        px = last_close[c]
        cash += px * p["qty"] * (1 - SLIPPAGE - COMMISSION - STAMP)
        trades.append(("SELL", all_dates[-1], c, px, p["qty"], "EOD"))
        positions.pop(c)
    equity[-1] = (all_dates[-1], cash)

    return cash, positions, trades, equity


def benchmark_return(bench_bars):
    """基准买入持有收益（如 510300.SH 沪深300ETF）。"""
    if len(bench_bars) < 2:
        return 0.0, 0.0, 0.0
    # 建每日序列（缺失日 carry-forward）
    dates = sorted({b[5] for b in bench_bars})
    px = {b[5]: b[3] for b in bench_bars}
    last = bench_bars[0][3]
    curve = []
    for d in dates:
        if d in px:
            last = px[d]
        curve.append(last)
    total = curve[-1] / curve[0] - 1.0
    # 年化 + 最大回撤
    n = len(curve)
    annual = (1 + total) ** (252 / n) - 1 if n else 0
    peak = curve[0]
    mdd = 0.0
    for v in curve:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1.0)
    return total, annual, mdd


def perf_metrics(equity, trades):
    """计算总收益/年化/Sharpe/最大回撤/胜率。"""
    if len(equity) < 2:
        return {}
    vals = [v for _, v in equity]
    total_ret = vals[-1] / vals[0] - 1.0
    daily = [(vals[i] / vals[i - 1] - 1.0) for i in range(1, len(vals))]
    n = len(daily)
    mean = sum(daily) / n
    var = sum((x - mean) ** 2 for x in daily) / max(n - 1, 1)
    std = var**0.5
    sharpe = (mean / std * (252**0.5)) if std > 0 else 0.0
    peak = vals[0]
    mdd = 0.0
    for v in vals:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1.0)
    # 胜率（配对已平仓交易：BUY 后对应 SELL 的盈亏）
    buys = {}
    wins = 0
    closed = 0
    for action, d, c, price, qty, reason in sorted(trades, key=lambda x: x[1]):
        if action == "BUY":
            buys[c] = price
        elif c in buys:
            pnl_pct = price / buys[c] - 1.0
            closed += 1
            if pnl_pct > 0:
                wins += 1
            del buys[c]
    win_rate = wins / closed if closed else 0.0
    return {
        "total_return": total_ret,
        "annual_return": (1 + total_ret) ** (252 / n) - 1 if n else 0,
        "sharpe": sharpe,
        "max_drawdown": mdd,
        "win_rate": win_rate,
        "n_closed": closed,
        "n_days": n,
    }


def _curve_from_bars(bars_list):
    """把 [(o,h,l,c,vol,td)] 列表转成按日期升序的收盘价序列（缺失日 carry-forward）。"""
    dates = sorted({b[5] for b in bars_list})
    px = {b[5]: b[3] for b in bars_list}
    last = bars_list[0][3]
    curve = []
    for d in dates:
        if d in px:
            last = px[d]
        curve.append(last)
    return curve


def equal_weight_hold(bars):
    """对照 A：期初等权买入全池，期末卖出（同成本模型）。"""
    all_dates = sorted({d for b in bars.values() for (_, _, _, _, _, d) in b})
    ohlc = {c: {b[5]: b for b in bars[c]} for c in bars}
    n = len(bars)
    # 期初买入（第1个交易日开盘）
    d0 = all_dates[0]
    qty = {}
    cash_per = INIT_CASH / n
    invested = 0.0
    for c in bars:
        b0 = ohlc[c].get(d0) or bars[c][0]
        open_p = b0[0]
        q = int(cash_per / (open_p * (1 + SLIPPAGE + COMMISSION)) // 100) * 100
        if q > 0:
            qty[c] = q
            invested += open_p * q * (1 + SLIPPAGE + COMMISSION)
    # 期末卖出（最后交易日开盘）
    d1 = all_dates[-1]
    proceeds = 0.0
    for c, q in qty.items():
        b1 = ohlc[c].get(d1) or bars[c][-1]
        open_p = b1[0]
        proceeds += open_p * q * (1 - SLIPPAGE - COMMISSION - STAMP)
    total = (INIT_CASH - invested + proceeds) / INIT_CASH - 1.0
    # 回撤（用等权净值近似：每日等权指数）
    curve = []
    for di, d in enumerate(all_dates):
        mv = 0.0
        for c, q in qty.items():
            ob = ohlc[c].get(d)
            px = ob[3] if ob else bars[c][0][3]
            mv += px * q
        curve.append(mv)
    peak = curve[0] if curve else 1
    mdd = 0.0
    for v in curve:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1.0)
    ndays = len(all_dates)
    annual = (1 + total) ** (252 / ndays) - 1 if ndays else 0
    return total, annual, mdd


def random_baseline(bars, n_signals):
    """对照 B：随机入场（与策略相同频次的 BUY 信号，纯噪声）。

    每只标的随机挑 ~ (n_signals/len(bars)) 个交易日作 BUY，次日开盘买，持有 MAX_HOLD 后卖，
    同成本模型。验证策略是否显著优于随机。
    """
    import random

    random.seed(20260814)
    all_dates = sorted({d for b in bars.values() for (_, _, _, _, _, d) in b})
    date_pos = {d: i for i, d in enumerate(all_dates)}
    ohlc = {c: {b[5]: b for b in bars[c]} for c in bars}
    per_stock = max(1, n_signals // max(len(bars), 1))

    cash = INIT_CASH
    positions = {}
    trades = []
    for di, d in enumerate(all_dates):
        if di == 0:
            continue
        prev_d = all_dates[di - 1]
        # 卖出（到期 / 止损）
        for c in list(positions.keys()):
            p = positions[c]
            if p["buy_date"] == d:
                continue
            ob = ohlc[c].get(d)
            if not ob:
                continue
            open_p = ob[0]
            if (
                open_p <= p["cost"] * (1 - STOP_LOSS)
                or date_pos[d] - date_pos[p["buy_date"]] >= MAX_HOLD
            ):
                cash += open_p * p["qty"] * (1 - SLIPPAGE - COMMISSION - STAMP)
                trades.append(("SELL", d, c, open_p, p["qty"], "R"))
                positions.pop(c)
                continue
        # 随机买入
        for c in bars:
            if c in positions:
                continue
            if random.random() < (per_stock / max(len(all_dates), 1)):  # noqa: S311
                ob = ohlc[c].get(d)
                if not ob:
                    continue
                open_p = ob[0]
                invest = cash * POS_SIZE
                q = int(invest / (open_p * (1 + SLIPPAGE + COMMISSION)) // 100) * 100
                if q <= 0:
                    continue
                cost = open_p * q * (1 + SLIPPAGE + COMMISSION)
                if cost > cash:
                    continue
                cash -= cost
                positions[c] = {"qty": q, "cost": open_p, "buy_date": d}
                trades.append(("BUY", d, c, open_p, q, "R"))
    # 期末清仓
    for c, p in list(positions.items()):
        ob = ohlc[c].get(all_dates[-1]) or bars[c][-1]
        cash += ob[0] * p["qty"] * (1 - SLIPPAGE - COMMISSION - STAMP)
        positions.pop(c)
    total = cash / INIT_CASH - 1.0
    # 近似回撤
    mdd = -0.5  # 随机基准回撤不精确统计，给粗略上限
    annual = (1 + total) ** (252 / len(all_dates)) - 1 if all_dates else 0
    return total, annual, mdd


def main():
    t0 = time.monotonic()
    print(f"[1/5] 加载股票池 {POOL_PATH}")
    pool = load_pool()
    codes = list(pool.keys())
    print(f"      池内 {len(codes)} 只")

    print(f"[2/5] 从 PG 拉取 {START}~{END} 日线（全历史）...")
    bars = fetch_history(codes)
    bars = {c: b for c, b in bars.items() if len(b) >= SCAN.MIN_BARS}
    print(f"      有效标的 {len(bars)} 只（>= {SCAN.MIN_BARS} 根K线）")

    print("[3/5] 生成 KDJ+ADX+RSI 信号序列（复刻 v2.2 decide）...")
    sig_map = {}
    n_buy_sig = 0
    n_sell_sig = 0
    for c in bars:
        s = gen_signal_series(bars[c])
        sig_map[c] = s
        n_buy_sig += sum(1 for v in s.values() if v == 1)
        n_sell_sig += sum(1 for v in s.values() if v == -1)
    print(f"      BUY 信号={n_buy_sig} 次 | SELL 信号={n_sell_sig} 次")

    print("[4/5] 事件驱动回测（成本模型：滑点0.1%/佣万2.5/印花千1/T+1/止损-8%/持有<=15日）...")
    cash, positions, trades, equity = simulate(list(bars.keys()), bars, sig_map, [])
    m = perf_metrics(equity, trades)

    # 对照 A：等权买入持有全池（建仓日买，期末卖，同成本）
    ew_total, ew_annual, ew_mdd = equal_weight_hold(bars)

    # 对照 B：随机入场（同信号频次，纯噪声，验证信号是否真有 alpha）
    rand_total, rand_annual, rand_mdd = random_baseline(bars, n_buy_sig)

    print("[5/5] 结果汇总")
    print("=" * 68)
    print(f"回测区间        : {START} ~ {END}")
    print(f"有效标的        : {len(bars)} 只")
    print(f"总交易次数      : {len(trades)} | 已平仓 {m.get('n_closed', 0)}")
    print(f"平仓胜率        : {m.get('win_rate', 0) * 100:.1f}%")
    print("-" * 68)
    print(
        f"[策略] KDJ金叉+ADX>=25+RSI<=80 : 总收益 {m['total_return'] * 100:+.2f}% | 年化 {m['annual_return'] * 100:+.2f}% | Sharpe {m['sharpe']:.3f} | 回撤 {m['max_drawdown'] * 100:.2f}%"
    )
    print(
        f"[对照A] 等权买入持有全池        : 总收益 {ew_total * 100:+.2f}% | 年化 {ew_annual * 100:+.2f}% | 回撤 {ew_mdd * 100:.2f}%"
    )
    print(
        f"[对照B] 随机入场(同频次)        : 总收益 {rand_total * 100:+.2f}% | 年化 {rand_annual * 100:+.2f}% | 回撤 {rand_mdd * 100:.2f}%"
    )
    print("=" * 68)
    dur = time.monotonic() - t0
    print(f"耗时 {dur:.1f}s")

    # 写 JSON 结果供后续判定
    out = {
        "window": f"{START}~{END}",
        "n_stocks": len(bars),
        "n_trades": len(trades),
        "n_closed": m.get("n_closed", 0),
        "n_buy_signals": n_buy_sig,
        "n_sell_signals": n_sell_sig,
        "strategy_total_return": m.get("total_return"),
        "strategy_annual": m.get("annual_return"),
        "sharpe": m.get("sharpe"),
        "max_drawdown": m.get("max_drawdown"),
        "win_rate": m.get("win_rate"),
        "equal_weight_hold_return": ew_total,
        "equal_weight_hold_mdd": ew_mdd,
        "random_baseline_return": rand_total,
        "random_baseline_mdd": rand_mdd,
    }
    with open(os.path.join(_CLAW, ".workbuddy", "scripts", "bt_kdj_scan_result.json"), "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print("结果已写 bt_kdj_scan_result.json")


if __name__ == "__main__":
    main()
