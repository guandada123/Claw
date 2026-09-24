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
  D5 文档漂移  skill 文档里的**时段/键名/自动化 id** 与真值不一致 → 需人审（文档是"会腐烂的声明"）

退出码：0=全净；10=仅 D1（可 --fix 自愈）；20=存在 D2/D3/D4/D5（需人介入）
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


# ---------------------------------------------------------------- D5 文档漂移
TIME_RE = re.compile(r"\b([01]\d|2[0-3]):([0-5]\d)\b")
AUTO_ID_RE = re.compile(r"\bautomation-\d{6,}\b")
UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
TABLE_KEY_RE = re.compile(r"^\|\s*`([A-Za-z_][A-Za-z0-9_]*(?:\[\])?)`\s*\|")


def rrule_times(rrule: str) -> list[str]:
    """rrule → ["HH:MM"]（多 BYHOUR×BYMINUTE 时取笛卡尔积的字符串形式）。"""
    rr = rrule or ""
    hs = re.findall(r"BYHOUR=([\d,]+)", rr)
    ms = re.findall(r"BYMINUTE=([\d,]+)", rr)
    if not hs:
        return []
    hours = [h for h in hs[0].split(",") if h.strip()]
    mins = [m for m in (ms[0].split(",") if ms else ["0"]) if m.strip()]
    out = []
    for h in hours:
        for m in mins:
            try:
                out.append(f"{int(h):02d}:{int(m):02d}")
            except ValueError:
                pass
    return out


def doc_section_keys(text: str, header: str = "## 中央注册表") -> list[str]:
    """抽取某标题下首列为反引号标识符的表格键。"""
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.strip().startswith(header):
            start = i
            break
    if start is None:
        return []
    keys = []
    for ln in lines[start + 1:]:
        if ln.startswith("## "):
            break
        m = TABLE_KEY_RE.match(ln)
        if m:
            keys.append(m.group(1))
    return keys


