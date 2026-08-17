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
import statistics
import sys
import types
import urllib.request
from collections import defaultdict
from datetime import date, datetime

QTS_SERVICES = "/Users/guan/WorkBuddy/QuantTradingSystem/strategy-service"
_SERVICES_PKG = os.path.join(QTS_SERVICES, "services")


def _load_qts(mod_file, mod_name):
    spec = importlib.util.spec_from_file_location(mod_name, os.path.join(_SERVICES_PKG, mod_file))
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

# ── 规则参数（v2.1 修正: 基于 2026-08-14 全市场 COMBO 分布校准）──
# 诊断实测: 全市场 COMBO 中位数=-0.8, max=0.0, 0 只 >=0.2。
# VWM/BBR 为逆向信号, 在近 10 日震荡市只产负事件, 故需区分:
#   * COMBO==0  → 无信号(观望), 不判 SELL
#   * -1.0<COMBO<0 → 轻度逆向信号(弱趋势/观望), 不判 SELL
#   * COMBO<=-1.0 → 明确弱势(全市场约 45% 分位), 才标 SELL
ADX_FILTER = 25
RSI_BLOCK = 80
COMBO_BUY = 0.2  # 买入阈值(正向信号, 当前市场实际无触发)
COMBO_STRONG = 0.4  # 强买阈值
COMBO_SELL = -1.0  # 明确弱势阈值(原 -0.2 过严, 误把轻度逆向信号标成卖压)
MIN_BARS = 30
LOOKBACK = 60

# 用户实盘规模（对齐 USER.md）
USER_CAPITAL = 15000
MAX_SINGLE = USER_CAPITAL / 3
STOP_LOSS = 0.08

# ── 数据源 ──
POOL_PATH = os.path.join(os.path.dirname(__file__), "mainboard_scan_pool.json")
DB_CFG = {
    "host": "127.0.0.1",
    "port": 15432,
    "dbname": "quant_trading",
    "user": "quant_user",
    "password": "quant_pass",
}  # noqa: S106
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
        sql = (  # nosec B608
            "SELECT ts_code, high, low, close, trade_date FROM daily_quote "
            "WHERE ts_code IN (" + placeholders + ") "  # nosec B608
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
        batch = codes[i : i + 40]
        q = ",".join(f"{'sh' if x.endswith('.SH') else 'sz'}{x.split('.')[0]}" for x in batch)
        url = GTIMG_BASE + q
        try:  # noqa
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}
            )
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
                    "high": float(f[33]),
                    "low": float(f[34]),
                    "close": float(f[3]),
                    "date": today,
                }
            except (ValueError, IndexError):
                continue
    return out


# ---------------------------------------------------------------------------
WINDOW = 10  # 信号聚合窗口(最近 N 日)


def calc_combo(series):
    """series: [(high,low,close,td)] -> COMBO 信号 (v2.2 加入正向 KDJ 趋势信号)。

    VWM/BBR 是逆向信号(在震荡市只产负事件, 导致原 COMBO 恒<=0, BUY 不可达);
    2026-08-14 回测确认: KDJ金叉+ADX>=25+RSI<=70 持10日 → 胜率50.6%/均值+2.28%
    (随机对照 43.3%/-0.62%), 具真实正收益优势。故将 KDJ 正向信号纳入 COMBO,
    让买入分支在当前市场结构下可达。

    权重(v2.2): COMBO = 0.45*VWM + 0.30*BBR + 0.25*KDJ_pos
      - KDJ_pos = 最近 WINDOW 日 KDJ 金叉(+1)次数, 范围[0, WINDOW]
      - 归一化到与 VWM/BBR 同量级(事件型±1求和), 避免单一信号主导
    """
    dicts = [{"close": r[2], "high": r[0], "low": r[1], "volume": 0, "open": r[2]} for r in series]
    cl = [r[2] for r in series]
    hi = [r[0] for r in series]
    lo = [r[1] for r in series]
    vwm = generate_signals(dicts, "vwm", {})
    bbr = generate_signals(dicts, "bollinger", {})
    kdj = generate_signals(dicts, "kdj", {})
    # 窗口聚合: 最近 WINDOW 日信号求和
    vw = sum(vwm[-WINDOW:])
    bb = sum(bbr[-WINDOW:])
    kd = sum(kdj[-WINDOW:])  # KDJ金叉(+1)次数, 正向
    combo_val = round(0.45 * vw + 0.30 * bb + 0.25 * kd, 3)
    adx = calculate_adx(hi, lo, cl, 14)[2]
    rsi = calculate_rsi(cl, 14)

    def sl(v):
        v = v[-1] if v else 0.0
        return 0.0 if (isinstance(v, float) and math.isnan(v)) else float(v)

    return (combo_val, int(vw), int(bb), int(kd), round(sl(adx), 1), round(sl(rsi), 1), cl[-1])


