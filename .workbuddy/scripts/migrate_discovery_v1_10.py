#!/usr/bin/env python3
"""migrate_discovery_v1_10.py — 统一巡检中枢 · 发现回路 v1.10 一次性迁移（schema 升级）

把「发现回路」从 v1.9「只发现不验证、只优化技能元层」升级到 v1.10：
  A. 每条候选新增目标锚点与收益度量字段（target_project / success_metric / impact …）
  B. 新增 evolution_radar（周度分环 + 升降环历史）
  C. 新增 discovery_policy（双轨 / 项目锚点 / 去重 / 高价值绕过 / 指标闸策略）
  另：doc_apply.quality_gate 增补 require_target_project / require_success_metric
  另：value_scoreboard（收益记分板）+ version → 1.10.0

铁律：
  - 写前备份 registry（copy → 校验可解析 → 保留）；绝不删任何文件
  - 幂等：已迁移（存在 discovery_policy 且候选字段齐）则只做"补缺"，不覆盖既有值
  - 只加字段、不改状态：绝不触碰候选的 status / applied 记录（历史事实不可改写）
  - 退出码：0=成功；2=registry 不可读（不得当成"迁移过了"）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from discovery_schema import (  # noqa: E402
    CANDIDATE_V110_DEFAULTS,
    META_PROJECT,
    NONE_PROJECT,
    POLICY_KEY,
    RADAR_KEY,
    REAL_PROJECTS,
    SCOREBOARD_KEY,
    apply_defaults,
    backup_registry,
    claw_root,
    load_registry,
    now_iso,
    real_project_share,
    save_registry_atomic,
    validate_candidate,
)

SCRIPT = Path(__file__).resolve()
CLAW = claw_root(__file__)
DEFAULT_REGISTRY = CLAW / ".workbuddy" / "inspection_hub" / "registry.json"
NEW_VERSION = "1.10.0"


def infer_project(c: dict) -> str:
    """既有候选项的锚点推断：既有队列 100% 落在技能元层（已实测），故除显式项目名外一律 meta。"""
    skill = str(c.get("skill") or "")
    if skill in REAL_PROJECTS:
        return skill
    if not skill:
        return NONE_PROJECT
    return META_PROJECT


def infer_track(c: dict) -> str:
    url = str(c.get("url") or "")
    if url.startswith("file://"):
        # 来源是自家产物/账本 → 项目自身信号（不是外部趋势）
        return "project_signal"
    blob = f"{c.get('source_summary','')}{c.get('suggestion','')}"
    if any(k in blob for k in ("卫生", "hygiene", "坏链", "重复段落", "prompt-injection")):
        return "hygiene"
    return "external_trend"


def build_policy(claw: Path) -> dict:
    # 路径以 $HOME 相对表达并落到 registry 数据里（脚本不写死本机绝对路径）
    return {
        "version": 1,
        "why": (
            "发现回路的病根是「发现面只盯 2 个技能 skill、落地即宣布成功」。本策略把发现面扩到真实项目、"
            "并把『优化』定义成可回查的指标（相对数值基线提升，而非看起来更好了）。"
        ),
        "tracks": {
            "external_trend": "外部同类项目/研究趋势 → 候选（target_project 常为 meta；纯趋势，不硬塞进项目）",
            "project_signal": "项目自身痛点信号（.learnings 事故 / 失败测试 / CI 红灯 / 性能回归 / TODO / 自身产物缺口）→ 候选（target_project 为真实项目，或 meta=中枢自身）",
            "hygiene": "技能卫生审计（重复/坏链/未用/prompt-injection）→ 候选",
        },
        "project_watch": {
            "note": "真实项目锚点。发现面必须盯这些项目，否则「对比现有项目统一优化」是空话。路径是数据（非脚本硬编码）。",
            "anchors": [
                {
                    "project": "Claw",
                    "root": "$HOME/WorkBuddy/Claw",
                    "learnings_dirs": ["$HOME/WorkBuddy/Claw/.learnings"],
                    "todo_globs": [
                        "$HOME/WorkBuddy/Claw/scripts/*.py",
                        "$HOME/WorkBuddy/Claw/.workbuddy/scripts/*.py",
                        "$HOME/WorkBuddy/Claw/src/**/*.py",
                    ],
                    "tests_cmd": "cd $HOME/WorkBuddy/Claw && python3 -m pytest -q --maxfail=1 tests 2>&1 | tail -20",
                },
                {
                    "project": "QTS",
                    "root": "$HOME/WorkBuddy/QuantTradingSystem",
                    "learnings_dirs": ["$HOME/WorkBuddy/QuantTradingSystem/.learnings"],
                    "todo_globs": [
                        "$HOME/WorkBuddy/QuantTradingSystem/*.py",
                        "$HOME/WorkBuddy/QuantTradingSystem/**/*.py",
                    ],
                    "tests_cmd": None,
                },
                {
                    "project": "StockInsight",
                    "root": "$HOME/WorkBuddy/StockInsight",
                    "learnings_dirs": ["$HOME/WorkBuddy/StockInsight/.learnings"],
                    "todo_globs": [
                        "$HOME/WorkBuddy/StockInsight/*.py",
                        "$HOME/WorkBuddy/StockInsight/backend/**/*.py",
                    ],
                    "tests_cmd": None,
                },
            ],
        },
        "dedup": {
            "index": ".workbuddy/inspection_hub/discovery_archive/dedup_index.json",
            "rule": "target_project|skill|归一URL(去协议/query)|归一建议前120字 → sha1；命中索引即视为重复，不再入队",
            "why": "已实测：同一候选在 applied 列表里重复落地 3 次（skill-lifecycle-registry.md）→ 去重是卫生问题不是优化",
        },
        "metric_policy": {
            "require_success_metric": True,
            "require_target_project": True,
            "allowed_targets": list(REAL_PROJECTS) + [META_PROJECT, NONE_PROJECT],
            "real_project_priority": True,
            "blocked_metric_alert_min": 3,
            "why": (
                "没有可判定指标的候选 = 无法证真的『优化』。落地通道只放行带指标的；"
                "被指标闸拦下的条数达 block_metric_alert_min 即提示（症状准×药方对：去每日扫描里补 success_metric），"
                "避免回路被静默卡死。"
            ),
        },
        "high_value_bypass": {
            "enabled": True,
            "rule": "value=high 且 risk=low 且 target_project ∈ 真实项目 且 success_metric 非空 → 不等周度排序，当日推卡",
            "why": "对照 Renovate：安全补丁绕排期立即发；高危单独人工批。发现节奏 ≠ 采纳节奏。",
        },
        "impact_recheck": {
            "default_after_days": 7,
            "allow_check_cmd": True,
            "cmd_timeout_sec": 60,
            "why": (
                "落地即宣布成功 = 退化裁判（SEAL 实证：自评维持 90 分、真实部署塌到随机线）。"
                "只认外部可验证的结论：有 check_cmd 就跑命令取退出码；没有就报『需裁决』，不许自己给自己打勾。"
            ),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    ap.add_argument("--dry-run", action="store_true", help="只打印将做什么，不写盘")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rp = Path(args.registry)
    reg = load_registry(rp)
    if "_error" in reg:
        print(json.dumps({"ok": False, "error": reg["_error"]}, ensure_ascii=False))
        return 2

    before_version = reg.get("version")
    cands = reg.get("discovery_candidates", []) or []
    changed_fields = 0
    probs_all: list[str] = []

    # ---- 1) 候选补字段（只补缺，不覆盖既有值，绝不改 status）----
    for c in cands:
        missing = [k for k in CANDIDATE_V110_DEFAULTS if k not in c]
        if missing:
            changed_fields += len(missing)
        apply_defaults(c)
        # 锚点/轨道：仅在"未声明"时推断（幂等）
        if c.get("target_project") in (None, "", NONE_PROJECT) and c.get("skill"):
            c["target_project"] = infer_project(c)
        if c.get("track") in (None, "", "external_trend") and c.get("url", "").startswith("file://"):
            c["track"] = infer_track(c)
        # 已落地但从未度量 → 标注为 legacy 未度量（可见，但不制造回查洪峰）
        if str(c.get("status", "")).startswith("applied") and c.get("impact") in (None, ""):
            c["impact"] = "unmeasured"
            c["recheck_after_days"] = None
            if not c.get("landed_at"):
                c["landed_at"] = (str(c.get("resolved_at") or "")[:10]) or None
        probs_all += [f"{c.get('id')}: {p}" for p in validate_candidate(c)]

    # ---- 2) 顶层新键（幂等：存在则不覆盖）----
    added_keys = []
    if POLICY_KEY not in reg:
        reg[POLICY_KEY] = build_policy(CLAW)
        added_keys.append(POLICY_KEY)
    if RADAR_KEY not in reg:
        reg[RADAR_KEY] = {
            "updated_at": None,
            "rings": {r: [] for r in ("Adopt", "Trial", "Assess", "Caution")},
            "history": [],
            "note": "周度排序状态：发现节奏 ≠ 采纳节奏。Adopt/Trial/Assess/Caution 四环 + 升降环记录；落地仍逐条增量（禁批量落地）。",
        }
        added_keys.append(RADAR_KEY)
    if SCOREBOARD_KEY not in reg:
        reg[SCOREBOARD_KEY] = {
            "updated_at": None,
            "by_impact": {},
            "by_project": {},
            "real_project_share": real_project_share(cands),
            "legacy_unmeasured": sum(1 for c in cands if c.get("impact") == "unmeasured"),
            "note": "收益记分板：只统计可回查的结论（achieved/partial/none）。『优化』= 指标相对基线提升，不是『看起来更好了』。",
        }
        added_keys.append(SCOREBOARD_KEY)

    # ---- 3) 指标闸（quality_gate 增补；已有键不覆盖，保留用户手改）----
    da = reg.setdefault("doc_apply", {})
    qg = da.setdefault("quality_gate", {})
    gate_added = []
    for k, v in (("require_target_project", True), ("require_success_metric", True)):
        if k not in qg:
            qg[k] = v
            gate_added.append(k)
    if "blocked_metric_alert_min" not in da:
        da["blocked_metric_alert_min"] = 3

    # ---- 4) 版本 ----
    if reg.get("version") != NEW_VERSION:
        reg["version"] = NEW_VERSION
    reg["updated_at"] = now_iso()
    reg.setdefault("_state_sync_notes", []).append(
        f"{now_iso()} v1.10 迁移：候选补 target_project/track/success_metric/impact 等字段（{len(cands)} 条）；"
        f"新增 {', '.join(added_keys) if added_keys else '(顶层键已存在)'}；"
        f"quality_gate 增补 {', '.join(gate_added) if gate_added else '(已存在)'}；version {before_version}→{NEW_VERSION}"
    )

    summary = {
        "ok": True,
        "registry": str(rp),
        "dry_run": args.dry_run,
        "version": f"{before_version} → {NEW_VERSION}",
        "candidates": len(cands),
        "fields_filled": changed_fields,
        "keys_added": added_keys,
        "gate_added": gate_added,
        "validate_problems": probs_all[:20],
        "project_share": real_project_share(cands),
    }

    if args.dry_run:
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            print(f"[migrate] DRY-RUN 版本 {summary['version']}｜候选 {len(cands)}｜补字段 {changed_fields}｜新增键 {added_keys}")
        return 0

    bak = backup_registry(rp, "pre-v1.10")
    if bak is None:
        print(json.dumps({"ok": False, "error": "备份失败 → 中止迁移（绝不无备份写盘）"}, ensure_ascii=False))
        return 2
    summary["backup"] = str(bak)

    if not save_registry_atomic(rp, reg):
        print(json.dumps({"ok": False, "error": "回写失败"}, ensure_ascii=False))
        return 2

    # 回读校验：日志成功 ≠ 数据落库
    chk = load_registry(rp)
    ok_back = (
        chk.get("version") == NEW_VERSION
        and POLICY_KEY in chk
        and RADAR_KEY in chk
        and SCOREBOARD_KEY in chk
        and all(k in (chk.get("doc_apply", {}).get("quality_gate", {})) for k in ("require_target_project", "require_success_metric"))
    )
    summary["readback_ok"] = ok_back

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"[migrate] OK 版本 {summary['version']}｜候选 {len(cands)}｜补字段 {changed_fields}")
        print(f"[migrate] 备份: {bak}")
        print(f"[migrate] 回读校验: {'✅ 通过' if ok_back else '❌ 失败'}")
        if probs_all:
            print(f"[migrate] 候选自检问题 {len(probs_all)} 条（前 5）：{probs_all[:5]}")
    return 0 if ok_back else 2


if __name__ == "__main__":
    raise SystemExit(main())
