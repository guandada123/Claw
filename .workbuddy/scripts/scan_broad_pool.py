#!/usr/bin/env python3
"""投顾扩大扫描池：扫描主板蓝筹 + 现仓，输出 COMBO 信号

数据：QTS Postgres 历史(>=60天) + 腾讯实时补当日一根
运行：docker exec -e PYTHONPATH=/app quant-strategy python3 /tmp/scan_broad_pool.py
"""
import math
import os
import sys
import urllib.request
from datetime import date

import psycopg2

sys.path.insert(0, "/app")
from services.indicators import calculate_adx, calculate_rsi  # noqa: E402
from services.signals import generate_signals  # noqa: E402

DB_URL = os.environ.get("DATABASE_URL")
# 主板蓝筹池（DB 中有 >=60 天历史的标的）+ 现仓
POOL = {
    "000333.SZ": "美的集团",
    "000858.SZ": "五粮液",
    "600036.SH": "招商银行",
    "600519.SH": "贵州茅台",
    "601318.SH": "中国平安",
    "600900.SH": "长江电力",
    "601899.SH": "紫金矿业",
}
ADX_FILTER = 25
RSI_BLOCK = 80
BUY = 0.2
STRONG = 0.4


def db_hist(ts_code):
    c = psycopg2.connect(DB_URL, connect_timeout=8)
    cur = c.cursor()
    cur.execute(
        "SELECT trade_date,open,high,low,close,volume FROM daily_quote "
        "WHERE ts_code=%s ORDER BY trade_date ASC", (ts_code,))
    rows = [{"trade_date": str(r[0]), "open": float(r[1]), "high": float(r[2]),
             "low": float(r[3]), "close": float(r[4]), "vol": int(r[5])}
            for r in cur.fetchall()]
    c.close()
    return rows


def rt_quote(sym):
    url = f"https://qt.gtimg.cn/q={sym}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0",
                                                "Referer": "https://gu.qq.com/"})
    raw = urllib.request.urlopen(req, timeout=10).read().decode("gbk", "ignore")
    f = raw.split('="')[-1].rstrip('"').split("~")
    return {"trade_date": date.today().isoformat(), "open": float(f[5]),
            "high": float(f[33]), "low": float(f[34]), "close": float(f[3]),
            "vol": int(float(f[6]) * 100)}


def calc(series):
    cl = [r["close"] for r in series]
    hi = [r["high"] for r in series]
    lo = [r["low"] for r in series]
    vwm = generate_signals(series, "vwm", {})
    bbr = generate_signals(series, "bollinger", {})
    combo = [round(0.6 * (vwm[i] if i < len(vwm) else 0) +
                   0.4 * (bbr[i] if i < len(bbr) else 0), 3)
             for i in range(len(series))]
    adx = calculate_adx(hi, lo, cl, 14)[2]
    rsi = calculate_rsi(cl, 14)

    def sl(v):
        v = v[-1] if v else 0.0
        return 0.0 if (isinstance(v, float) and math.isnan(v)) else float(v)

    return (round(sl(combo), 3), int(sl(vwm)), int(sl(bbr)),
            round(sl(adx), 1), round(sl(rsi), 1), cl[-1])


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
    print(f"{'CODE':<10}{'NAME':<8}{'CLOSE':>9}{'COMBO':>8}{'VWM':>5}{'BBR':>5}"
          f"{'ADX':>7}{'RSI':>7}  ACTION")
    print("-" * 82)
    for ts, name in POOL.items():
        sym = ts.replace(".SZ", "").replace(".SH", "").lower()
        sym = ("sz" + sym) if ts.endswith("SZ") else ("sh" + sym)
        try:
            h = db_hist(ts)
            r = rt_quote(sym)
            if h and h[-1]["trade_date"] == r["trade_date"]:
                h[-1] = r
            else:
                h.append(r)
            if len(h) < 40:
                print(f"{ts:<10}{name:<8}  DATA_SHORT({len(h)})")
                continue
            combo, vwm, bbr, adx, rsi, close = calc(h)
            act = decide(combo, adx, rsi)
            print(f"{ts:<10}{name:<8}{close:>9.2f}{combo:>8.2f}{vwm:>5}{bbr:>5}"
                  f"{adx:>7.1f}{rsi:>7.1f}  {act}")
        except Exception as e:
            print(f"{ts:<10}{name:<8}  ERR {e}")


if __name__ == "__main__":
    main()
