#!/usr/bin/env python3
"""本地全市场 COMBO 选股扫描（gtimg 兜底源 · 含创业板）

扫描范围：主板(60xxxx/SH, 00xxxx/SZ) + 中小板(002/003) + 创业板(300/301)。
池文件 mainboard_scan_pool.json 已含上述所有标的（07-29 创业板放开后含 300/301）。

背景：QTS daily_quote 全市场日线拉取在 2026-06 中断，仅 ~50 只样本仍更新到 7 月。
原 scan_mainboard_full.py 硬编码连容器名 quant-postgres:5432 且 scan_date 写死 2026-07-13，
本地跑不通、且结论基于 6 月旧数据 → "死水期"假象。

本脚本（2026-07-23 新增, 07-30 扩展含创业板）：
  1. 连本地映射端口 127.0.0.1:15432 读 daily_quote 历史（到 6 月仍有 100+ 日序列）
  2. 用 qt.gtimg.cn 实时行情补「今日一根 K 线」(open/high/low/close)，让信号基于最新价
  3. scan_date 动态取真实日期，不再硬编码
  4. 复用 scan_mainboard_full.py 的 COMBO 规则 (VWM0.6+BBR0.4, ADX>=25, RSI<=80)

运行：python3 .workbuddy/scripts/scan_mainboard_local.py [--limit N] [--no-gtimg]
      参数经 argparse 解析，--limit 支持 --limit=N 与 --limit N 两种写法
依赖：QTS strategy-service 指标包 (PYTHONPATH) + psycopg2 + urllib
"""
from __future__ import annotations

# ── QTS 指标（本地路径，importlib 绕过 services/__init__ 副作用）──
# services/__init__.py 会触发 ai_client.py（PEP604 语法，需 Py3.10+），
# 用 importlib 直接加载纯指标模块，避免拉起整个 services 包，兼容 Py3.9。
import argparse
import importlib.util
import json
import math
import os
import sys
import types
import urllib.request
from collections import defaultdict
from datetime import date, datetime

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

# ── 规则参数（对齐 scan_mainboard_full.py v2.0）──
ADX_FILTER = 25
RSI_BLOCK = 80
COMBO_BUY = 0.2
COMBO_STRONG = 0.4
MIN_BARS = 30
LOOKBACK = 60

# 用户实盘规模（对齐 USER.md）
USER_CAPITAL = 15000
MAX_SINGLE = USER_CAPITAL / 3
STOP_LOSS = 0.08

# ── 数据源 ──
POOL_PATH = os.path.join(os.path.dirname(__file__), "mainboard_scan_pool.json")
DB_CFG = {"host": "127.0.0.1", "port": 15432, "dbname": "quant_trading",
          "user": "quant_user", "password": "quant_pass"}  # noqa: S106
GTIMG_BASE = "https://qt.gtimg.cn/q="

# ── 公众号共振库（LLM 阅读自动化产出，多账号推荐同一股≥RESONANCE_THRESHOLD 算共振）──
# 用绝对路径（与 QTS_SERVICES 同风格），避免 __file__ 相对路径在 PYTHONPATH 调用下歧义
RESONANCE_PATH = "/Users/guan/WorkBuddy/Claw/.workbuddy/data/article_resonance.json"
RESONANCE_THRESHOLD = 2  # ≥2 篇公众号推荐同一股 = 共振（用户决策）


