#!/usr/bin/env python3
"""扩大池(科技+红利38只) COMBO 扫描 - 容器内运行
复用 QTS signals/indicators；数据来自 expanded_pool_klines.json
运行: docker exec -e PYTHONPATH=/app quant-strategy python3 /tmp/scan_expanded_pool.py
"""
import json
import sys

sys.path.insert(0, "/app")
import math

from services.indicators import calculate_adx, calculate_rsi
from services.signals import generate_signals

ADX_FILTER, RSI_BLOCK, BUY, STRONG = 25, 80, 0.2, 0.4


def calc(series):
    cl = [r["close"] for r in series]
    hi = [r["high"] for r in series]
    lo = [r["low"] for r in series]
    vwm = generate_signals(series, "vwm", {})
    bbr = generate_signals(series, "bollinger", {})
    combo = [
        round(0.6 * (vwm[i] if i < len(vwm) else 0) + 0.4 * (bbr[i] if i < len(bbr) else 0), 3)
        for i in range(len(series))
    ]
    adx = calculate_adx(hi, lo, cl, 14)[2]
    rsi = calculate_rsi(cl, 14)

    def sl(v):
        v = v[-1] if v else 0.0
        return 0.0 if (isinstance(v, float) and math.isnan(v)) else float(v)

    return round(sl(combo), 3), int(sl(vwm)), int(sl(bbr)), round(sl(adx), 1), round(sl(rsi), 1), cl[-1]


def decide(combo, adx, rsi):
    if combo >= STRONG and adx >= ADX_FILTER and rsi <= RSI_BLOCK:
        return "STRONG_BUY"
    if combo >= BUY and adx >= ADX_FILTER and rsi <= RSI_BLOCK:
        return "BUY"
    if combo <= -BUY:
        return "SELL"
    if adx < ADX_FILTER:
        return "HOLD_ADX_WEAK"
    if rsi > RSI_BLOCK:
        return "HOLD_OVERBOUGHT"
    return "HOLD"


def main():
    with open("/tmp/expanded_pool_klines.json") as f:
        data = json.load(f)
    pool = data["pool"]
    klines = data["klines"]
    rows = []
    for sym, name in pool.items():
        s = klines[sym]
        if len(s) < 40:
            print(f"{sym} {name}: DATA_SHORT({len(s)})")
            continue
        combo, vwm, bbr, adx, rsi, close = calc(s)
        act = decide(combo, adx, rsi)
        rows.append((sym, name, close, combo, vwm, bbr, adx, rsi, act))
    # 排序：先按动作强度，再按combo
    order = {"STRONG_BUY": 0, "BUY": 1, "HOLD": 2, "HOLD_ADX_WEAK": 3, "HOLD_OVERBOUGHT": 4, "SELL": 5}
    rows.sort(key=lambda r: (order.get(r[8], 9), -r[3]))
    print(f"{'CODE':<9}{'NAME':<8}{'CLOSE':>9}{'COMBO':>8}{'VWM':>5}{'BBR':>5}{'ADX':>7}{'RSI':>7}  ACTION")
    print("-" * 82)
    for r in rows:
        print(f"{r[0]:<9}{r[1]:<8}{r[2]:>9.2f}{r[3]:>8.2f}{r[4]:>5}{r[5]:>5}{r[6]:>7.1f}{r[7]:>7.1f}  {r[8]}")
    # 买入候选汇总
    buys = [r for r in rows if r[8] in ("BUY", "STRONG_BUY")]
    print("\n=== 买入候选 ===")
    if buys:
        for r in buys:
            print(f"  {r[0]} {r[1]} @ {r[2]:.2f} | COMBO={r[3]:.2f} VWM={r[4]} BBR={r[5]} ADX={r[6]} RSI={r[7]} [{r[8]}]")
    else:
        print("  无 (全池 COMBO<0.2 或 ADX<25 或 RSI>80)")


if __name__ == "__main__":
    main()
