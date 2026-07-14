#!/usr/bin/env python3
"""
投顾本地 COMBO 信号计算（复用 QTS strategy-service 成熟指标）

数据来源：westockdata MCP 已拉取的 K 线 JSON（本脚本直接读取缓存）
计算：VWM + BBR = COMBO(0.6/0.4)，ADX 趋势过滤，RSI 超买保护
输出：每只标的的 COMBO 信号、ADX、RSI、建议动作

用法:
  python3 local_combo_signal.py <kline_json_file>
  python3 local_combo_signal.py   # 默认读 /tmp/kline_cache.json
"""
import json
import math
import sys
from pathlib import Path

# ── 复用 QTS 指标（包路径导入）──
QTS_SERVICES = "/Users/guan/WorkBuddy/QuantTradingSystem/strategy-service"
sys.path.insert(0, QTS_SERVICES)
from services.indicators import calculate_adx, calculate_rsi  # noqa: E402
from services.signals import generate_signals  # noqa: E402

# ── 投顾规则常量（对齐 STRATEGY.md v2.0）──
ADX_TREND_FILTER = 25       # ADX >= 25 才允许建仓
RSI_OVERBOUGHT = 80         # RSI(14) > 80 禁止追入
BUY_THRESHOLD = 0.2         # COMBO 综合得分买入阈值
STRONG_BUY = 0.4

NAME_MAP = {"sz000333": "美的集团", "sh600900": "长江电力", "sh601899": "紫金矿业"}


def load_kline(path: str) -> dict:
    return json.loads(Path(path).read_text())


def to_merged_series(nodes: list[dict]) -> list[dict]:
    """westock nodes -> QTS 期望的 {close,high,low,vol} 序列（按时间升序）"""
    rows = []
    for n in sorted(nodes, key=lambda x: x["date"]):
        rows.append({
            "trade_date": n["date"],
            "close": float(n["last"]),
            "high": float(n["high"]),
            "low": float(n["low"]),
            "vol": int(n.get("volume", 0)),
        })
    return rows


def compute_combo(series: list[dict]) -> dict:
    closes = [r["close"] for r in series]
    highs = [r["high"] for r in series]
    lows = [r["low"] for r in series]
    vols = [r["vol"] for r in series]

    vwm = generate_signals(series, "vwm", {})
    bbr = generate_signals(series, "bollinger", {})
    # COMBO 加权
    n = len(series)
    combo = [0] * n
    for i in range(n):
        s1 = vwm[i] if i < len(vwm) else 0
        s2 = bbr[i] if i < len(bbr) else 0
        combo[i] = round(0.6 * s1 + 0.4 * s2, 3)

    adx_tuple = calculate_adx(highs, lows, closes, 14)
    adx = adx_tuple[2] if isinstance(adx_tuple, tuple) else adx_tuple
    rsi = calculate_rsi(closes, 14)

    def _safe_last(vals):
        if not vals:
            return 0.0
        v = vals[-1]
        if isinstance(v, float) and math.isnan(v):  # NaN
            return 0.0
        return float(v)

    return {
        "combo_last": round(_safe_last(combo), 3),
        "vwm_last": int(_safe_last(vwm)),
        "bbr_last": int(_safe_last(bbr)),
        "adx_last": round(_safe_last(adx), 1),
        "rsi_last": round(_safe_last(rsi), 1),
        "close_last": closes[-1],
        "date_last": series[-1]["trade_date"],
    }


def decide(sig: dict) -> str:
    """套用投顾新规则给出动作"""
    adx_ok = sig["adx_last"] >= ADX_TREND_FILTER
    rsi_ob = sig["rsi_last"] > RSI_OVERBOUGHT
    combo = sig["combo_last"]

    if combo >= STRONG_BUY and adx_ok and not rsi_ob:
        return "🔥 强买"
    if combo >= BUY_THRESHOLD and adx_ok and not rsi_ob:
        return "✅ 买入"
    if combo <= -BUY_THRESHOLD:
        return "�红灯 卖出"
    if not adx_ok:
        return "⏸ 持有(ADX弱)"
    if rsi_ob:
        return "⏸ 持有(超买)"
    return "⏸ 持有"


