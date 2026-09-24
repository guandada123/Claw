#!/usr/bin/env python3
"""hub_reconcile.py — 统一巡检中枢 · 声明式收敛检查器（observe → diff → report/act）

背景（2026-09-24 审计 + 全网对标结论）：
  "A reconciler only ever covers what you remembered to declare."
  中枢把调度真值放在 scheduler DB（automations 表），把意图/清单放在 registry.json。
  两者一旦分家就会**默认漂移**，而没有任何东西会报告它。
  本脚本就是那个"报告它"的东西：把 registry 当 declared state，把 DB 当 observed state，
  diff 出漂移并按可逆性分类处置。

漂移分类（可逆性决定处置权）：
  D1 镜像漂移  declared.rrule/status ≠ DB            → **可自动收敛**（以 DB 为准回写 registry）
  D2 声明悬空  registry 声明的 id 在 DB 不存在/已软删  → 需人审（声明本身过期）
  D3 未纳管    DB 中活跃且命中中枢 scope，但未登记      → 需人审（清单不完整）
  D4 心跳超时  declared 且节奏 ≤48h 的项，距最近一次运行超阈值 → 需人审（该跑没跑 / 平台停摆）

退出码：0=全净；10=仅 D1（可 --fix 自愈）；20=存在 D2/D3/D4（需人介入）
铁律：--fix **只写 registry（镜像）**，绝不写 DB；不碰 skill 文档；不推送（推送交给调用方）
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve()
CLAW = SCRIPT.parents[2]
DEFAULT_REGISTRY = CLAW / ".workbuddy" / "inspection_hub" / "registry.json"
DEFAULT_DB = Path(os.environ.get("HOME", "/Users/guan")) / ".workbuddy" / "workbuddy.db"

# D4 只覆盖"高频项"（节奏 ≤ 此小时数）；周/月/季项的漏跑由各自周期审阅覆盖，避免噪音
HEARTBEAT_MAX_CADENCE_H = 48


def now() -> datetime.datetime:
    return datetime.datetime.now()


def load_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"_error": str(e)}


def cadence_hours(rrule: str) -> float | None:
    """从 rrule 估算单次间隔（小时）；无法判定返回 None。"""
    rr = rrule or ""
    m = re.search(r"FREQ=HOURLY;INTERVAL=(\d+)", rr)
    if m:
        return float(m.group(1))
    if "FREQ=HOURLY" in rr:
        return 1.0
    if "FREQ=DAILY" in rr:
        return 24.0
    if "FREQ=WEEKLY" in rr:
        return 24 * 7.0
    if "FREQ=MONTHLY" in rr:
        return 24 * 31.0
    if "FREQ=YEARLY" in rr:
        return 24 * 366.0
    return None


def stale_threshold_h(cadence: float) -> float:
    """节奏 + 宽限（每日类给 6h、小时类给 3h），既灵敏又不误报。"""
    return cadence + max(3.0, cadence * 0.25)


def in_scope(name: str, scope: dict) -> bool:
    inc = scope.get("include_name_patterns") or []
    exc = scope.get("exclude_name_patterns") or []
    if any(x and x in name for x in exc):
        return False
    return any(x and x in name for x in inc)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--fix", action="store_true", help="仅收敛 D1（以 DB 真值回写 registry 镜像）")
    ap.add_argument("--brief", action="store_true",
                    help="仅输出需人审项的一行式摘要（供外部看门狗做告警正文，避免调用方拼 JSON）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    reg_path = Path(args.registry)
    reg = load_json(reg_path)
    if "_error" in reg:
        print(json.dumps({"ok": False, "error": f"registry 读取失败: {reg['_error']}"}, ensure_ascii=False))
        return 20

    db_path = Path(args.db)
    if not db_path.exists():
        print(json.dumps({"ok": False, "error": f"调度库不存在: {db_path}"}, ensure_ascii=False))
        return 20

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "select id,name,rrule,status,deleted_at,created_at from automations"
        ).fetchall()
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"调度库读取失败: {e}"}, ensure_ascii=False))
        return 20

    live = {r["id"]: r for r in rows if not r["deleted_at"]}
    all_ids = {r["id"] for r in rows}
    last_run: dict[str, int] = {}
    try:
        for r in conn.execute(
            "select automation_id, max(created_at) as t from automation_runs group by automation_id"
        ):
            if r["t"]:
                last_run[r["automation_id"]] = r["t"]
    except Exception:  # noqa: BLE001
        pass  # 运行表缺失 → D4 全部按"未知"处理（不误报）

    declared = reg.get("automations", []) or []
    scope = reg.get("automation_scope", {}) or {}
    now_dt = now()

    d1, d2, d3, d4 = [], [], [], []

    # ---- D1 / D2 ----
    for a in declared:
        i = a.get("id")
        row = live.get(i)
        if row is None:
            d2.append({"id": i, "name": a.get("name"),
                       "why": "已软删" if i in all_ids else "DB 中不存在",
                       "declared_rrule": a.get("rrule")})
            continue
        if row["rrule"] != a.get("rrule") or row["status"] != a.get("status"):
            d1.append({"id": i, "name": row["name"],
                       "field": "rrule" if row["rrule"] != a.get("rrule") else "status",
                       "declared": a.get("rrule") if row["rrule"] != a.get("rrule") else a.get("status"),
                       "observed": row["rrule"] if row["rrule"] != a.get("rrule") else row["status"]})

    # ---- D3 未纳管 ----
    declared_ids = {a.get("id") for a in declared}
    if scope.get("include_name_patterns"):
        for r in live.values():
            if r["id"] in declared_ids:
                continue
            if r["status"] != "ACTIVE":
                continue
            if in_scope(r["name"] or "", scope):
                d3.append({"id": r["id"], "name": r["name"], "rrule": r["rrule"], "status": r["status"]})

    # ---- D4 心跳 ----
    for a in declared:
        i = a.get("id")
        row = live.get(i)
        if row is None:
            continue
        c = cadence_hours(row["rrule"])
        if c is None or c > HEARTBEAT_MAX_CADENCE_H:
            continue
        base_ts = last_run.get(i)
        if base_ts is None:
            base_ts = row["created_at"]          # 新自动化：以创建时刻计宽限
        if not base_ts:
            continue
        age_h = (now_dt - datetime.datetime.fromtimestamp(base_ts / 1000)).total_seconds() / 3600
        thr = stale_threshold_h(c)
        if age_h > thr:
            d4.append({"id": i, "name": row["name"], "rrule": row["rrule"],
                       "age_h": round(age_h, 1), "threshold_h": round(thr, 1),
                       "last_run": datetime.datetime.fromtimestamp(base_ts / 1000).strftime("%Y-%m-%d %H:%M")
                       if last_run.get(i) else "(从未运行，按创建时刻计)"})

    # ---- 收敛（仅 D1，且仅在 --fix）----
    fixed = []
    if args.fix and d1:
        by_id = {a.get("id"): a for a in declared}
        for d in d1:
            a = by_id.get(d["id"])
            row = live.get(d["id"])
            if not a or not row:
                continue
            a["rrule"], a["status"] = row["rrule"], row["status"]
            fixed.append(d["id"])
        if fixed:
            reg.setdefault("_state_sync_notes", []).append(
                f"{now():%Y-%m-%d %H:%M} hub_reconcile --fix 以 DB 真值收敛镜像 D1 {len(fixed)} 处: " + ", ".join(fixed)
            )
            reg["updated_at"] = now().strftime("%Y-%m-%dT%H:%M:%S")
            tmp = str(reg_path) + ".tmp"
            Path(tmp).write_text(json.dumps(reg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, reg_path)

    out = {
        "ok": True,
        "declared": len(declared),
        "observed_live": len(live),
        "scope": {"include": scope.get("include_name_patterns", []), "exclude": scope.get("exclude_name_patterns", [])},
        "D1_mirror_drift": d1,
        "D2_declared_missing": d2,
        "D3_unregistered": d3,
        "D4_heartbeat_stale": d4,
        "fixed": fixed,
        "clean": not (d1 or d2 or d3 or d4),
        "needs_human": bool(d2 or d3 or d4),
    }
    rc = 0 if out["clean"] else (10 if (d1 and not (d2 or d3 or d4)) else 20)

    if args.brief:
        # 供告警正文：只列"需人审"的项，天然带 6h 冷却由调用方控制
        for x in d4:
            print(f"[心跳超时] {x['name']} 上次 {x['last_run']} 已 {x['age_h']}h（阈 {x['threshold_h']}h）")
        for x in d2:
            print(f"[声明悬空] {x.get('name')} — {x.get('why')}")
        for x in d3:
            print(f"[未纳管] {x.get('name')} {x.get('rrule')}")
        return rc

    if args.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        print(f"[reconcile] declared={len(declared)} observed_live={len(live)} clean={out['clean']}")
        for k, label in (("D1_mirror_drift", "D1 镜像漂移(可自动收敛)"),
                         ("D2_declared_missing", "D2 声明悬空(需人审)"),
                         ("D3_unregistered", "D3 未纳管(需人审)"),
                         ("D4_heartbeat_stale", "D4 心跳超时(需人审)")):
            items = out[k]
            if items:
                print(f"  {label}: {len(items)}")
                for it in items[:8]:
                    if k == "D1_mirror_drift":
                        print(f"    · {it['name'][:30]} {it['field']}: {it['declared']} -> {it['observed']}")
                    elif k == "D4_heartbeat_stale":
                        print(f"    · {it['name'][:30]} 上次 {it['last_run']} 已 {it['age_h']}h (阈 {it['threshold_h']}h)")
                    else:
                        print(f"    · {it.get('name')} {it.get('rrule','')} {it.get('why','')}")
        if fixed:
            print(f"  ✅ 已收敛 D1 {len(fixed)} 处（DB → registry 镜像）")
        print(f"[reconcile] rc={rc}" + ("（可 --fix 自愈）" if rc == 10 else ""))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
