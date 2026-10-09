#!/usr/bin/env python3
"""impact_recheck.py — 统一巡检中枢 v1.10 · 「收益度量」stage（发现回路的证真环节）

病根：落地即宣布成功 = 退化裁判。2026 年共识：**自改进只在结果可客观验证处有效**；
      中科院 SEAL 实证：智能体同时掌控「被优化的程序」与「评判它的标准」时，自测维持高分、真实部署塌到随机线。

本脚本的立场（铁律）：
  - **只认外部可验证的结论**。有 `check_cmd` → 跑命令取退出码（等价于 CI 门禁）；
    没有 → 报「需裁决」，**绝不给候选自己打勾**。
  - 未到期不查（recheck_after_days + landed_at 判due）；legacy（recheck_after_days=null）不查（避免回查洪峰）。
  - `impact=unmeasured` 的存量条目计入记分板但**不制造**回查任务。

用法：
  python3 impact_recheck.py --json                 # 只读：列出到期条目与结论（dry-run）
  python3 impact_recheck.py --apply --json         # 写回 impact / value_scoreboard（写前备份）
  python3 impact_recheck.py --today 2026-10-20     # 指定"今天"（测试用）

退出码：0=跑完（含"无到期"）；2=registry 不可读（不得当成"没有到期"）
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from discovery_schema import (  # noqa: E402
    SCOREBOARD_KEY,
    backup_registry,
    claw_root,
    days_between,
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


def due_candidates(cands: list[dict], today: str) -> list[dict]:
    out = []
    for c in cands:
        if not str(c.get("status", "")).startswith("applied"):
            continue
        if c.get("impact") not in (None, ""):      # 已裁决过的（含 legacy unmeasured）不再到期
            continue
        land = c.get("landed_at")
        rdays = c.get("recheck_after_days")
        if not land or not isinstance(rdays, int):
            continue
        d = days_between(land, today)
        if d is not None and d >= rdays:
            out.append(c)
    return out


def run_check_cmd(cmd: str, timeout: int) -> tuple[str, str, str]:
    """跑确定性检查命令。返回 (impact, note, raw_tail)。stdout+stderr 合并（不合并 = 日志静默丢一半）。"""
    try:
        p = subprocess.run(  # noqa: S602  # nosec B602 - 命令来自本机 registry 的 check_cmd 声明，非外部输入
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        raw = ((p.stdout or "") + (p.stderr or "")).strip()
        tail = "\n".join(raw.splitlines()[-8:])
        if p.returncode == 0:
            return "achieved", "check_cmd rc=0", tail
        return "none", f"check_cmd rc={p.returncode}", tail
    except subprocess.TimeoutExpired:
        return "partial", f"check_cmd 超时({timeout}s) → 不能算通过", "(timeout)"
    except Exception as e:  # noqa: BLE001
        return "partial", f"check_cmd 执行异常: {e} → 不能算通过", "(error)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    ap.add_argument("--apply", action="store_true", help="写回 impact 与 value_scoreboard（默认只读）")
    ap.add_argument("--today", default=None, help="覆盖'今天'（YYYY-MM-DD，测试用）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rp = Path(args.registry)
    reg = load_registry(rp)
    if "_error" in reg:
        print(json.dumps({"ok": False, "error": reg["_error"],
                          "note": "退出码 2 = 输入不可读，**不等于「没有到期回查」**"}, ensure_ascii=False))
        return 2

    today = args.today or today_iso()
    policy = reg.get("discovery_policy", {}) or {}
    rc_cfg = policy.get("impact_recheck", {}) or {}
    allow_cmd = bool(rc_cfg.get("allow_check_cmd", True))
    timeout = int(rc_cfg.get("cmd_timeout_sec", 60))

    cands = reg.get("discovery_candidates", []) or []
    due = due_candidates(cands, today)

    checked, needs_adjudication = [], []
    for c in due:
        cmd = str(c.get("check_cmd") or "").strip()
        if cmd and allow_cmd:
            impact, note, tail = run_check_cmd(cmd, timeout)
            checked.append(
                {
                    "id": c.get("id"),
                    "target_project": c.get("target_project"),
                    "metric": c.get("success_metric") or cmd,
                    "impact": impact,
                    "note": note,
                    "output_tail": tail,
                }
            )
            if args.apply:
                c["impact"] = impact
                c["impact_checked_at"] = now_iso()
                c["impact_note"] = note
        else:
            needs_adjudication.append(
                {
                    "id": c.get("id"),
                    "target_project": c.get("target_project"),
                    "metric": c.get("success_metric") or "(未声明)",
                    "landed_at": c.get("landed_at"),
                    "why": "无 check_cmd → 必须由外部裁决（人或 agent 按外部证据判），脚本不代打勾",
                }
            )
            if args.apply:
                c["impact_note"] = "待裁决（无 check_cmd）：须按 success_metric 用库外证据判定"
                c["recheck_due_since"] = c.get("recheck_due_since") or today

    # 记分板：只统计「已裁决」结论（achieved/partial/none）；unmeasured 单列
    by_impact: dict[str, int] = {}
    by_project: dict[str, dict] = {}
    for c in cands:
        im = c.get("impact")
        if im in ("achieved", "partial", "none"):
            by_impact[im] = by_impact.get(im, 0) + 1
        tp = c.get("target_project") or "none"
        slot = by_project.setdefault(tp, {"achieved": 0, "partial": 0, "none": 0, "unmeasured": 0, "pending": 0})
        if im in ("achieved", "partial", "none"):
            slot[im] += 1
        elif im == "unmeasured":
            slot["unmeasured"] += 1
        else:
            slot["pending"] += 1

    scoreboard = {
        "updated_at": now_iso(),
        "by_impact": by_impact,
        "by_project": by_project,
        "real_project_share": real_project_share(cands),
        "legacy_unmeasured": sum(1 for c in cands if c.get("impact") == "unmeasured"),
        "note": reg.get(SCOREBOARD_KEY, {}).get("note")
        or "收益记分板：只统计可回查的结论（achieved/partial/none）。",
    }

    out = {
        "ok": True,
        "today": today,
        "due_total": len(due),
        "machine_checked": len(checked),
        "needs_adjudication": len(needs_adjudication),
        "checked": checked,
        "adjudication_list": needs_adjudication,
        "scoreboard": scoreboard,
        "applied": bool(args.apply),
    }

    # v1.10.1 修复「记分板空转」：记分板是**累计状态快照**，不是「到期事件的产物」。
    #   旧写法 `if args.apply and due:` → 无到期项就永不写 → value_scoreboard 长期为 null
    #   （实测：registry.value_scoreboard.updated_at=null、by_impact={}）→ 周报「收益结论」永远为空，
    #   与「让优化可见」的初衷正好相反。改为：只要 --apply 就写；但当「无到期项且记分板实质未变」时跳过写盘，
    #   避免每天无谓备份 + 刷 _state_sync_notes。
    if args.apply:
        prev = reg.get(SCOREBOARD_KEY) or {}
        substantive_keys = ("by_impact", "by_project", "real_project_share", "legacy_unmeasured")
        unchanged = all(prev.get(k) == scoreboard.get(k) for k in substantive_keys)
        if unchanged and not due:
            out["skipped_write"] = "记分板无变化且无到期回查 → 跳过写盘（避免空转备份）"
        else:
            bak = backup_registry(rp, "pre-impact")
            out["backup"] = str(bak) if bak else None
            if bak is None:
                out["ok"] = False
                out["error"] = "备份失败 → 中止（绝不无备份写盘）"
            else:
                reg[SCOREBOARD_KEY] = scoreboard
                if due:  # 只有真有到期回查时才留痕，避免无到期时空刷 note
                    reg.setdefault("_state_sync_notes", []).append(
                        f"{now_iso()} 收益回查：到期 {len(due)} 条；机器判定 {len(checked)}、"
                        f"待裁决 {len(needs_adjudication)}；记分板 by_impact={by_impact}"
                    )
                if not save_registry_atomic(rp, reg):
                    out["ok"] = False
                    out["error"] = "回写失败"

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"[impact] 今日 {today}｜到期 {len(due)}｜机器判定 {len(checked)}｜待裁决 {len(needs_adjudication)}")
        for c in checked:
            print(f"  · {c['id']} [{c['target_project']}] → {c['impact']}（{c['note']}）")
        for c in needs_adjudication:
            print(f"  · {c['id']} [{c['target_project']}] → 待裁决：{c['metric']}")
        print(f"[impact] 记分板 by_impact={by_impact}｜真实项目占比={scoreboard['real_project_share']['real_project_share']}")
        if not args.apply and due:
            print("[impact] （dry-run；加 --apply 写回）")
    return 0 if out["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
