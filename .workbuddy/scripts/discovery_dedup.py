#!/usr/bin/env python3
"""discovery_dedup.py — 统一巡检中枢 v1.10 · 发现队列去重闸

病根（已实测）：同一候选在 applied 列表里**重复落地 3 次**（skill-lifecycle-registry.md）。
     去重不是"优化"，是卫生底线；否则「发现 9 条」里有几条是同一件事换个措辞。

去重键 = target_project | skill | 归一URL(去协议/query) | 归一建议前 120 字 → sha1[:16]
（算法唯一真源在 discovery_schema.dedup_key，本脚本只做读写与判定。）

用法：
  python3 discovery_dedup.py --build --json                      # 重建索引（读 registry 全量候选）
  python3 discovery_dedup.py --check new_cands.json --json       # 判定一批新候选是否重复（只读）
  python3 discovery_dedup.py --build --append new_cands.json --json   # 重建 + 收录新键

退出码：0=跑完；2=输入不可读（不得当成"没有重复"）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from discovery_schema import (  # noqa: E402
    apply_defaults,
    claw_root,
    dedup_key,
    load_dedup_index,
    load_registry,
    now_iso,
    save_dedup_index,
)

SCRIPT = Path(__file__).resolve()
CLAW = claw_root(__file__)
DEFAULT_REGISTRY = CLAW / ".workbuddy" / "inspection_hub" / "registry.json"


def build_index(cands: list[dict]) -> dict:
    idx = {"version": 1, "keys": {}, "updated_at": now_iso()}
    for c in cands:
        k = dedup_key(c)
        ent = idx["keys"].get(k)
        rec = {
            "id": c.get("id"),
            "status": c.get("status"),
            "target_project": c.get("target_project"),
            "skill": c.get("skill"),
        }
        if ent is None:
            idx["keys"][k] = {**rec, "also_seen": []}
        # 保留"已落地"优先作为主记录（已落地是历史事实），其余计入 also_seen
        elif str(c.get("status", "")).startswith("applied") and not str(
            ent.get("status", "")
        ).startswith("applied"):
            old = {k2: ent.get(k2) for k2 in ("id", "status", "target_project", "skill")}
            ent.update(rec)
            ent.setdefault("also_seen", []).append(old)
        else:
            ent.setdefault("also_seen", []).append(rec)
    return idx


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    ap.add_argument("--build", action="store_true", help="重建索引（默认动作）")
    ap.add_argument(
        "--check", default=None, help="判定该 JSON 文件里的候选是否重复（只读，不改索引）"
    )
    ap.add_argument("--append", default=None, help="重建后把该 JSON 文件里的候选键收录进索引")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    reg = load_registry(Path(args.registry))
    if "_error" in reg:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": reg["_error"],
                    "note": "退出码 2 = registry 不可读，**不等于「没有重复」**",
                },
                ensure_ascii=False,
            )
        )
        return 2

    cands = reg.get("discovery_candidates", []) or []
    idx = load_dedup_index(CLAW)
    rebuild = args.build or not idx.get("keys")
    if rebuild:
        idx = build_index(cands)

    def load_batch(p: str) -> list[dict]:
        try:
            d = json.loads(Path(p).read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            # 读不到批次 ≠ "没有重复"：显式退出 2，让调用方别把它当通过
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": f"批次文件不可读: {e}",
                        "note": "退出码 2 = 输入不可读，**不等于「没有重复」**",
                    },
                    ensure_ascii=False,
                )
            )
            raise SystemExit(2) from None
        items = d if isinstance(d, list) else d.get("candidates", []) or d.get("signals", []) or []
        return [apply_defaults(dict(x)) for x in items]

    checked, duplicates, fresh = 0, [], []
    if args.check:
        batch = load_batch(args.check)
        seen_in_batch: dict[str, str] = {}
        for c in batch:
            checked += 1
            k = dedup_key(c)
            hit = idx.get("keys", {}).get(k)
            if hit:
                duplicates.append(
                    {
                        "id": c.get("id"),
                        "key": k,
                        "dup_of": hit.get("id"),
                        "dup_of_status": hit.get("status"),
                        "why": "命中已有候选（同锚点+同来源+同主张）",
                    }
                )
            elif k in seen_in_batch:
                duplicates.append(
                    {"id": c.get("id"), "key": k, "dup_of": seen_in_batch[k], "why": "本批次内重复"}
                )
            else:
                seen_in_batch[k] = c.get("id")
                fresh.append({"id": c.get("id"), "key": k})

    appended = 0
    if args.append:
        batch = load_batch(args.append)
        for c in batch:
            k = dedup_key(c)
            if k not in idx["keys"]:
                idx["keys"][k] = {
                    "id": c.get("id"),
                    "status": c.get("status"),
                    "target_project": c.get("target_project"),
                    "skill": c.get("skill"),
                    "also_seen": [],
                }
                appended += 1

    wrote = False
    if rebuild or args.append:
        wrote = save_dedup_index(CLAW, idx)

    out = {
        "ok": True,
        "index_size": len(idx.get("keys", {})),
        "index_written": wrote,
        "checked": checked,
        "duplicates": duplicates,
        "fresh": fresh,
        "appended": appended,
        "blocked_in_index": sum(1 for v in idx.get("keys", {}).values() if v.get("also_seen")),
        "note": "重复项一律**不再入队**（同一个「优化对象+来源+主张」只应存在一条活跃候选）",
    }
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(
            f"[dedup] 索引 {out['index_size']} 键｜本轮检查 {checked}｜重复 {len(duplicates)}｜新增 {appended}"
        )
        for d in duplicates[:10]:
            print(f"  - {d['id']} ≈ {d['dup_of']}（{d['why']}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