def fetch_db_history(ts_code: str) -> list[dict]:
    """从 QTS Postgres 读历史日K（到 6/17 左右），需容器网络可用"""
    import os

    import psycopg2
    url = os.environ.get("QTS_DB_URL",
                         "postgresql://quant_user:quant_pass@quant-postgres:5432/quant_trading")
    try:
        c = psycopg2.connect(url, connect_timeout=5)
        cur = c.cursor()
        cur.execute(
            "SELECT trade_date, open, high, low, close, volume FROM daily_quote "
            "WHERE ts_code=%s ORDER BY trade_date ASC LIMIT 120", (ts_code,))
        rows = [{"trade_date": str(r[0]), "open": float(r[1]), "high": float(r[2]),
                 "low": float(r[3]), "close": float(r[4]), "vol": int(r[5])}
                for r in cur.fetchall()]
        c.close()
        return rows
    except Exception as e:
        print(f"  ⚠️ DB历史读取失败 {ts_code}: {e}")
        return []


def fetch_realtime(symbol: str) -> dict | None:
    """腾讯实时行情补最新一根（qt.gtimg.cn）"""
    import urllib.request
    url = f"https://qt.gtimg.cn/q={symbol}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0",
                                                    "Referer": "https://gu.qq.com/"})
        raw = urllib.request.urlopen(req, timeout=10).read().decode("gbk", "ignore")
        fields = raw.split('="')[-1].rstrip('"').split("~")
        if len(fields) < 30:
            return None
        from datetime import date
        return {
            "trade_date": date.today().isoformat(),
            "open": float(fields[5]), "high": float(fields[33]),
            "low": float(fields[34]), "close": float(fields[3]),
            "vol": int(float(fields[6]) * 100),
        }
    except Exception as e:
        print(f"  ⚠️ 实时行情失败 {symbol}: {e}")
        return None


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--db":
        # DB历史 + 实时补全模式（可重复、不依赖 mcp 手动复制）
        targets = [("sz000333", "000333.SZ", "美的集团"),
                   ("sh600900", "600900.SH", "长江电力"),
                   ("sh601899", "601899.SH", "紫金矿业")]
        print(f"{'代码':<10}{'名称':<8}{'收盘':>9}{'COMBO':>8}{'VWM':>6}{'BBR':>6}{'ADX':>7}{'RSI':>7}  动作")
        print("-" * 78)
        for sym, ts_code, name in targets:
            hist = fetch_db_history(ts_code)
            rt = fetch_realtime(sym)
            if rt:
                # 去重：若历史最后一天==今日则替换，否则追加
                if hist and hist[-1]["trade_date"] == rt["trade_date"]:
                    hist[-1] = rt
                else:
                    hist.append(rt)
            if len(hist) < 30:
                print(f"{sym:<10}{name:<8} 数据不足({len(hist)}天)")
                continue
            sig = compute_combo(hist)
            act = decide(sig)
            print(f"{sym:<10}{name:<8}{sig['close_last']:>9.2f}{sig['combo_last']:>8.2f}"
                  f"{sig['vwm_last']:>6}{sig['bbr_last']:>6}{sig['adx_last']:>7.1f}"
                  f"{sig['rsi_last']:>7.1f}  {act}")
        return
    if len(sys.argv) > 1 and sys.argv[1] != "--stdin":
        path = sys.argv[1]
        data = load_kline(path)
    else:
        raw = sys.stdin.read()
        data = json.loads(raw)
    items = data.get("data", {}).get("data", [])
    if not items:
        print("⚠️ 无 K 线数据（data.data 为空）")
        return
    print(f"{'代码':<10}{'名称':<8}{'收盘':>9}{'COMBO':>8}{'VWM':>6}{'BBR':>6}{'ADX':>7}{'RSI':>7}  动作")
    print("-" * 78)
    for it in items:
        symbol = it["symbol"]
        series = to_merged_series(it["data"]["nodes"])
        if len(series) < 60:
            print(f"{symbol:<10} 数据不足({len(series)}天)")
            continue
        sig = compute_combo(series)
        name = NAME_MAP.get(symbol, symbol)
        act = decide(sig)
        print(f"{symbol:<10}{name:<8}{sig['close_last']:>9.2f}{sig['combo_last']:>8.2f}"
              f"{sig['vwm_last']:>6}{sig['bbr_last']:>6}{sig['adx_last']:>7.1f}"
              f"{sig['rsi_last']:>7.1f}  {act}")


if __name__ == "__main__":
    main()