def load_resonance() -> dict:
    """读 article_resonance.json → {股票名: 推荐篇数}，仅保留 ≥ 阈值的共振股。

    返回空 dict 时调用方应静默跳过（不阻断主流程）。
    """
    if not os.path.exists(RESONANCE_PATH):
        return {}
    try:
        with open(RESONANCE_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    if not isinstance(data, list):
        return {}
    m = {}
    for rec in data:
        name = rec.get("stock_name", "")
        cnt = rec.get("count", 0)
        if name and cnt >= RESONANCE_THRESHOLD:
            m[name] = cnt
    return m


def match_resonance(pool_name: str, res_map: dict) -> int:
    """模糊匹配池内股票名到共振库（应对 LLM 提取的截断名，如「华天」vs「华天科技」）。

    规则：精确相等优先；否则取「共振名是 pool 名子串」中的最大篇数。
    反向（pool 名是共振名子串）不做，避免「华天」误命中无关长名。
    """
    if not pool_name or not res_map:
        return 0
    if pool_name in res_map:
        return res_map[pool_name]
    best = 0
    for rn, cnt in res_map.items():
        if rn and rn in pool_name:
            best = max(best, cnt)
    return best



# ---------------------------------------------------------------------------
def load_pool():
    with open(POOL_PATH, encoding="utf-8") as f:
        return json.load(f)  # ts_code -> {avg_amt, name}


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
    """批量拉 gtimg 实时快照。codes: ts_code 列表(如 600036.SH)。
    返回 {ts_code: {open,high,low,close,date}}。"""
    sym_map = {}
    for c in codes:
        market = "sh" if c.endswith(".SH") else "sz"
        num = c.split(".")[0]
        sym_map[f"{market}{num}"] = c
    out = {}
    today = date.today().isoformat()
    # 分批(每批40)避免 414 URI Too Large
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


# ---------------------------------------------------------------------------
WINDOW = 10  # 信号聚合窗口(最近 N 日)


def calc_combo(series):
    """series: [(high,low,close,td)] -> COMBO 信号。

    VWM/BBR 是事件型信号(触发点±1，非连续评分)，故取最近 WINDOW 日内的
    信号求和作为"当前信号强度"，避免原脚本末位单点=0 导致的"死水期"假象。
    """
    dicts = [{"close": r[2], "high": r[0], "low": r[1], "volume": 0, "open": r[2]}
             for r in series]
    cl = [r[2] for r in series]
    hi = [r[0] for r in series]
    lo = [r[1] for r in series]
    vwm = generate_signals(dicts, "vwm", {})
    bbr = generate_signals(dicts, "bollinger", {})
    # 窗口聚合: 最近 WINDOW 日信号求和
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
    ap = argparse.ArgumentParser(description="本地全市场(含创业板) COMBO 选股扫描")
    ap.add_argument("--limit", type=int, default=None,
                    help="仅扫描前 N 只（按池顺序），默认全池")
    ap.add_argument("--no-gtimg", action="store_true",
                    help="关闭 qt.gtimg.cn 实时行情兜底，仅用历史日线")
    args = ap.parse_args()

    limit = args.limit
    use_gtimg = not args.no_gtimg

    pool = load_pool()
    codes = list(pool.keys())
    if limit:
        codes = codes[:limit]
    resonance = load_resonance()  # {股票名: 推荐篇数}，仅共振股
    print(f"[INFO] 池 {len(pool)} 只; 本次扫 {len(codes)} 只; gtimg兜底={'开' if use_gtimg else '关'}; 共振股 {len(resonance)} 只")

    bars = fetch_history(codes)
    rt = fetch_gtimg_batch(codes) if use_gtimg else {}

    rows = []
    skipped = 0
    today = date.today().isoformat()
    for code in codes:
        s = bars.get(code, [])
        # 拼今日K线(gtimg 快照)
        snap = rt.get(code)
        if snap and (not s or s[-1][3] != today):
            s = list(s) + [(snap["high"], snap["low"], snap["close"], today)]
        if len(s) < MIN_BARS:
            skipped += 1
            continue
        s = s[-LOOKBACK:]
        combo, vwm, bbr, adx, rsi, close = calc_combo(s)
        act = decide(combo, adx, rsi)
        # 共振加分：命中公众号共振库则记篇数（不影响 decide 买卖判定，仅作标签+同档优先）
        res = match_resonance(pool[code]["name"], resonance)
        rows.append((code, pool[code]["name"], close, combo, vwm, bbr, adx, rsi, act,
                     "history+gtimg" if snap else "history-only", res))

    scan_date = today if use_gtimg else (s[-1][3] if (s := bars.get(codes[0], [("","","","")])) else today)
    print(f"[DEBUG] 有效 {len(rows)} 只; 跳过(数据不足) {skipped} 只; scan_date={scan_date}")

    order = {"STRONG_BUY": 0, "BUY": 1, "HOLD": 2, "HOLD_ADX_WEAK": 3,
             "HOLD_OVERBOUGHT": 4, "SELL": 5}
    # 排序：action 优先级 → 共振优先(-res 使共振股在同档内靠前) → COMBO 降序
    rows.sort(key=lambda r: (order.get(r[8], 9), -r[10], -r[3]))

    print(f"{'CODE':<10}{'NAME':<10}{'CLOSE':>9}{'COMBO':>8}{'VWM':>5}{'BBR':>5}{'ADX':>7}{'RSI':>7}  ACTION")
    print("-" * 96)
    for r in rows[:60]:
        print(f"{r[0]:<10}{r[1]:<10}{r[2]:>9.2f}{r[3]:>8.2f}{r[4]:>5}{r[5]:>5}{r[6]:>7.1f}{r[7]:>7.1f}  {r[8]}")

    buys = [r for r in rows if r[8] in ("BUY", "STRONG_BUY")]
    print(f"\n=== 买入候选 (COMBO>={COMBO_BUY} & ADX>={ADX_FILTER} & RSI<={RSI_BLOCK}) ===")
    print(f"全市场(含创业板)扫描 {len(rows)} 只有效; 买入候选 {len(buys)} 只\n")
    for r in buys:
        code, name, close = r[0], r[1], r[2]
        price = close
        max_shares = int(MAX_SINGLE // price // 100 * 100)
        if max_shares < 100:
            max_shares = 0
        stop_px = round(price * (1 - STOP_LOSS), 2)
        lots = max_shares // 100
        cost = max_shares * price
        print(f"■ {code} {name}")
        res_tag = f" | 📡公众号共振×{r[10]}" if r[10] >= RESONANCE_THRESHOLD else ""
        print(f"  现价 {price:.2f} | COMBO {r[3]:.2f}(VWM{r[4]}/BBR{r[5]}) ADX {r[6]} RSI {r[7]} [{r[8]}]{res_tag}")
        if lots > 0:
            print(f"  建议: 买 {lots} 手({max_shares}股) ≈ ¥{cost:,.0f} | 止损价 {stop_px} (-8%) | 周期 3-10天")
            print(f"  风险: 中等; 单只仓位 {cost/USER_CAPITAL*100:.0f}% ≤ 33%上限; 跌破止损立即走")
        else:
            print(f"  建议: 现价 {price:.2f} 超单只上限(¥{MAX_SINGLE:,.0f}), 不推")
        print()

    cand = [{"code": r[0], "name": r[1], "close": r[2], "combo": r[3],
             "vwm": r[4], "bbr": r[5], "adx": r[6], "rsi": r[7], "action": r[8],
             "src": r[9], "resonance": r[10]} for r in buys]
    # action 分布统计
    dist = defaultdict(int)
    for r in rows:
        dist[r[8]] += 1
    dist_str = dict(sorted(dist.items(), key=lambda x: -x[1]))
    out = {"scan_date": scan_date, "total": len(rows), "skipped": skipped,
           "pool_size": len(pool), "buys": cand, "action_dist": dist_str,
           "data_source": "QTS daily_quote(history) + gtimg(realtime today)"}
    with open("/tmp/mainboard_scan_result.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("候选结果已写 /tmp/mainboard_scan_result.json")
    print(f"\n[分布] action_dist={dict(dist_str)}")


if __name__ == "__main__":
    main()