def check_doc_drift(reg: dict, live: dict, all_ids: set, now_dt: datetime.datetime,
                    docs_override: list[str] | None = None) -> list[dict]:
    """D5：文档里的时段/键名/自动化 id 是否与真值一致。只读，不改任何文档。"""
    contract = reg.get("doc_contract") or {}
    docs = docs_override or contract.get("docs") or []
    if not docs:
        return []

    reg_keys = set(reg.keys())
    findings: list[dict] = []

    # 时段锚点：真值从调度器 DB 现算（不写死在文档/契约里，避免两头腐烂）
    anchors = []
    for a in contract.get("time_anchors", []) or []:
        sid = a.get("source_id")
        row = live.get(sid)
        truth = rrule_times(row["rrule"]) if row else []
        anchors.append({"label": a.get("label"), "source_id": sid,
                        "truth": a.get("expect_override") or (truth[0] if truth else None)})

    for raw in docs:
        p = Path(os.path.expanduser(raw))
        if not p.exists():
            findings.append({"kind": "doc_missing", "doc": raw, "detail": "契约声明的文档不存在"})
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()

        # K1 时段漂移：label 所在行必须出现真值时刻
        for an in anchors:
            label, truth = an["label"], an["truth"]
            if not label or not truth:
                continue
            hit = any(label in ln and truth in ln for ln in lines)
            if hit:
                continue
            near = {f"{h}:{m}" for ln in lines if label in ln for h, m in TIME_RE.findall(ln)}
            near_s = sorted(near)
            findings.append({
                "kind": "time_mismatch", "doc": raw, "label": label, "source_id": an["source_id"],
                "truth": truth, "doc_times": near_s,
                "detail": (f"「{label}」真值 {truth}，但文档中没有该时刻" +
                           (f"（文档同类行出现 {', '.join(near_s)}）" if near_s else "（文档完全未提该时段）")),
            })

        # K2 契约键缺失
        for k in contract.get("must_mention_keys", []) or []:
            if k not in text:
                findings.append({"kind": "key_omitted", "doc": raw, "key": k,
                                 "detail": f"契约要求提及的 registry 键 `{k}` 在文档中找不到"})

        # K3 文档表格列出的键不存在于 registry / registry 有键但表格漏列
        listed = doc_section_keys(text, contract.get("key_table_header", "## 中央注册表"))
        listed_norm = {k[:-2] if k.endswith("[]") else k for k in listed}
        for k in listed_norm:
            if k not in reg_keys:
                findings.append({"kind": "key_unknown", "doc": raw, "key": k,
                                 "detail": f"文档注册表列出了 `{k}`，但 registry.json 里没有这个键"})
        if listed_norm and contract.get("table_keys_expected"):
            excl = set(contract.get("table_keys_expected_exclude", []))
            expected = {k for k in reg_keys if k not in excl}
            for k in sorted(expected - listed_norm):
                findings.append({"kind": "key_missing_from_table", "doc": raw, "key": k,
                                 "detail": f"registry 有键 `{k}`，但文档中央注册表表格漏列了（表=registry 的索引，会一起腐烂）"})

        # K4 引用了 DB 中不存在的自动化 id（跳过含 URL 的行 —— 曾把 bittide 文章号误判为 dangling id）
        for i, ln in enumerate(lines, 1):
            if "http" in ln:
                continue
            for m in list(AUTO_ID_RE.findall(ln)) + list(UUID_RE.findall(ln)):
                if m not in all_ids:
                    findings.append({"kind": "id_dangling", "doc": raw, "id": m, "line": i,
                                     "detail": f"第 {i} 行引用了不存在的自动化 id {m}"})

    return findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--fix", action="store_true", help="仅收敛 D1（以 DB 真值回写 registry 镜像）")
    ap.add_argument("--doc", action="append", default=None,
                    help="覆盖 doc_contract.docs（可重复；供自测造错样本，不写回任何文件）")
    ap.add_argument("--no-doc", action="store_true", help="跳过 D5 文档漂移检查")
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

    # ---- D5 文档漂移（只读；文档是"会腐烂的声明"）----
    d5 = [] if args.no_doc else check_doc_drift(reg, live, all_ids, now_dt, args.doc)

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
        "D5_doc_drift": d5,
        "fixed": fixed,
        "clean": not (d1 or d2 or d3 or d4 or d5),
        "needs_human": bool(d2 or d3 or d4 or d5),
    }
    rc = 0 if out["clean"] else (10 if (d1 and not (d2 or d3 or d4 or d5)) else 20)

    if args.brief:
        # 供告警正文：只列"需人审"的项，天然带 6h 冷却由调用方控制
        for x in d4:
            print(f"[心跳超时] {x['name']} 上次 {x['last_run']} 已 {x['age_h']}h（阈 {x['threshold_h']}h）")
        for x in d2:
            print(f"[声明悬空] {x.get('name')} — {x.get('why')}")
        for x in d3:
            print(f"[未纳管] {x.get('name')} {x.get('rrule')}")
        for x in d5:
            print(f"[文档腐烂] {Path(os.path.expanduser(x['doc'])).name} — {x['detail']}")
        return rc

    if args.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        print(f"[reconcile] declared={len(declared)} observed_live={len(live)} clean={out['clean']}")
        for k, label in (("D1_mirror_drift", "D1 镜像漂移(可自动收敛)"),
                         ("D2_declared_missing", "D2 声明悬空(需人审)"),
                         ("D3_unregistered", "D3 未纳管(需人审)"),
                         ("D4_heartbeat_stale", "D4 心跳超时(需人审)"),
                         ("D5_doc_drift", "D5 文档漂移(需人审)")):
            items = out[k]
            if items:
                print(f"  {label}: {len(items)}")
                for it in items[:8]:
                    if k == "D1_mirror_drift":
                        print(f"    · {it['name'][:30]} {it['field']}: {it['declared']} -> {it['observed']}")
                    elif k == "D4_heartbeat_stale":
                        print(f"    · {it['name'][:30]} 上次 {it['last_run']} 已 {it['age_h']}h (阈 {it['threshold_h']}h)")
                    elif k == "D5_doc_drift":
                        print(f"    · [{it.get('kind')}] {it['detail']}")
                    else:
                        print(f"    · {it.get('name')} {it.get('rrule','')} {it.get('why','')}")
        if fixed:
            print(f"  ✅ 已收敛 D1 {len(fixed)} 处（DB → registry 镜像）")
        print(f"[reconcile] rc={rc}" + ("（可 --fix 自愈）" if rc == 10 else ""))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
