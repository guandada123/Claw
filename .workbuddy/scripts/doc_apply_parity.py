#!/usr/bin/env python3
"""doc_apply_parity.py — 统一巡检中枢 · 校准期「对拍校验器」

用途：把 PA-001（校准期 → 生效期）的三条判据里**可机器验证的两条**变成确定性检查，
     让 2026-10-08 的切换不再是"人工翻 14 份报告"，而是一条命令给结论。

判据（对应 registry.doc_apply.switch_criteria）：
  ① 时长：校准满 N 天（默认 14，读 calibrate_start / calibrate_until）
  ② 对拍 0 越界：账本里每次 calibrate 的每个 selected.target 都必须落在白名单
     （references/*.md | README.md | CHANGELOG.md）；出现任一越界 → 不通过
  ③ 人工抽检 0 反例：**无法机器判定**（"真写进去会不会更糟"是人的判断）
     → 本脚本只输出抽检样本清单，判据本身仍留给人

铁律：只读 + 只打印，不写 registry、不碰 skill 文档。

退出码（三次审计 2026-09-24 明确，**切换守卫直接依赖这个语义**）：
  0 = 通过且 0 越界（**仍须 `ok==true`**，见下）
  1 = 有越界（判据②不通过）→ 不得切换
  2 = **输入不可读/不可信**（registry / 账本读不出来）→ 不得切换
      ⚠️ 这条是三次审计 S1 的核心：原实现读不出 registry 时 **return 0**，
         而 payload 里连 `violations` 键都没有 —— 守卫的 `violations == []` 求值成 undefined，
         等价于"没有越界"，于是**错误被当成了通过**，可能放行生产 mode 切换。
      现在：输入不可信 → 显式把每个可求值键置 None 并置 `ok:false`、退出码 2。
      铁律：**错误 ≠ 通过；缺键 ≠ 空集。**
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve()
CLAW = SCRIPT.parents[2]
DEFAULT_REGISTRY = CLAW / ".workbuddy" / "inspection_hub" / "registry.json"
LEDGER = CLAW / ".workbuddy" / "inspection_hub" / "calibration" / "parity.json"

# 与 apply_doc_candidates.py 保持同一白名单口径
WHITELIST_RE = re.compile(r"^(references/[A-Za-z0-9._/\-]+\.md|README\.md|CHANGELOG\.md)$")
DEFAULT_DAYS = 14


def load_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"_error": str(e)}


def parse_days(reg: dict) -> int:
    da = reg.get("doc_apply", {}) or {}
    try:
        start = datetime.date.fromisoformat(str(da.get("calibrate_start")))
        until = datetime.date.fromisoformat(str(da.get("calibrate_until")))
        return max(1, (until - start).days)
    except Exception:  # noqa: BLE001
        return DEFAULT_DAYS


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    ap.add_argument("--ledger", default=str(LEDGER))
    ap.add_argument("--sample", type=int, default=3, help="给人工抽检的样本条数")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    reg = load_json(Path(args.registry))
    if "_error" in reg:
        # ⚠️ 三次审计 S1：原实现这里 return 0（"无越界"），且不输出 violations 键。
        #    调用方（切换守卫 / 落地自动化）拿这个 payload 判 `violations == []` 会得到 undefined
        #    → 等价于通过 → 可能在证据不可信时把生产 mode 切成 live。
        #    现在：所有机器可求值键显式给 None，ok=false，退出码 2。
        print(json.dumps({
            "ok": False,
            "error": f"registry 读取失败: {reg['_error']}",
            "mode": None, "days_elapsed": None, "days_needed": None,
            "violations": None, "ledger_vs_registry_gaps": None,
            "ready_for_live_by_machine_criteria": False,
            "blockers": [f"输入不可信：registry 读取失败（{reg['_error']}）→ 不得切换，先修输入"],
            "note": "退出码 2 = 输入不可读/不可信，**绝不等于「没有越界」**",
        }, ensure_ascii=False))
        return 2

    da = reg.get("doc_apply", {}) or {}
    mode = da.get("mode", "calibrate")
    days_needed = parse_days(reg)
    today = datetime.date.today()

    # 时长
    try:
        start = datetime.date.fromisoformat(str(da.get("calibrate_start")))
        days_elapsed = (today - start).days
    except Exception:  # noqa: BLE001
        start, days_elapsed = None, 0

    # 账本
    led = load_json(Path(args.ledger))
    ledger_broken = "_error" in led
    runs = (led.get("runs", {}) or {}) if not ledger_broken else {}
    cal_runs = {k: v for k, v in runs.items() if v.get("mode") == "calibrate"}

    # 越界校验（判据②）
    violations, missing_in_registry, chosen = [], [], []
    reg_ids = {c.get("id") for c in (reg.get("discovery_candidates", []) or [])}
    for key, run in sorted(cal_runs.items()):
        for s in run.get("selected", []) or []:
            tgt = s.get("target") or ""
            if not WHITELIST_RE.match(tgt):
                violations.append({"run": key, "id": s.get("id"), "target": tgt or "(空)",
                                   "why": "target 越出白名单（应仅 references/*.md|README.md|CHANGELOG.md）"})
            if s.get("id") and s["id"] not in reg_ids:
                missing_in_registry.append({"run": key, "id": s["id"], "why": "账本有但 registry 无此候选"})
            chosen.append({"run": key, **s})

    # 汇总
    blockers = []
    if ledger_broken:
        blockers.append(f"输入不可信：对拍账本读取失败（{led['_error']}）→ 不得切换")
    if mode != "calibrate":
        blockers.append(f"当前 mode={mode}（非 calibrate）——本校验器服务于校准期对拍")
    elif days_elapsed < days_needed:
        blockers.append(f"时长未满：{days_elapsed}/{days_needed} 天（至 {da.get('calibrate_until')}）")
    if violations:
        blockers.append(f"对拍越界 {len(violations)} 处（判据②不通过）")
    if not cal_runs:
        blockers.append("尚无 calibrate 账本记录（parity.json 为空或未生成）")
    if missing_in_registry:
        # 三次审计 S7：账本是切换的**证据**，证据引用了 registry 里不存在的候选 → 证据不可核 → 拦住。
        # 方向恒定保守：宁可拦住一次能过的切换，也不放行一次证据不完整的切换。
        blockers.append(f"证据链不完整：账本引用了 {len(missing_in_registry)} 条 registry 里没有的候选"
                        f"（{', '.join(str(x.get('id')) for x in missing_in_registry[:5])}）→ 先对齐再切")

    # 人工抽检样本（判据③：只给样本，不给结论）
    uniq, seen = [], set()
    for c in chosen:
        if c.get("id") and c["id"] not in seen:
            seen.add(c["id"])
            uniq.append(c)
    sample = uniq[-int(args.sample):]

    ready = (not blockers) and not violations
    out = {
        "ok": not ledger_broken,
        "mode": mode,
        "days_elapsed": days_elapsed,
        "days_needed": days_needed,
        "calibrate_until": da.get("calibrate_until"),
        "calibrate_runs": len(cal_runs),
        "selected_total": len(chosen),
        "violations": violations,
        "ledger_vs_registry_gaps": missing_in_registry,
        "human_spotcheck_sample": sample,
        "ready_for_live_by_machine_criteria": ready,
        "blockers": blockers,
        "note": "判据③（人工抽检 0 反例）无法机器判定，须人对 human_spotcheck_sample 逐条回答『真写进去会不会更糟』",
        "gate_rule": "切换守卫必须先要 ok==true，再要 days_elapsed>=days_needed 且 violations==[]；缺键 ≠ 空集，错误 ≠ 通过",
    }
    if ledger_broken:
        # 账本不可读时，所有"由账本算出"的键一律置 None：让任何消费方都无法把"读不到"误读成"空"。
        # （`violations=[]` 会让 `violations == []` 判为通过 —— 错误 ≠ 空集。）
        for k in ("violations", "ledger_vs_registry_gaps", "selected_total",
                  "calibrate_runs", "human_spotcheck_sample"):
            out[k] = None
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        print(f"[parity] mode={mode} 校准 {days_elapsed}/{days_needed} 天（{da.get('calibrate_until')} 届满）")
        print(f"[parity] 账本 {len(cal_runs)} 次运行 / 累计入选 {len(chosen)} 条 / 越界 {len(violations)}")
        for b in blockers:
            print(f"  ⛔ {b}")
        if sample:
            print("[parity] 人工抽检样本：")
            for s in sample:
                print(f"  · {s.get('id')} [{s.get('skill')}] -> {s.get('target')}")
        print(f"[parity] 机器判据就绪 = {ready}" + ("（仍须人工抽检后才可切 live）" if ready else ""))
    if ledger_broken:
        return 2                      # 输入不可信 ≠ 没有越界
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
