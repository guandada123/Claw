#!/usr/bin/env python3
"""find_syspath_bareimport_breaks.py — 独立扫描器：找「prompt 把 scripts/ 塞进 sys.path 后裸 import X」的断链。

为什么需要它（2026-09-25 D2 逐仓 review 发现）：
  既有的两道哨兵 restore_forwarders.py / check_broken_refs.py **只认 `scripts/X.py` 这种字面路径**；
  而真实存在另一类引用形态：
        import sys; sys.path.insert(0, 'scripts')
        import anysearch_helper as ah
  它不含 "scripts/anysearch_helper.py" 这个字面串 → 两道哨兵全盲。
  实测已命中 2 个 ACTIVE 自动化（微信早报 / 收盘晚报），import 直接 ModuleNotFoundError。
  → 哨兵的正则只覆盖了"路径式引用"，漏了"sys.path 注入 + 裸模块名"式引用。

判定：对每条 ACTIVE prompt
  ① 找出所有把 `scripts` 注入 sys.path 的痕迹（insert/append 'scripts'、cd .../scripts）
  ② 收集 prompt 中的裸 import 名（import X / from X import ...）
  ③ 对每个 X：scripts/X.py 不存在 且 .workbuddy/scripts/X.py 存在 → 断链（该补薄壳）
  ④ 两边都不存在 → 标准库/第三方/外部仓库，跳过
退出：0=无断链 / 1=有断链
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from pathlib import Path

CLAW = Path(os.environ.get("CLAW") or "/Users/guan/WorkBuddy/Claw").resolve()
AUTH = CLAW / ".workbuddy" / "scripts"
SHIM = CLAW / "scripts"

INJECT = re.compile(r"sys\.path\.(?:insert|append)\([^)]*['\"]scripts['\"]")
CD_SCRIPTS = re.compile(r"cd\s+\S*/scripts\b")
BARE_IMPORT = re.compile(r"^\s*(?:import\s+([A-Za-z_][\w]*)"
                         r"|from\s+([A-Za-z_][\w]*)\s+import)", re.M)

STDLIB = set(sys.stdlib_module_names)


def scan(db: Path | None = None) -> dict:
    """返回 {"scanned": n, "broken": {mod: [{"id","name"}...]}}；**不打印**（供 check_broken_refs 复用）。"""
    db = db or (Path(os.environ.get("HOME", "/Users/guan")) / ".workbuddy" / "workbuddy.db")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute(
        "select id,name,prompt,status from automations where deleted_at is null"
    ).fetchall()

    broken: dict[str, list[dict]] = {}
    scanned = 0
    for aid, name, prompt, status in rows:
        if status != "ACTIVE" or not prompt:
            continue
        if not (INJECT.search(prompt) or CD_SCRIPTS.search(prompt)):
            continue
        scanned += 1
        for m in BARE_IMPORT.finditer(prompt):
            mod = m.group(1) or m.group(2)
            if mod in STDLIB or mod in ("sys", "json", "os", "re"):
                continue
            if (SHIM / f"{mod}.py").exists():
                continue
            if (AUTH / f"{mod}.py").exists():
                broken.setdefault(mod, []).append({"id": aid, "name": name})
    return {"scanned": scanned, "broken": broken}


def main() -> int:
    res = scan()
    broken = res["broken"]
    print(f"仓库            : {CLAW}")
    print(f"含 scripts/ 注入的 ACTIVE 自动化: {res['scanned']}")
    if not broken:
        print("✅ 无此类断链")
        return 0
    print(f"\n❌ 发现 {len(broken)} 个模块被裸 import，但 scripts/ 下无薄壳（真身在 .workbuddy/scripts/）：")
    for mod, hits in sorted(broken.items()):
        print(f"\n  {mod}.py  ← {len(hits)} 条自动化")
        for h in hits:
            print(f"      · {h['id']}  {h['name']}")
        print(f"      真身: {AUTH / (mod + '.py')}")
        print(f"      缺失: {SHIM / (mod + '.py')}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
