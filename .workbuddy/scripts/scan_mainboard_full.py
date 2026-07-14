#!/usr/bin/env python3
"""全主板 COMBO 选股扫描 (助理新建仓候选生成)
运行: docker exec -e PYTHONPATH=/app quant-strategy python3 /tmp/scan_mainboard_full.py
依赖: QTS daily_quote(全市场日线) + mainboard_scan_pool.json(流动性+ST过滤池)
输出: 买入候选(代码+名称+价位+仓位+止损+周期+风险) 完整建议格式
"""
import json
import sys

sys.path.insert(0, "/app")
from services.indicators import calculate_adx, calculate_rsi
from services.signals import generate_signals

# ---- 规则参数 (v2.0 体系, 贴合 USER.md: 中等风险/中短线/主板) ----
ADX_FILTER = 25          # ADX>=25 才认为有趋势
RSI_BLOCK = 80           # RSI(14)>80 超买拦截
COMBO_BUY = 0.2          # 买入阈值
COMBO_STRONG = 0.4       # 强买阈值
MIN_BARS = 30            # 最少K线数(数据中位40天, 放宽到30)
LOOKBACK = 60            # 取近60日

# 用户实盘规模
USER_CAPITAL = 15000
MAX_SINGLE = USER_CAPITAL / 3   # 单只<=1/3 ≈ 5000
STOP_LOSS = 0.08                 # 止损8%

import math


def calc_combo(series):
    # series: [(high,low,close,td)] -> 转 dict 列表供 QTS 指标消费
    dicts = [{"close": r[2], "high": r[0], "low": r[1], "volume": 0, "open": r[2]} for r in series]
    cl = [r[2] for r in series]
    hi = [r[0] for r in series]
    lo = [r[1] for r in series]
    vwm = generate_signals(dicts, "vwm", {})
    bbr = generate_signals(dicts, "bollinger", {})
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
    if combo >= COMBO_STRONG and adx >= ADX_FILTER and rsi <= RSI_BLOCK:
        return "STRONG_BUY"
    if combo >= COMBO_BUY and adx >= ADX_FILTER and rsi <= RSI_BLOCK:
        return "BUY"
    if combo <= -COMBO_BUY:
        return "SELL"
    if adx < ADX_FILTER:
        return "HOLD_ADX_WEAK"
    if rsi > RSI_BLOCK:
        return "HOLD_OVERBOUGHT"
    return "HOLD"


def main():
    from collections import defaultdict

    import psycopg2

    with open("/tmp/mainboard_scan_pool.json") as f:
        pool = json.load(f)  # ts_code -> {avg_amt,name}
    codes = list(pool.keys())
    # 容器内本地数据库连接, 非用户输入, 无注入风险 (S106/S608 已知安全)
    conn = psycopg2.connect(
        host="quant-postgres",
        port=5432,
        dbname="quant_trading",
        user="quant_user",
        password="quant_pass",  # noqa: S106
    )
    cur = conn.cursor()
    # 批量取近60日K线: (high,low,close,ts_code,trade_date)
    # 参数化查询, 消除 SQL 注入风险 (codes 来自内部 mainboard_scan_pool.json, 非用户输入)
    placeholders = ",".join(["%s"] * len(codes))
    sql = (
        "SELECT ts_code, high, low, close, trade_date FROM daily_quote "
        "WHERE ts_code IN (" + placeholders + ") "
        "AND trade_date >= (SELECT MAX(trade_date) FROM daily_quote) - INTERVAL '75 day' "
        "ORDER BY ts_code, trade_date"
    )
    cur.execute(sql, tuple(codes))
    raw = cur.fetchall()
    # 分组
    bars = defaultdict(list)
    for ts_code, high, low, close, td in raw:
        bars[ts_code].append((float(high), float(low), float(close), td))
    rows = []
    skipped = 0
    for code in codes:
        s = bars.get(code, [])
        if len(s) < MIN_BARS:
            skipped += 1
            continue
        s = s[-LOOKBACK:]
        combo, vwm, bbr, adx, rsi, close = calc_combo(s)
        act = decide(combo, adx, rsi)
        rows.append((code, pool[code]["name"], close, combo, vwm, bbr, adx, rsi, act))
    print(f"[DEBUG] 池 {len(codes)} 只; 有效 {len(rows)} 只; 跳过(数据不足) {skipped} 只")

    order = {"STRONG_BUY": 0, "BUY": 1, "HOLD": 2, "HOLD_ADX_WEAK": 3, "HOLD_OVERBOUGHT": 4, "SELL": 5}
    rows.sort(key=lambda r: (order.get(r[8], 9), -r[3]))

    print(f"{'CODE':<10}{'NAME':<10}{'CLOSE':>9}{'COMBO':>8}{'VWM':>5}{'BBR':>5}{'ADX':>7}{'RSI':>7}  ACTION")
    print("-" * 90)
    for r in rows[:60]:
        print(f"{r[0]:<10}{r[1]:<10}{r[2]:>9.2f}{r[3]:>8.2f}{r[4]:>5}{r[5]:>5}{r[6]:>7.1f}{r[7]:>7.1f}  {r[8]}")

    buys = [r for r in rows if r[8] in ("BUY", "STRONG_BUY")]
    print(f"\n=== 买入候选 (COMBO>={COMBO_BUY} & ADX>={ADX_FILTER} & RSI<={RSI_BLOCK}) ===")
    print(f"全主板扫描 {len(rows)} 只有效; 买入候选 {len(buys)} 只\n")
    for r in buys:
        code, name, close = r[0], r[1], r[2]
        # 仓位建议: 单只<=5000, 整百股
        price = close
        max_shares = int(MAX_SINGLE // price // 100 * 100)
        if max_shares < 100:
            max_shares = 0
        stop_px = round(price * (1 - STOP_LOSS), 2)
        lots = max_shares // 100
        cost = max_shares * price
        print(f"■ {code} {name}")
        print(f"  现价 {price:.2f} | COMBO {r[3]:.2f}(VWM{r[4]}/BBR{r[5]}) ADX {r[6]} RSI {r[7]} [{r[8]}]")
        if lots > 0:
            print(f"  建议: 买 {lots} 手({max_shares}股) ≈ ¥{cost:,.0f} | 止损价 {stop_px} (-8%) | 周期 3-10天")
            print(f"  风险: 中等; 单只仓位 {cost/USER_CAPITAL*100:.0f}% ≤ 33%上限; 跌破止损立即走")
        else:
            print(f"  建议: 现价 {price:.2f} 超单只上限(¥{MAX_SINGLE:,.0f}), 不推")
        print()

    # 写出候选JSON供飞书推送
    cand = [
        {"code": r[0], "name": r[1], "close": r[2], "combo": r[3], "vwm": r[4], "bbr": r[5],
         "adx": r[6], "rsi": r[7], "action": r[8]}
        for r in buys
    ]
    with open("/tmp/mainboard_scan_result.json", "w") as f:
        json.dump({"scan_date": "2026-07-13", "total": len(rows), "buys": cand}, f, ensure_ascii=False)
    print("候选结果已写 /tmp/mainboard_scan_result.json")


if __name__ == "__main__":
    main()
