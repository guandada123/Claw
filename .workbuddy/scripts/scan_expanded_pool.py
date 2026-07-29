#!/usr/bin/env python3
"""扩大池(科技+红利38只) COMBO 扫描 — 本地架构(根治容器依赖)

与 scan_mainboard_local.py 统一架构：
  1. 连本地 QTS Postgres 127.0.0.1:15432 读 daily_quote 历史
  2. 用 qt.gtimg.cn 实时行情补「今日一根 K 线」(high/low/close)
  3. 复用 scan_mainboard_local.py 的 COMBO 规则 (VWM0.6+BBR0.4, ADX>=25, RSI<=80)
  4. 不再依赖容器内 /tmp/expanded_pool_klines.json 或 /app PYTHONPATH

运行：python3 .workbuddy/scripts/scan_expanded_pool.py [--no-gtimg]
依赖：QTS strategy-service 指标包 (本文件自带 sys.path) + psycopg2 + urllib
"""
from __future__ import annotations

# ── QTS 指标（本地路径，importlib 绕过 services/__init__ 副作用）──
# services/__init__.py 会触发 ai_client.py（PEP604 语法，需 Py3.10+），
# 用 importlib 直接加载纯指标模块，避免拉起整个 services 包，兼容 Py3.9。
import importlib.util
import json
import math
import os
import sys
import types
import urllib.request
from collections import defaultdict
from datetime import date

QTS_SERVICES = "/Users/guan/WorkBuddy/QuantTradingSystem/strategy-service"
_SERVICES_PKG = os.path.join(QTS_SERVICES, "services")


def _load_qts(mod_file, mod_name):
    spec = importlib.util.spec_from_file_location(
        mod_name, os.path.join(_SERVICES_PKG, mod_file))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# 伪造 services 包（不执行真实 __init__），让 signals 的相对 import 命中
_svc_pkg = types.ModuleType("services")
_svc_pkg.__path__ = [_SERVICES_PKG]
sys.modules.setdefault("services", _svc_pkg)
_ind = _load_qts("indicators.py", "services.indicators")
_sig = _load_qts("signals.py", "services.signals")
calculate_adx = _ind.calculate_adx
calculate_rsi = _ind.calculate_rsi
generate_signals = _sig.generate_signals

# ── 规则参数（对齐 scan_mainboard_local.py）──
ADX_FILTER = 25
RSI_BLOCK = 80
COMBO_BUY = 0.2
COMBO_STRONG = 0.4
MIN_BARS = 30
LOOKBACK = 60
WINDOW = 10  # 信号聚合窗口(最近 N 日)

# ── 数据源 ──
POOL_PATH = os.path.join(os.path.dirname(__file__), "expanded_pool.json")
DB_CFG = {"host": "127.0.0.1", "port": 15432, "dbname": "quant_trading",
          "user": "quant_user", "password": "quant_pass"}  # noqa: S106
GTIMG_BASE = "https://qt.gtimg.cn/q="


# ---------------------------------------------------------------------------
def load_pool():
    with open(POOL_PATH, encoding="utf-8") as f:
        return json.load(f)  # ts_code -> {name}


def fetch_history(codes):
    """从本地 QTS Postgres 读每只标的最近 75 日历史日线。"""
    import psycopg2
    bars = defaultdict(list)
    try:
        conn = psycopg2.connect(**DB_CFG, connect_timeout=8)
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] Postgres 连接失败({e})，将仅用 gtimg 当日快照(数据不足标的跳过)")
        return bars
    try:
        cur = conn.cursor()
        placeholders = ",".join(["%s"] * len(codes))
        sql = (
            "SELECT ts_code, high, low, close, trade_date FROM daily_quote "
            "WHERE ts_code IN (" + placeholders + ") "
            "AND trade_date >= (SELECT MAX(trade_date) FROM daily_quote) - INTERVAL '75 day' "
            "ORDER BY ts_code, trade_date"
        )
        cur.execute(sql, tuple(codes))
        for ts_code, high, low, close, td in cur.fetchall():
            bars[ts_code].append((float(high), float(low), float(close), str(td)))
    finally:
        conn.close()
    return bars


def fetch_gtimg_batch(codes):
    """批量拉 gtimg 实时快照。返回 {ts_code: {high,low,close,date}}。"""
    sym_map = {}
    for c in codes:
        market = "sh" if c.endswith(".SH") else "sz"
        num = c.split(".")[0]
        sym_map[f"{market}{num}"] = c
    out = {}
    today = date.today().isoformat()
    for i in range(0, len(codes), 40):
        batch = codes[i:i + 40]
        q = ",".join(f"{'sh' if x.endswith('.SH') else 'sz'}{x.split('.')[0]}" for x in batch)
        url = GTIMG_BASE + q
        try:  # noqa
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
            raw = urllib.request.urlopen(req, timeout=20).read().decode("gbk", "ignore")
        except Exception as e:  # noqa: BLE001
            print(f"[WARN] gtimg 批次 {i} 拉取失败({e})")
            continue
        for line in raw.split(";"):
            line = line.strip()
            if not line or "=" not in line:
                continue
            try:
                key = line.split("=")[0].replace("v_", "")
                f = line.split('="')[-1].rstrip('"').split("~")
                if len(f) < 35:
                    continue
                ts_code = sym_map.get(key)
                if not ts_code:
                    continue
                out[ts_code] = {
                    "high": float(f[33]), "low": float(f[34]),
                    "close": float(f[3]), "date": today,
                }
            except (ValueError, IndexError):
                continue
    return out


