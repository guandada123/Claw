#!/usr/bin/env python3
"""
drill_assistant_monitor.py — 助理实盘监控 端到端演练（默认不发送）

复刻自动化 automation-1784039316540 的 PHASE3 / 3.5 / 4 判定与卡片拼装逻辑，
用真实组件输出预览推送内容，**不实际发送飞书（防噪声）**。

用途：
  - 改完脚本/规则后，先在真实行情下演练一遍，确认链路串通、卡片拼装正确
  - 验证触发条件（止损/止盈/异动/北向）是否按预期命中

用法（在 Claw 根目录或任意目录均可）：
  python3 scripts/drill_assistant_monitor.py                 # 预览，不发送
  python3 scripts/drill_assistant_monitor.py --send          # 真发飞书（验证卡片）
  python3 scripts/drill_assistant_monitor.py --days 10       # 北向多查 10 日
  python3 scripts/drill_assistant_monitor.py --portfolio 路径
  python3 scripts/drill_assistant_monitor.py --claw /path/to/Claw

退出码：0 正常；组件缺失/解析失败会在预览中标注但仍退出 0（演练不阻断）。
"""
import argparse
import datetime
import json
import os
import subprocess
import sys

# ── 路径自适配 ──
# CLAW：优先用环境变量，其次取本脚本上级目录（scripts/ 的父目录即 Claw 根）
DEFAULT_CLAW = os.environ.get("CLAW") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.environ.get("PYTHON") or "/Users/guan/.workbuddy/binaries/python/envs/default/bin/python"

# 组件候选目录：主脚本在 scripts/，内部脚本在 .workbuddy/scripts/
def _candidates(*names):
    return [os.path.join(DEFAULT_CLAW, d, n) for d in ("scripts", ".workbuddy/scripts") for n in names]

HOLDINGS_SCRIPT = next((p for p in _candidates("fetch_holdings_quotes.py") if os.path.exists(p)), None)
NORTH_SCRIPT = next((p for p in _candidates("fetch_northbound_flow.py") if os.path.exists(p)), None)
ADVISOR_SCRIPT = next((p for p in _candidates("advisor_rules.py") if os.path.exists(p)), None)
PUSH_SH = next((p for p in _candidates("push_feishu.sh") if os.path.exists(p)), None)


def _run(script, *args, claw=DEFAULT_CLAW):
    """运行组件并捕获 stdout。失败返回 (None, stderr)。"""
    try:
        r = subprocess.run([PY, script, *args], cwd=claw,
                           capture_output=True, text=True, timeout=60)
    except Exception as e:  # noqa
        return None, str(e)
    if r.returncode != 0:
        return None, r.stderr.strip()[:300]
    return r.stdout, r.stderr.strip()


def _load_json(out, label):
    if out is None:
        return None, f"{label}: 运行失败"
    try:
        return json.loads(out), None
    except json.JSONDecodeError as e:
        return None, f"{label}: JSON 解析失败 ({e})"


