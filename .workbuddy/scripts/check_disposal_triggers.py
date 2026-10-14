#!/usr/bin/env python3
"""实盘处置触发器 — 把「不操作就放 66 天」变成「到点必须给结论」（2026-10-11）。

背景：`live-disposal-plan-2026-10-11.html` 建议减仓（推荐方案 B：清华天+减长电100），
但实盘执行靠人工 → 方案发出去容易石沉大海（历史上 H1 止损逾期 66 天）。
本脚本按方案第三章的三条复核触发器做**到期必查、触发必报**。

四条触发器：
    T1 深度亏损：任一持仓 pnl_pct ≤ -35%
    T2 板块持续弱：跟踪板块 RPS < 20 连续 ≥3 个**不同 report_date**（天然抗陈旧/重跑）
    T3 无动作 + 高集中：跟踪板块占比 > 65% 且 距基线 ≥5 个交易日 且 持仓股数未变
    M1 市场级破位：**上证指数收盘 ≤ 关注点 3764**（2026-10-15 用户指令「关注点调整为 3764 点」）

⚠️ 方案文档：`live-disposal-plan-2026-10-11.html` **已丢失**（本机与 git 历史均无），
   现存唯一记录见 `.workbuddy/docs/live-disposal-plan-2026-10-11.md`（重建版，只收录可核 evidence）。

输出与退出码（供自动化判定）：
    rc=0 → 未触发（或已达标退休）→ 静默，不推送
    rc=1 → 任一触发 → 由自动化推送飞书（必须给明确处置结论，不只报红线）

用法：
    python3 .workbuddy/scripts/check_disposal_triggers.py [--dry-run] [--sector 半导体]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent  # Claw 仓库根

USER_PORTFOLIO = PROJECT_ROOT / ".workbuddy" / "data" / "user" / "portfolio.json"
SECTOR_CACHE = PROJECT_ROOT / ".workbuddy" / "data" / "sector_cache.json"
SECTOR_STRENGTH = PROJECT_ROOT / ".workbuddy" / "data" / "sector_strength.json"
BASELINE_FILE = PROJECT_ROOT / ".workbuddy" / "data" / "disposal_baseline.json"
RPS_HISTORY = PROJECT_ROOT / ".workbuddy" / "data" / "sector_rps_history.json"

# ── 阈值（与处置方案第三章一致） ──────────────────────────
DEEP_LOSS = -0.35  # T1
RPS_WEAK = 20.0  # T2
T2_CONSEC = 3  # T2 连续天数（按不同 report_date 计）
CONCENTRATION_MAX = 65.0  # T3 集中度上限(%)
T3_IDLE_DAYS = 5  # T3 无动作交易日数

# ── M1 市场级关注点（2026-10-15 用户指令：把关注点调整为 3764 点）──
#    标的是**上证指数**（同日实测：上证 3767.23、深证 12110.59、创业板 2866.76、沪深300 4217.68
#    —— 只有上证与该数值同量级）。判据用**收盘价**，与本脚本 15:45 收盘后运行的时点一致。
SSE_CODE = "sh000001"
SSE_FOCUS_LEVEL = 3764.0
SSE_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,5,qfq"


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def load_calendar():
    """交易日历：返回 (holidays, ok)。不可用则降级自然日。"""
    try:
        sys.path.insert(0, str(SCRIPT_DIR))
        import is_trading_day as itd

        return itd.load_holidays(), True
    except SystemExit:
        return None, False
    except Exception:  # noqa: BLE001
        return None, False


def _normalize_holidays(holidays) -> set[str]:
    """把休市日历统一成 ISO 字符串集合。

    为什么必须归一化：`is_trading_day(d, holidays)` 内部是
    ``d.isoformat() not in dates`` —— **只认字符串**。若调用方顺手传了一组
    ``date`` 对象，比较恒为 False（字符串 ∉ 日期集合）→ **节假日被当成交易日**，
    计数偏大且完全不报错（测试写出来的当天实测）。
    """
    if holidays is None:
        return set()
    dates = holidays.get("dates") if isinstance(holidays, dict) else holidays
    if not dates:
        return set()
    return {d.isoformat() if isinstance(d, date) else str(d) for d in dates}


def trading_days_between(start: date, end: date, holidays, cal_ok: bool) -> tuple[int, str]:
    """[start, end] 区间内的交易日数（不含 start，含 end）。日历不可用 → 自然日降级并标注。"""
    if end <= start:
        return 0, "交易日"
    if not cal_ok:
        return (end - start).days, "自然日(日历不可用降级)"
    import is_trading_day as itd

    hol_set = _normalize_holidays(holidays)
    n, d = 0, start + timedelta(days=1)
    while d <= end:
        if itd.is_trading_day(d, hol_set):
            n += 1
        d += timedelta(days=1)
    return n, "交易日"


def sector_of(code: str, cache: dict) -> str | None:
    return cache.get(code) if isinstance(cache, dict) else None


def fetch_sse_close(timeout: int = 10) -> tuple[float | None, str | None]:
    """取上证指数最新**收盘价**（腾讯行情，只读）。失败返回 (None, None)。

    为什么不复用其它脚本：本脚本要能在**没有任何缓存**时也判得了市场级触发；
    取不到就明确返回空 —— 由调用方打印「判不了」，**绝不用昨日值或臆造值冒充**。
    """
    import urllib.request

    url = SSE_KLINE_URL.format(code=SSE_CODE)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        rows = data.get("data", {}).get(SSE_CODE) or {}
        rows = rows.get("qfqday") or rows.get("day") or []
        if not rows:
            return None, None
        return float(rows[-1][2]), str(rows[-1][0])
    except Exception:  # noqa: BLE001 —— 网络/格式任何异常都只意味着"判不了"
        return None, None


def evaluate(
    portfolio: dict,
    cache: dict,
    sector: str,
    rps_series: list[float],
    sse_close: float | None = None,
) -> dict:
    """纯函数：算出指标（便于测试）。rps_series 为按 report_date 升序的 RPS 列表。"""
    holdings = portfolio.get("holdings", [])
    total = portfolio.get("summary", {}).get("total_assets", 0) or 0

    per_holding = []
    sector_value = 0.0
    for h in holdings:
        code = str(h.get("code", ""))
        shares = h.get("shares", 0) or 0
        price = h.get("current_price") or h.get("avg_cost") or 0
        cost = h.get("avg_cost") or 0
        mv = shares * price
        pnl_pct = (price / cost - 1) if cost else None
        per_holding.append(
            {
                "code": code,
                "name": h.get("name", code),
                "shares": shares,
                "mv": mv,
                "pnl_pct": pnl_pct,
            }
        )
        if sector_of(code, cache) == sector:
            sector_value += mv

    sector_pct = round(sector_value / total * 100, 1) if total else 0.0

    # T2：末段连续走弱（按不同 report_date 已去重）
    tail = rps_series[-T2_CONSEC:]
    t2 = len(tail) >= T2_CONSEC and all(v < RPS_WEAK for v in tail)

    return {
        "total_assets": round(total, 2),
        "sector": sector,
        "sector_value": round(sector_value, 2),
        "sector_pct": sector_pct,
        "per_holding": per_holding,
        "deep_loss": [
            h for h in per_holding if h["pnl_pct"] is not None and h["pnl_pct"] <= DEEP_LOSS
        ],
        "t2_weak": t2,
        "rps_tail": tail,
        "rps_latest": rps_series[-1] if rps_series else None,
        # M1 市场级关注点：None = 取数失败 → 视为"判不了"，不触发、也不谎报距离
        "sse_close": sse_close,
        "sse_dist": (round(sse_close - SSE_FOCUS_LEVEL, 2) if sse_close is not None else None),
        "m1_break": bool(sse_close is not None and sse_close <= SSE_FOCUS_LEVEL),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="实盘处置触发器")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写历史/基线")
    ap.add_argument(
        "--sse-close",
        type=float,
        default=None,
        help="覆盖上证收盘价（仅离线/测试用；正常走腾讯行情）",
    )
    ap.add_argument("--sector", default="半导体", help="跟踪板块（默认半导体）")
    args = ap.parse_args()

    # ── 休市闸门（PA-014）──────────────────────────────────────────────
    # 为什么必须有：本脚本的 RRULE 只有 BYDAY=MO-FR，**不含 A 股休市日历** →
    # 休市工作日也会照跑：板块层会以"新的一天 + 上一交易日的旧数据"多记一天，
    # 把 T2「连续 ≥3 个不同日期」灌水（PA-015 已从数据侧按 data_date 去重兜了一层，
    # 这里是**源头闸门**：连跑都不跑）。
    # 分寸：判不了（None）**不跳过** —— 不因日历不可用而停工，但要说出来。
    import is_trading_day as _itd

    _tt = _itd.today_is_trading()
    if _tt is False:
        print("⏸ 今日休市（非交易日）→ 跳过：不写盘、不推进连续计数基线")
        return 0
    if _tt is None:
        print("⚠️ 交易日历不可用 → 无法判定是否休市，按原逻辑继续（不因此停工）")

    portfolio = load_json(USER_PORTFOLIO, {})
    if not portfolio.get("holdings"):
        print("⚠️ 未读到持仓快照，跳过")
        return 0
    cache = load_json(SECTOR_CACHE, {})
    strength = load_json(SECTOR_STRENGTH, {})

    # 1) RPS 历史（按**数据日**去重）
    # ⚠️ 2026-10-11（PA-015）修正原判据：此前按 `report_date`（= **生成日**）为键。
    #    · 字典键本身就把「同日多跑」折叠成一条 → 所谓"重复计数"其实**不成立**（原描述有误）；
    #    · 真问题是**非交易日也跑**：生成日是新的一天、数据却还是上一交易日的旧值
    #      → 多出一个"休市日"键，把 T2「连续 ≥3 个不同日期」**灌水**。
    #    故改用 `data_date`（板块层取最新 K 线日）；**缺字段时回退** report_date ——
    #    老产物若直接不写，历史会停止增长，那比灌水更坏。
    history = load_json(RPS_HISTORY, {})
    rd = strength.get("data_date") or strength.get("report_date")
    rps_now = None
    if rd:
        row = next((s for s in strength.get("sectors", []) if s.get("sector") == args.sector), None)
        if row and "rps" in row:
            rps_now = float(row["rps"])
            history.setdefault(args.sector, {})[rd] = rps_now
    # 🔴 2026-10-11 取证（PA-015 排查时顺带发现，**比 PA 原描述更严重**）：
    #    `RPS_HISTORY` 此前**只读不写** —— 全脚本仅写过 BASELINE_FILE，磁盘上该文件也**不存在**。
    #    后果：`series` 每次运行都只有"今天一条" → **T2「连续 ≥3 个不同日期」永远不可能触发**，
    #    是**结构性死触发器**（而它此前一直"看起来在跑"）。
    #    （自审首轮那句"RPS 历史目前仅 1 个 report_date"其实是**没落盘**的症状，被归因成"才过一天"。）
    #    故在此持久化；放在基线逻辑**之前**，否则首次运行（建基线后即 return）会丢掉第一天。
    if rps_now is not None and not args.dry_run:
        try:
            RPS_HISTORY.write_text(
                json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as e:  # 写不了 = T2 仍是死的 → 必须响亮，别静默
            print(f"⚠️ RPS 历史写入失败({e}) → T2 判据将不可用")
    series = [history.get(args.sector, {})[d] for d in sorted(history.get(args.sector, {}))]

    if args.sse_close is not None:
        sse_close, sse_date = args.sse_close, "参数覆盖"
    else:
        sse_close, sse_date = fetch_sse_close()
    m = evaluate(portfolio, cache, args.sector, series, sse_close=sse_close)
    today = date.today()

    # 2) 基线（首次运行建立；结构变化则刷新基线 = 视为已动作）
    baseline = load_json(BASELINE_FILE, None)
    shares_now = {h["code"]: h["shares"] for h in m["per_holding"]}
    structure_changed = False
    if baseline is None:
        baseline = {
            "created": today.isoformat(),
            "plan": "live-disposal-plan-2026-10-11.html",
            "sector": args.sector,
            "holdings": shares_now,
            "sector_pct": m["sector_pct"],
            "note": "处置方案发布时的基线（未减仓）",
        }
        if not args.dry_run:
            BASELINE_FILE.write_text(
                json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        print(f"📌 基线已建立：{baseline['created']} {shares_now} 跟踪板块占比 {m['sector_pct']}%")
        return 0
    if shares_now != baseline.get("holdings"):
        structure_changed = True

    holidays, cal_ok = load_calendar()
    try:
        base_date = date.fromisoformat(baseline.get("created", today.isoformat()))
    except ValueError:
        base_date = today
    idle_days, cal_note = trading_days_between(base_date, today, holidays, cal_ok)

    # 3) 三条触发器
    fired: list[str] = []
    if m["sse_close"] is None:
        print("⚠️ M1 判不了：上证指数取数失败 → 本次不据此触发（不用旧值/臆造值冒充）")
    if m["m1_break"]:
        fired.append(
            f"M1 市场级破位：上证指数 {m['sse_close']:.2f} ≤ 关注点 {SSE_FOCUS_LEVEL:.0f}"
            f"（{sse_date} 收盘，距关注点 {m['sse_dist']:+.2f} 点）→ 按处置方案 T+1 限价执行"
            f"（冲高减、不砸跌），目标板块占比 ≤50%"
        )
    if m["deep_loss"]:
        d = m["deep_loss"][0]
        fired.append(f"T1 深度亏损：{d['name']}({d['code']}) {d['pnl_pct'] * 100:+.1f}% ≤ -35%")
    if m["t2_weak"]:
        fired.append(
            f"T2 板块持续弱：{args.sector} RPS 连续 {len(m['rps_tail'])} 日落于 {RPS_WEAK} 以下"
            f"（最近 {m['rps_tail']}）"
        )
    if not structure_changed and m["sector_pct"] > CONCENTRATION_MAX and idle_days >= T3_IDLE_DAYS:
        fired.append(
            f"T3 无动作+高集中：{args.sector} 占比 {m['sector_pct']}% ＞ {CONCENTRATION_MAX}%，"
            f"且距基线 {idle_days} 个{cal_note}未减仓（≥{T3_IDLE_DAYS}）"
        )

    # 4) 输出
    print(
        f"📊 跟踪板块 {args.sector}：占比 {m['sector_pct']}%（¥{m['sector_value']:,.0f}）"
        f"｜总资产 ¥{m['total_assets']:,.2f}"
    )
    print(
        f"   RPS 最新 {m['rps_latest']}（历史 {len(series)} 个 report_date）"
        f"｜距基线 {idle_days} 个{cal_note}"
        f"｜结构变化={structure_changed}"
    )
    if m["sse_close"] is None:
        print(f"   M1 上证关注点 {SSE_FOCUS_LEVEL:.0f}：取数失败（判不了）")
    else:
        flag = "🔴 已破" if m["m1_break"] else "🟢 未破"
        print(
            f"   M1 上证关注点 {SSE_FOCUS_LEVEL:.0f}：{sse_date} 收 {m['sse_close']:.2f}"
            f"（距 {m['sse_dist']:+.2f} 点）→ {flag}"
        )
    for h in m["per_holding"]:
        pct = f"{h['pnl_pct'] * 100:+.1f}%" if h["pnl_pct"] is not None else "n/a"
        print(
            f"   {h['code']:8s} {h['name'][:14]:16s} {h['shares']:>7d}股 市值¥{h['mv']:>10,.0f} 盈亏{pct}"
        )

    if structure_changed:
        print("✅ 检测到结构变化（已减仓）→ 基线刷新，计时重置")
        if not args.dry_run:
            baseline.update(
                {
                    "created": today.isoformat(),
                    "holdings": shares_now,
                    "sector_pct": m["sector_pct"],
                    "note": "上次基线后检测到结构变化，已重置",
                }
            )
            BASELINE_FILE.write_text(
                json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        return 0

    # 🔴 T1/T2 与「处置目标是否达成」互相独立：**先报触发，再谈退休**
    #    （原实现把退休判定放在触发之前 → 集中度低时 -40% 的深度亏损被静默吞掉）
    if not fired and m["sector_pct"] <= 50.0:
        print(
            f"🎉 {args.sector} 占比已 ≤50% → 处置目标达成，**本触发器可退休**（暂停/删除对应自动化）"
        )
        return 0

    if not fired:
        print("🟢 四条触发器均未触发（T1/T2/T3/M1）")
        return 0

    print("\n🚨 触发器命中（需给明确处置结论，禁止只报红线）：")
    for f in fired:
        print(f"   · {f}")
    print("\n【处置结论口径】")
    print("   ① 若已减仓但未达标 → 按方案缺口继续减至 ≤50%；")
    print(
        "   ② 若完全未动作 → 复核 .workbuddy/docs/live-disposal-plan-2026-10-11.md 的方案（推荐 B：清华天+减长电100），"
    )
    print("      并按 T+1 限价单执行（引擎提示：冲高减、不砸跌）；")
    print("   ③ 若因基本面/消息面改变判断 → 请显式记录理由并更新方案文档（否则按纪律执行）。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
