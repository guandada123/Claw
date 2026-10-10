#!/usr/bin/env python3
"""weekly_radar_sort.py — 统一巡检中枢 v1.10 · 周度「统一排序」（技术雷达四环）

把「统一优化」改成「统一**排序**」：
  - 周度只做 **评审 + 排优先级**（Adopt/Trial/Assess/Caution 四环 + 升降环记录）
  - 落地仍**逐条增量**（每次一处 —— 归因清晰、回滚精确）；**禁止**把一周发现攒起来一起上

对照（业界同构）：
  - Thoughtworks 技术雷达：四环 + 记录升降环；**发现节奏 ≠ 采纳节奏**（半年一版快照）
  - Karpathy AutoResearch：每次只改一处，不是三处、不是重写
  - Renovate：低危 automerge / 高危单独人工批 → 这里体现为 Trial 可走自动文档通道、Caution 必须人工批

用法：
  python3 weekly_radar_sort.py --json              # 只读排环 + 出报告（dry-run）
  python3 weekly_radar_sort.py --apply --json      # 写回 ring + evolution_radar + 报告

退出码：0=跑完；2=registry 不可读（不得当成"本周无待排序项"）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from discovery_schema import (  # noqa: E402
    RADAR_KEY,
    RINGS,
    RISK_OK,
    VALUE_HIGH,
    backup_registry,
    claw_root,
    has_metric,
    is_real_project,
    load_registry,
    now_iso,
    real_project_share,
    save_registry_atomic,
    today_iso,
)

SCRIPT = Path(__file__).resolve()
CLAW = claw_root(__file__)
DEFAULT_REGISTRY = CLAW / ".workbuddy" / "inspection_hub" / "registry.json"
RADAR_DIR = CLAW / ".workbuddy" / "inspection_hub" / "radar"

RING_ORDER = {r: i for i, r in enumerate(RINGS)}  # Adopt(0) 最好 → Caution(3) 最差


def assign_ring(c: dict) -> tuple[str, str]:
    """确定性排环。返回 (ring, 理由)。判断顺序即优先级，避免"两个条件都对"的歧义。"""
    linked = bool(str(c.get("url") or "").strip())
    if not linked:
        return "Caution", "来源缺失（防幻觉底线：无来源不进任何采纳环）"
    if c.get("risk") not in RISK_OK:
        return "Caution", f"风险={c.get('risk')} → 高危单独人工批（对照 Renovate）"
    value_ok = c.get("value") in VALUE_HIGH
    metric = has_metric(c)
    real = is_real_project(c.get("target_project"))
    if value_ok and metric and real:
        return "Adopt", "真实项目 + 有可判定指标 + 低危 → 可直接进落地通道"
    if value_ok and metric and c.get("track") == "project_signal":
        return "Adopt", "自身系统信号 + 有指标 + 低危 → 可直接进落地通道"
    if value_ok and metric:
        return "Trial", "技能元层 + 有指标 → 限文档类通道试用"
    if value_ok and not metric:
        return "Assess", "价值够但**无可判定指标** → 先评估/补指标，不进落地通道"
    return "Assess", f"价值={c.get('value')} → 仅纳入观察"


def movement(prev: str | None, cur: str) -> str:
    if prev not in RING_ORDER or prev is None:
        return "new"
    if prev == cur:
        return "flat"
    return "promote" if RING_ORDER[cur] < RING_ORDER[prev] else "demote"


def week_tag(today: str) -> str:
    import datetime as _dt

    d = _dt.date.fromisoformat(today)
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--today", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rp = Path(args.registry)
    reg = load_registry(rp)
    if "_error" in reg:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": reg["_error"],
                    "note": "退出码 2 = 输入不可读，**不等于「本周无待排序项」**",
                },
                ensure_ascii=False,
            )
        )
        return 2

    today = args.today or today_iso()
    tag = week_tag(today)
    cands = reg.get("discovery_candidates", []) or []

    # 待排序集合：未落地的活跃候选（pending/approved/proposed/needs_review）
    target_status = ("pending", "approved", "proposed", "proposal_new_capability")
    pool = [
        c
        for c in cands
        if str(c.get("status", "")) in target_status or c.get("needs_review") is True
    ]

    rows, movements = [], []
    for c in pool:
        ring, why = assign_ring(c)
        prev = c.get("ring")
        mv = movement(prev, ring)
        rows.append(
            {
                "id": c.get("id"),
                "skill": c.get("skill"),
                "target_project": c.get("target_project"),
                "track": c.get("track"),
                "value": c.get("value"),
                "risk": c.get("risk"),
                "status": c.get("status"),
                "metric": c.get("success_metric") or c.get("check_cmd") or None,
                "ring": ring,
                "ring_prev": prev,
                "movement": mv,
                "why": why,
            }
        )
        if mv in ("promote", "demote", "new"):
            movements.append({"id": c.get("id"), "from": prev, "to": ring, "movement": mv})

    counts = {r: sum(1 for x in rows if x["ring"] == r) for r in RINGS}
    adopt = [x for x in rows if x["ring"] == "Adopt"]
    caution = [x for x in rows if x["ring"] == "Caution"]
    no_metric = [x for x in rows if x["ring"] == "Assess" and not x["metric"]]
    share = real_project_share(pool)

    # ---- 周报正文（"价值结论"式，而非"候选清单"式）----
    move_txt = "、".join(f"{m['id']} {m['from']}→{m['to']}" for m in movements) or "无"
    lines = [
        f"# 技能演进 · 周度统一排序（{tag}）",
        "",
        f"- 待排序：**{len(pool)}** 条｜真实项目锚点：**{share['real_project']}** 条"
        f"（占比 {share['real_project_share'] * 100:.0f}%）",
        f"- 四环：Adopt {counts['Adopt']} / Trial {counts['Trial']} /"
        f" Assess {counts['Assess']} / Caution {counts['Caution']}",
        f"- 升降环：{len(movements)} 条变动（{move_txt}）",
        "",
        "## 本周结论",
        f"- 可**立即进落地通道**（Adopt）：{len(adopt)} 条"
        + (
            ""
            if adopt
            else " —— 本周没有『带可判定指标的真实项目项』，这是发现面的缺口，不是好消息"
        ),
        f"- **缺指标**被拦在 Assess：{len(no_metric)} 条"
        + ("（药方：在每日扫描里补 success_metric，否则永远进不了落地通道）" if no_metric else ""),
        f"- 高危/无来源 Caution：{len(caution)} 条（需人工批）",
        "",
        "## 排序明细",
        "| 环 | id | 锚点 | 轨道 | 价值/风险 | 指标 | 变动 |",
        "|---|---|---|---|---|---|---|",
    ]
    for x in sorted(rows, key=lambda r: RING_ORDER[r["ring"]]):
        lines.append(
            f"| {x['ring']} | `{x['id']}` | {x['target_project']} | {x['track']} |"
            f" {x['value']}/{x['risk']} | {(x['metric'] or '—')[:40]} | {x['movement']} |"
        )
    lines += [
        "",
        "## 纪律",
        "- 本周只**排序**，不批量落地：落地仍逐条增量（每次一处 → 归因清晰、回滚精确）。",
        "- Adopt 走「📥 每日·文档类落地」；Trial 限文档类；Caution 必须人工批。",
        "- 落地后 N 天由 `impact_recheck.py` 回查 `success_metric`（相对数值基线提升，而非「看起来更好了」）。",
    ]
    push_md = "\n".join(lines)

    radar_hist_entry = {
        "week": tag,
        "ts": now_iso(),
        "pool": len(pool),
        "counts": counts,
        "movements": movements,
        "real_project_share": share,
        "no_metric_in_assess": len(no_metric),
    }

    out = {
        "ok": True,
        "today": today,
        "week": tag,
        "pool": len(pool),
        "counts": counts,
        "movements": movements,
        "real_project_share": share,
        "rows": rows,
        "push_markdown": push_md,
        "applied": bool(args.apply),
    }

    if args.apply:
        bak = backup_registry(rp, "pre-radar")
        out["backup"] = str(bak) if bak else None
        if bak is None:
            out["ok"] = False
            out["error"] = "备份失败 → 中止（绝不无备份写盘）"
        else:
            for c in pool:
                c["ring_prev"] = c.get("ring")
                c["ring"] = next(x["ring"] for x in rows if x["id"] == c.get("id"))
            radar = reg.setdefault(RADAR_KEY, {})
            radar["updated_at"] = now_iso()
            radar["rings"] = {r: [x["id"] for x in rows if x["ring"] == r] for r in RINGS}
            hist = radar.setdefault("history", [])
            hist.append(radar_hist_entry)
            radar["history"] = hist[-52:]
            radar.setdefault(
                "note",
                "周度排序状态：发现节奏 ≠ 采纳节奏。Adopt/Trial/Assess/Caution 四环 + 升降环记录；"
                "落地仍逐条增量（禁批量落地）。",
            )
            RADAR_DIR.mkdir(parents=True, exist_ok=True)
            rpt = RADAR_DIR / f"radar_{tag}.md"
            rpt.write_text(push_md + "\n", encoding="utf-8")
            out["report"] = str(rpt)
            reg.setdefault("_state_sync_notes", []).append(
                f"{now_iso()} 周度统一排序（{tag}）：待排序 {len(pool)}｜四环 {counts}｜升降环 {len(movements)}"
            )
            if not save_registry_atomic(rp, reg):
                out["ok"] = False
                out["error"] = "回写失败"

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"[radar] {tag}｜待排序 {len(pool)}｜四环 {counts}｜升降环 {len(movements)}")
        for x in sorted(rows, key=lambda r: RING_ORDER[r["ring"]]):
            print(
                f"  · {x['ring']:<7} {x['id']} [{x['target_project']}] {x['value']}/{x['risk']} — {x['why']}"
            )
        if not args.apply:
            print("[radar] （dry-run；加 --apply 写回 ring + 雷达状态）")
    return 0 if out["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
