"""
signal_verify_pipeline.py — 公众号信号溯源 v4 编排（步骤2~3）
  - 读取 signal_verify_report.json
  - 与上周快照比较各公众号胜率波动（<5% → 静默）
  - 写回历史快照（signal_verify_history.json）
  - 生成飞书推送 markdown 到 /tmp/signal_verify_msg.md
  - 打印决策 JSON：{decision, max_delta, changed, msg_file}
"""
import json
import pathlib

ROOT = pathlib.Path("/Users/guan/WorkBuddy/Claw")
REPORT = ROOT / ".workbuddy" / "data" / "signal_verify_report.json"
HISTORY = ROOT / ".workbuddy" / "data" / "signal_verify_history.json"
MSG_FILE = pathlib.Path("/tmp/signal_verify_msg.md")
CHAT_ID = "oc_9ee5303497f5e0e71666b610d6bdc346"

THRESHOLD = 5.0  # 胜率波动阈值（百分点）


def main():
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    today = report["trade_date"]
    overall = report["overall"]
    ranking = report["ranking"]

    cur = {x["account"]: x["win_rate"] for x in ranking}
    cur_overall = overall["win_rate"]

    hist = {}
    if HISTORY.exists():
        try:
            hist = json.loads(HISTORY.read_text(encoding="utf-8"))
        except Exception:
            hist = {}
    snapshots = hist.get("snapshots", [])
    last = snapshots[-1] if snapshots else None

    decision = "push"
    max_delta = 999.0
    changed = []
    if last:
        last_win = last.get("per_account", {})
        deltas = []
        for a, wr in cur.items():
            if wr is None:
                continue
            pw = last_win.get(a)
            if pw is None:
                changed.append(a)
                deltas.append(999.0)
            else:
                d = abs(wr - pw)
                deltas.append(d)
                if d >= THRESHOLD:
                    changed.append(a)
        max_delta = max(deltas) if deltas else 999.0
        if max_delta < THRESHOLD:
            decision = "silent"

    # 保存快照
    snap = {"date": today, "overall_win": cur_overall, "per_account": cur}
    snapshots.append(snap)
    hist["snapshots"] = snapshots[-12:]
    HISTORY.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")

    # 生成推送文案
    msg = build_message(report)
    MSG_FILE.write_text(msg, encoding="utf-8")

    print(json.dumps({
        "decision": decision,
        "max_delta": round(max_delta, 1),
        "changed": changed,
        "chat_id": CHAT_ID,
        "msg_file": str(MSG_FILE),
        "overall_win": cur_overall,
        "verify_cov": overall["verify_cov"],
    }, ensure_ascii=False))
    return decision


def build_message(report):
    o = report["overall"]
    lines = []
    lines.append("📚【知识库】公众号信号溯源")
    lines.append("")
    lines.append(f"**行情验证总览**（{report['trade_date']}）")
    lines.append(f"- 信号总量：{o['total']} 条，已验证：{o['verified']} 条（覆盖 {o['verify_cov']}%）")
    lines.append(f"- 看多信号命中率：**{o['win_rate']}%**（命中 {o['hits']}/{o['with_return']}）")
    lines.append(f"- 已验证信号平均收益：**{o['avg_return']}%**（自推荐日起算，前复权）")
    lines.append("")
    lines.append("**各公众号排名（按命中率）**")
    for x in report["ranking"]:
        wr = "—" if x["win_rate"] is None else f"{x['win_rate']}%"
        ar = "—" if x["avg_return"] is None else f"{x['avg_return']}%"
        cov = "—" if x["win_rate"] is None else f"{x['verify_cov']}%"
        lines.append(f"▸ {x['account']}：信号{x['total']} / 验证{x['verified']}({cov}) / 命中率{wr} / 均收益{ar}")
    lines.append("")
    lines.append("⚠️ 掌门小才女、股德猫停 信号均来自 2019–2020，超出验证窗口(>1年)未计入命中率。")
    lines.append("📊 行情验证结果已更新（实时行情：腾讯；历史收益：新浪日线）")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