def calc_combo(series):
    """series: [(high,low,close,td)] -> COMBO 信号(窗口聚合)。"""
    dicts = [{"close": r[2], "high": r[0], "low": r[1], "volume": 0, "open": r[2]}
             for r in series]
    cl = [r[2] for r in series]
    hi = [r[0] for r in series]
    lo = [r[1] for r in series]
    vwm = generate_signals(dicts, "vwm", {})
    bbr = generate_signals(dicts, "bollinger", {})
    vw = sum(vwm[-WINDOW:])
    bb = sum(bbr[-WINDOW:])
    combo_val = round(0.6 * vw + 0.4 * bb, 3)
    adx = calculate_adx(hi, lo, cl, 14)[2]
    rsi = calculate_rsi(cl, 14)

    def sl(v):
        v = v[-1] if v else 0.0
        return 0.0 if (isinstance(v, float) and math.isnan(v)) else float(v)

    return (combo_val, int(vw), int(bb),
            round(sl(adx), 1), round(sl(rsi), 1), cl[-1])


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


# ---------------------------------------------------------------------------
def main():
    use_gtimg = True
    for a in sys.argv[1:]:
        if a == "--no-gtimg":
            use_gtimg = False

    pool = load_pool()
    codes = list(pool.keys())
    print(f"[INFO] 扩大池 {len(pool)} 只(科技+红利); 本次扫 {len(codes)} 只; "
          f"gtimg兜底={'开' if use_gtimg else '关'}")

    bars = fetch_history(codes)
    rt = fetch_gtimg_batch(codes) if use_gtimg else {}

    rows = []
    skipped = 0
    today = date.today().isoformat()
    for code in codes:
        s = bars.get(code, [])
        snap = rt.get(code)
        if snap and (not s or s[-1][3] != today):
            s = list(s) + [(snap["high"], snap["low"], snap["close"], today)]
        if len(s) < MIN_BARS:
            skipped += 1
            continue
        s = s[-LOOKBACK:]
        combo, vwm, bbr, adx, rsi, close = calc_combo(s)
        act = decide(combo, adx, rsi)
        rows.append((code, pool[code]["name"], close, combo, vwm, bbr, adx, rsi, act,
                     "history+gtimg" if snap else "history-only"))

    scan_date = today if use_gtimg else (
        s[-1][3] if (s := bars.get(codes[0], [("","","","")])) else today)
    print(f"[DEBUG] 有效 {len(rows)} 只; 跳过(数据不足) {skipped} 只; scan_date={scan_date}")

    order = {"STRONG_BUY": 0, "BUY": 1, "HOLD": 2, "HOLD_ADX_WEAK": 3,
             "HOLD_OVERBOUGHT": 4, "SELL": 5}
    rows.sort(key=lambda r: (order.get(r[8], 9), -r[3]))

    print(f"{'CODE':<10}{'NAME':<10}{'CLOSE':>9}{'COMBO':>8}{'VWM':>5}{'BBR':>5}{'ADX':>7}{'RSI':>7}  ACTION")
    print("-" * 96)
    for r in rows:
        print(f"{r[0]:<10}{r[1]:<10}{r[2]:>9.2f}{r[3]:>8.2f}{r[4]:>5}{r[5]:>5}{r[6]:>7.1f}{r[7]:>7.1f}  {r[8]}")

    buys = [r for r in rows if r[8] in ("BUY", "STRONG_BUY")]
    print(f"\n=== 买入候选 (COMBO>={COMBO_BUY} & ADX>={ADX_FILTER} & RSI<={RSI_BLOCK}) ===")
    print(f"扩大池扫描 {len(rows)} 只有效; 买入候选 {len(buys)} 只\n")
    for r in buys:
        code, name, close = r[0], r[1], r[2]
        print(f"■ {code} {name}")
        print(f"  现价 {close:.2f} | COMBO {r[3]:.2f}(VWM{r[4]}/BBR{r[5]}) ADX {r[6]} RSI {r[7]} [{r[8]}]")
        print()

    cand = [{"code": r[0], "name": r[1], "close": r[2], "combo": r[3],
             "vwm": r[4], "bbr": r[5], "adx": r[6], "rsi": r[7], "action": r[8],
             "src": r[9]} for r in buys]
    dist = defaultdict(int)
    for r in rows:
        dist[r[8]] += 1
    dist_str = dict(sorted(dist.items(), key=lambda x: -x[1]))
    out = {"scan_date": scan_date, "total": len(rows), "skipped": skipped,
           "pool_size": len(pool), "buys": cand, "action_dist": dist_str,
           "data_source": "QTS daily_quote(history) + gtimg(realtime today)"}
    with open("/tmp/expanded_pool_scan_result.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("候选结果已写 /tmp/expanded_pool_scan_result.json")
    print(f"\n[分布] action_dist={dict(dist_str)}")


if __name__ == "__main__":
    main()