def main():
    ap = argparse.ArgumentParser(description="助理实盘监控 端到端演练（默认不发送）")
    ap.add_argument("--claw", default=DEFAULT_CLAW, help="Claw 项目根目录")
    ap.add_argument("--portfolio", default=None, help="持仓 JSON 路径（默认 .workbuddy/data/user/portfolio.json）")
    ap.add_argument("--days", type=int, default=5, help="北向历史查询天数（默认 5）")
    ap.add_argument("--send", action="store_true", help="实际推送飞书（默认仅预览）")
    ap.add_argument("--ttl", type=int, default=360, help="推送卡片 TTL（分钟，默认 360）")
    args = ap.parse_args()

    claw = args.claw
    portfolio = args.portfolio or os.path.join(claw, ".workbuddy", "data", "user", "portfolio.json")

    now = datetime.datetime.now().strftime("%H:%M")
    today = datetime.datetime.now().strftime("%Y%m%d")

    print("=" * 60)
    print(f"📊 炒股助理 盘中监控 — 端到端演练预览  {now}")
    print(f"   模式: {'真发飞书' if args.send else '仅预览(不发送)'} | 北向天数: {args.days}")
    print("=" * 60)

    # ── 组件可用性自检 ──
    missing = [n for n, p in (("fetch_holdings_quotes", HOLDINGS_SCRIPT),
                              ("fetch_northbound_flow", NORTH_SCRIPT),
                              ("advisor_rules", ADVISOR_SCRIPT)) if not p]
    if missing:
        print(f"⚠️ 缺失组件: {', '.join(missing)}（请确认 Claw 根目录与脚本路径）")
    if args.send and not PUSH_SH:
        print("⚠️ --send 已指定但 push_feishu.sh 未找到，将降级为预览")
        args.send = False

    # ── PHASE1 真实组件 ──
    quotes, q_err = (None, None)
    if HOLDINGS_SCRIPT:
        out, err = _run(HOLDINGS_SCRIPT, claw=claw)
        quotes, q_err = _load_json(out, "fetch_holdings_quotes")
        if q_err:
            print(f"  {q_err}")
    north, n_err = (None, None)
    if NORTH_SCRIPT:
        out, err = _run(NORTH_SCRIPT, "--days", str(args.days), claw=claw)
        north, n_err = _load_json(out, "fetch_northbound_flow")
        if n_err:
            print(f"  {n_err}")
    rules, a_err = (None, None)
    if ADVISOR_SCRIPT:
        out, err = _run(ADVISOR_SCRIPT, "diagnose", "--portfolio", portfolio, claw=claw)
        rules, a_err = _load_json(out, "advisor_rules")
        if a_err:
            print(f"  {a_err}")

    if not quotes or "quotes" not in quotes:
        print("❌ 持仓行情为空，无法演练（PHASE2 数据完整性未通过）")
        return 1
    quotes = quotes["quotes"]

    # 纪律引擎映射
    rule_map = {}
    if isinstance(rules, list):
        rule_map = {r.get("code"): r for r in rules}

    # 总资产（用于 INFO 卡片）
    total_assets = None
    try:
        with open(portfolio) as f:
            pf = json.load(f)
        total_assets = pf.get("summary", {}).get("total_assets")
    except Exception:  # noqa
        pass
    if not total_assets:
        total_assets = sum(q.get("current_price", 0) * q.get("shares", 0) for q in clean_quotes)

    nb_ok = north.get("ok") if isinstance(north, dict) else None
    nb_flow = north.get("net_flow") if isinstance(north, dict) else None
    print(f"持仓 {len(quotes)} 只 | 北向 ok={nb_ok} net_flow={nb_flow}")
    print("-" * 60)

    # ── 价格防错隔离（2026-08-07 落地，根因=8/6早报选股价数量级错误）──
    # fetch_holdings_quotes 已对每只 current_price 做 sanity 校验，
    # price_sanity_fail=true 的标的不得参与盈亏/止损判定，需单独隔离告警。
    sanity_failed = [q for q in quotes if q.get("price_sanity_fail")]
    if sanity_failed:
        print(f"⚠️ 价格防错：{len(sanity_failed)} 只标的现价校验失败，已隔离（不参与止损/盈亏判定）")
        for q in sanity_failed:
            rp = q.get("reliable_current_price")
            print(f"  🚫 {q.get('name')}({q.get('code')}) 现价¥{q.get('current_price')} "
                  f"不可信 → 可信价¥{rp}" + (f" [{q['price_sanity']['fail_reasons'][0]}]" if q.get("price_sanity", {}).get("fail_reasons") else ""))
        print("-" * 60)

    clean_quotes = [q for q in quotes if not q.get("price_sanity_fail")]

    # ── PHASE3 分级 ──
    alerts, infos, silent = [], [], []
    for q in clean_quotes:
        pct = q.get("change_pct", 0) or 0
        if pct <= -8 or pct >= 5:
            alerts.append(q)
        elif abs(pct) >= 3:
            infos.append(q)
        else:
            silent.append(q)

    def discipline(code):
        r = rule_map.get(code, {})
        lines = [f["reason"] for f in r.get("flags", []) if isinstance(f, dict)]
        rr = r.get("risk_reward", {}) or {}
        if rr.get("warn"):
            lines.append(rr["warn"])
        return lines, r.get("has_block", False)

    def push(title, body):
        if args.send and PUSH_SH:
            try:
                subprocess.run(["bash", PUSH_SH, title, body], cwd=claw,
                               capture_output=True, text=True, timeout=60)
                print(f"  ✅ 已推送飞书: {title}")
            except Exception as e:  # noqa
                print(f"  ⚠️ 推送失败: {e}")
        else:
            print("  (预览，未发送)")

    triggered = 0

    # ── PHASE3 + 3.5 ALERT ──
    for q in alerts:
        code, name = q.get("code"), q.get("name")
        pct = q.get("change_pct", 0)
        pnl = q.get("pnl_pct", 0)
        mv = q.get("current_price", 0) * q.get("shares", 0)
        kind = "止损" if pct <= -8 else "止盈"
        disc, has_block = discipline(code)
        advice = "🚨 紧急减仓（纪律引擎标记）" if has_block else "关注，未触硬性纪律红线"

        title = "助理止损止盈"
        body = (f"📊炒股助理【止损止盈告警】{now}\n"
                f"🚨 {name}({code}) 触发{kind} | 盈亏{pnl:.2f}%\n"
                f"现价 ¥{q.get('current_price')} | 成本 ¥{q.get('avg_cost')}\n"
                f"当日 {pct:+.2f}% | 市值¥{mv:.0f}\n"
                f"💼 建议：{advice}")
        if disc:
            body += "\n📋 纪律标记：\n" + "\n".join(f"  {d}" for d in disc)

        print(f"【ALERT推送】📊炒股助理【止损止盈告警】{now}")
        print(f"🚨 {name}({code}) 触发{kind} | 盈亏{pnl:.2f}%")
        print(f"现价 ¥{q.get('current_price')} | 成本 ¥{q.get('avg_cost')}")
        print(f"当日 {pct:+.2f}% | 市值¥{mv:.0f} | 建议：{advice}")
        if disc:
            print("📋 纪律标记：")
            for d in disc:
                print(f"  {d}")
        push(title, body)
        print("-" * 60)
        triggered += 1

    # ── PHASE3 INFO ──
    for q in infos:
        code, name = q.get("code"), q.get("name")
        pct = q.get("change_pct", 0)
        pnl = q.get("pnl_pct", 0)
        mv = q.get("current_price", 0) * q.get("shares", 0)
        title = "助理持仓异动"
        body = (f"📊炒股助理【持仓异动】{now}\n"
                f"📈 {name}({code}) ¥{q.get('current_price')} 当日{pct:+.2f}% 盈亏{pnl:.2f}%\n"
                f"💼 市值¥{mv:.0f} | 总资产¥{total_assets:.0f}")
        print(f"【INFO推送】📊炒股助理【持仓异动】{now}")
        print(f"📈 {name}({code}) ¥{q.get('current_price')} 当日{pct:+.2f}% 盈亏{pnl:.2f}%")
        print(f"💼 市值¥{mv:.0f} | 总资产¥{total_assets:.0f}")
        push(title, body)
        print("-" * 60)
        triggered += 1

    # 全程静默的持仓
    for q in silent:
        print(f"  SILENT: {q.get('name')}({q.get('code')}) 当日 {q.get('change_pct',0):+.2f}%")

    # ── PHASE4 北向 ──
    nf = nb_flow
    cond_single = nf is not None and abs(nf) > 30
    nb_consec_days = north.get("consecutive_days", 0) if isinstance(north, dict) else 0
    nb_consec_sum = north.get("consecutive_sum", 0) if isinstance(north, dict) else 0
    cond_consec = isinstance(north, dict) and nb_consec_days >= 3 and abs(nb_consec_sum) > 80
    if cond_single or cond_consec:
        # 取今日沪/深分项（若有）
        sh = sz = None
        if isinstance(north, dict):
            rec = north.get("recent") or []
            if rec:
                today_rec = rec[-1]
                sh, sz = today_rec.get("sh"), today_rec.get("sz")
        title = "北向资金"
        body = (f"📊炒股助理【北向资金】{now}\n"
                f"今日净流入/流出：{nf}亿")
        if sh is not None and sz is not None:
            body += f"\n沪股通：{sh}亿 | 深股通：{sz}亿"
        if cond_consec:
            body += f"\n连续 {nb_consec_days} 日同向累计 {nb_consec_sum}亿"
        print(f"【北向推送】📊炒股助理【北向资金】{now}")
        print(f"今日净流入/流出：{nf}亿" + (f" (沪{sh}/深{sz})" if sh is not None else ""))
        if cond_consec:
            print(f"连续 {nb_consec_days} 日同向累计 {nb_consec_sum}亿")
        push(title, body)
        print("-" * 60)
        triggered += 1
    elif isinstance(north, dict) and north.get("discontinued"):
        print("北向：DISCONTINUED (自2024-05停止披露，跳过该维度)")
    else:
        cd = north.get("consecutive_days", 0) if isinstance(north, dict) else 0
        cs = north.get("consecutive_sum", 0) if isinstance(north, dict) else 0
        print(f"北向：SILENT (ok={nb_ok}, 当日={nf}, 连续={cd}日累计{cs})")

    print("=" * 60)
    print(f"✅ 演练完成 | 触发推送 {triggered} 条 | SILENT 持仓 {len(silent)} 只")
    print("链路验证：fetch_holdings_quotes "
          f"{'✅' if quotes else '❌'} | fetch_northbound_flow "
          f"{'✅' if north else '❌'} | advisor_rules "
          f"{'✅' if rules else '❌'} | 判定+卡片拼装 ✅")
    if not args.send:
        print("⚠️ 飞书实际发送未执行（防噪声）；真实盘中窗口将由自动化自动发送")
    return 0


if __name__ == "__main__":
    sys.exit(main())