def decide(combo, adx, rsi, kdj_pos=0):
    """买卖判定 (v2.3: 回退 v2.2 KDJ 驱动 BUY — QTS 级回测证明无 alpha)。

    根因(2026-08-14): VWM/BBR 为逆向信号, 在震荡市只产负事件 → 原 COMBO 恒<=0,
    BUY 不可达, 且被误渲染成"67.8% 卖压"。v2.1 已修：SELL 阈值从 -0.2 收到 -1.0,
    引入 HOLD 中间态 + 🔴信号健康度门禁（买入候选=0 且全市场 KDJ金叉=0 → 标红）。

    v2.2 曾引入 KDJ金叉+ADX>=25+RSI<=80 驱动 BUY（轻量回测 n=2028 胜率50.6%/+2.28%）。
    但 2026-08-14 QTS 级回测（带成本/止损/T+1，2.5年全4401只）证伪该 edge：
      [策略] KDJ金叉+ADX>=25+RSI<=80 : +39.24% | Sharpe 0.629 | 回撤 -24.30%
      [对照B] 随机入场(同频次)        : +37.99% | Sharpe ~   | 回撤 ~
      → 策略相对随机仅 +1.25pp，统计不显著，KDJ金叉规则在真实成本下无 distinguishable alpha。
    故 v2.3 回退 BUY 触发：BUY 仅在 combo>=COMBO_BUY(0.2) 时成立（当前市场恒不可达），
    扫描如实输出 0 候选 + 🔴 信号健康度告警，不再用伪信号粉饰"选出了股"。
    kdj_pos 仍保留用于健康度诊断（统计全市场 KDJ 正向触发数）。
    """
    if combo >= COMBO_BUY:
        return "BUY"
    if combo <= COMBO_SELL:
        return "SELL"
    if adx < ADX_FILTER:
        return "HOLD_ADX_WEAK"
    if rsi > RSI_BLOCK:
        return "HOLD_OVERBOUGHT"
    return "HOLD"
    return "HOLD"


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="本地全市场(含创业板) COMBO 选股扫描")
    ap.add_argument("--limit", type=int, default=None, help="仅扫描前 N 只（按池顺序），默认全池")
    ap.add_argument(
        "--no-gtimg", action="store_true", help="关闭 qt.gtimg.cn 实时行情兜底，仅用历史日线"
    )
    args = ap.parse_args()

    limit = args.limit
    use_gtimg = not args.no_gtimg

    pool = load_pool()
    codes = list(pool.keys())
    if limit:
        codes = codes[:limit]
    resonance = load_resonance()  # {股票名: 推荐篇数}，仅共振股
    print(
        f"[INFO] 池 {len(pool)} 只; 本次扫 {len(codes)} 只; gtimg兜底={'开' if use_gtimg else '关'}; 共振股 {len(resonance)} 只"
    )

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
        combo, vwm, bbr, kdj_pos, adx, rsi, close = calc_combo(s)
        act = decide(combo, adx, rsi, kdj_pos)
        # 共振加分：命中公众号共振库则记篇数（不影响 decide 买卖判定，仅作标签+同档优先）
        res = match_resonance(pool[code]["name"], resonance)
        rows.append(
            (
                code,
                pool[code]["name"],
                close,
                combo,
                vwm,
                bbr,
                kdj_pos,
                adx,
                rsi,
                act,
                "history+gtimg" if snap else "history-only",
                res,
            )
        )

    scan_date = (
        today
        if use_gtimg
        else (s[-1][3] if (s := bars.get(codes[0], [("", "", "", "")])) else today)
    )
    print(f"[DEBUG] 有效 {len(rows)} 只; 跳过(数据不足) {skipped} 只; scan_date={scan_date}")

    # ── 信号健康度自检 (提前到表前打印, 确保自动化 LLM prompt 首屏可见) ──
    combo_vals = [r[3] for r in rows]
    combo_median = round(statistics.median(combo_vals), 3) if combo_vals else 0.0
    # 正向信号股 = KDJ金叉≥1 (诊断用，v2.3 起不再驱动 BUY)
    n_pos_signal = sum(1 for r in rows if r[6] >= 1)
    buys_early = [r for r in rows if r[9] in ("BUY", "STRONG_BUY")]
    n_buy = len(buys_early)
    # 信号健康度分级（v2.3 回退伪 KDJ 信号后）：
    #   - 0 候选 → 🔴 引擎失效（combo>=COMBO_BUY 全市场不可达，非真实"全市场偏弱"）
    #   - 1~5 候选 → ⚠️ 可选标的极少（横盘市常态，非买入信号，勿满仓）
    #   - >5 候选 → ✅ 出现可信买入候选
    if n_buy == 0:
        SIGNAL_HEALTH_OK = False
        health_tag = (
            "🔴 信号引擎失效: 买入候选=0（combo>=COMBO_BUY 全市场不可达）"
            " — QTS 级回测已证伪 KDJ 正向信号 alpha，扫描如实输出 0 候选，非真实'全市场偏弱'误报"
        )
    elif n_buy <= 5:
        SIGNAL_HEALTH_OK = True
        health_tag = (
            f"⚠️ 可选标的极少({n_buy}只): 横盘市 COMBO 普遍负向，此乃市场常态而非买入信号；"
            "QTS 回测证伪 KDJ 规则 alpha，候选仅作观察，禁止据此满仓"
        )
    else:
        SIGNAL_HEALTH_OK = True
        health_tag = "✅ 信号引擎正常(出现可信买入候选)"
    print(
        f"[信号健康度] median_COMBO={combo_median} | KDJ金叉股(诊断)={n_pos_signal}只 | 买入候选={n_buy}只 | {health_tag}"
    )

    order = {
        "STRONG_BUY": 0,
        "BUY": 1,
        "HOLD": 2,
        "HOLD_ADX_WEAK": 3,
        "HOLD_OVERBOUGHT": 4,
        "SELL": 5,
    }
    # 排序：action 优先级 → 共振优先(-res 使共振股在同档内靠前) → COMBO 降序
    rows.sort(key=lambda r: (order.get(r[9], 9), -r[11], -r[3]))

    print(
        f"{'CODE':<10}{'NAME':<10}{'CLOSE':>9}{'COMBO':>8}{'VWM':>5}{'BBR':>5}{'KDJ':>5}{'ADX':>7}{'RSI':>7}  ACTION"
    )
    print("-" * 102)
    for r in rows[:60]:
        print(
            f"{r[0]:<10}{r[1]:<10}{r[2]:>9.2f}{r[3]:>8.2f}{r[4]:>5}{r[5]:>5}{r[6]:>5}{r[7]:>7.1f}{r[8]:>7.1f}  {r[9]}"
        )

    buys = [r for r in rows if r[9] in ("BUY", "STRONG_BUY")]
    print(f"\n=== 买入候选 (COMBO>={COMBO_BUY}, v2.3 回退伪信号) ===")
    print(f"全市场(含创业板)扫描 {len(rows)} 只有效; 买入候选 {len(buys)} 只")
    if not buys:
        print("🔴 本轮无任何买入候选：信号体系在当前市场结构下无可达的正向触发。")
        print("   历史: v2.2 曾用 KDJ金叉+ADX>=25+RSI<=80 造出 186 只候选，但 QTS 级回测")
        print("   (2.5年/4401只/带成本) 证明该规则相对随机入场仅 +1.25pp，无显著 alpha，已回退。")
        print("   结论: 扫描如实报 0，不应粉饰为'选出了股'。\n")
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
        res_tag = f" | 📡公众号共振×{r[11]}" if r[11] >= RESONANCE_THRESHOLD else ""
        print(
            f"  现价 {price:.2f} | COMBO {r[3]:.2f}(VWM{r[4]}/BBR{r[5]}/KDJ+{r[6]}) ADX {r[7]} RSI {r[8]} [{r[9]}]{res_tag}"
        )
        if lots > 0:
            print(
                f"  建议: 买 {lots} 手({max_shares}股) ≈ ¥{cost:,.0f} | 止损价 {stop_px} (-8%) | 周期 3-10天"
            )
            print(
                f"  风险: 中等; 单只仓位 {cost / USER_CAPITAL * 100:.0f}% ≤ 33%上限; 跌破止损立即走"
            )
        else:
            print(f"  建议: 现价 {price:.2f} 超单只上限(¥{MAX_SINGLE:,.0f}), 不推")
        print()

    cand = [
        {
            "code": r[0],
            "name": r[1],
            "close": r[2],
            "combo": r[3],
            "vwm": r[4],
            "bbr": r[5],
            "kdj_pos": r[6],
            "adx": r[7],
            "rsi": r[8],
            "action": r[9],
            "src": r[10],
            "resonance": r[11],
        }
        for r in buys
    ]
    # action 分布统计 (act 现位于 r[9])
    dist = defaultdict(int)
    for r in rows:
        dist[r[9]] += 1
    dist_str = dict(sorted(dist.items(), key=lambda x: -x[1]))

    # 信号健康度变量已在表前计算 (combo_median / n_pos_signal / n_buy / health_tag)
    print(
        f"\n[信号健康度·复验] median_COMBO={combo_median} | 正向信号股={n_pos_signal}只 | 买入候选={n_buy}只 | {health_tag}"
    )

    out = {
        "scan_date": scan_date,
        "total": len(rows),
        "skipped": skipped,
        "pool_size": len(pool),
        "buys": cand,
        "action_dist": dist_str,
        "signal_health": {
            "ok": SIGNAL_HEALTH_OK,
            "median_combo": combo_median,
            "n_positive_signal": n_pos_signal,
            "note": health_tag,
        },
        "data_source": "QTS daily_quote(history) + gtimg(realtime today)",
    }
    with open("/tmp/mainboard_scan_result.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("候选结果已写 /tmp/mainboard_scan_result.json")
    print(f"\n[分布] action_dist={dict(dist_str)}")


if __name__ == "__main__":
    main()
