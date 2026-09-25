#!/usr/bin/env python3
"""check_broken_refs.py — 双副本审计哨兵: 只报"真·断链", 排除外部仓库引用与死引用.

配套 restore_forwarders.py: restore_forwarders 负责"治愈"(补薄壳), 本脚本负责"监控"
(在日常双副本审计里跑, 第一时间发现新的真·断链回归, 且不误报).

判定(对每个 automation prompt 中的 scripts/X.py 引用):
  - 永远排除 DELETED 自动化(不运行).
  - 默认排除 PAUSED(不运行); --include-paused 纳入.
  - 命中"外部仓库 env"($QTS 等, --external-envs 可扩) → 该 prompt 内 scripts/X.py
    视为外部仓库引用(真身在独立仓库, 如 QuantTradingSystem), 跳过, 不误报.
    这正是 09-21 去重扫描把 `$QTS/scripts/X.py` 子串误判为 Claw 本地缺失的根因.
  - scripts/X.py 前接路径分隔('/' 或 '$<ENV>/' 字面) → 绝对/外部路径引用, 跳过.
  - 剩余"仓库相对裸引用": scripts/X.py 与 .workbuddy/scripts/X.py 双双缺失 → 真·断链.

退出: 0=无断链 / 1=发现断链 / 2=用法或 IO 错.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

# 已知外部仓库 env; 其内 scripts/X.py 真身在独立 repo, 不在 Claw 仓库范围.
DEFAULT_EXT_ENV = {"QTS"}

# 相对裸引用: scripts/X.py 前不能是 单词字符 / 路径分隔 / 点 / $ (后者多为 $ENV/scripts 外部路径)
BARE = re.compile(r"(?<![\w/$.])scripts/([A-Za-z0-9_./-]+\.py)")


def resolve_repo(repo: str | None) -> Path:
    if repo:
        return Path(repo).resolve()
    env = os.environ.get("CLAW")
    return Path(env).resolve() if env else Path.cwd().resolve()


def load_automations(db: Path) -> list[tuple]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT id,name,status,deleted_at,prompt FROM automations"
    ).fetchall()
    con.close()
    return rows


def is_external_scope(prompt: str, ext_envs: set[str]) -> bool:
    # 外部仓库路径引用: $ENV/scripts/X.py (真身在独立 repo, 如 $QTS/scripts/fetch_kline_tencent.py)
    if re.search(r"\$[A-Za-z_][A-Za-z0-9_]*/\s*scripts/", prompt):
        return True
    # 仓库切换至已知外部仓库: `cd $QTS` 之后裸 `scripts/X.py` 解析到该 repo
    # (只认已知外部 env, 避免误伤 cd $CLAW / cd $SCRIPTS 等 Claw 本地切换)
    if ext_envs and re.search(
        r"\bcd\s+\$(" + "|".join(re.escape(e) for e in ext_envs) + r")\b", prompt
    ):
        return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(
        description="双副本审计哨兵: 检测真·断链 scripts/X.py 引用(排除外部仓库/死引用)")
    ap.add_argument("--repo", default=None)
    ap.add_argument("--db", default=None, help="自动化库路径(默认 ~/.workbuddy/workbuddy.db)")
    ap.add_argument("--include-paused", action="store_true", help="也纳入 PAUSED 自动化(默认排除)")
    ap.add_argument("--external-envs", default="", help="逗号分隔的外部仓库 env 名(默认 QTS)")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args()

    repo = resolve_repo(args.repo)
    scripts_dir = repo / "scripts"
    wb_dir = repo / ".workbuddy" / "scripts"
    if not scripts_dir.is_dir():
        print(f"❌ scripts/ 不存在: {scripts_dir}", file=sys.stderr)
        return 2

    db = Path(args.db).expanduser() if args.db else Path.home() / ".workbuddy" / "workbuddy.db"
    if not db.is_file():
        print(f"❌ 自动化库缺失: {db}", file=sys.stderr)
        return 2

    ext_envs = DEFAULT_EXT_ENV | {e.strip().upper() for e in args.external_envs.split(",") if e.strip()}

    broken: list[tuple] = []        # (script, aid, name, status)
    ext_skipped: set[str] = set()
    dead_excluded = 0

    for (aid, name, status, deleted_at, prompt) in load_automations(db):
        if deleted_at not in (None, 0, "0", ""):
            continue  # DELETED 永远排除
        if status != "ACTIVE" and not (args.include_paused and status == "PAUSED"):
            dead_excluded += 1
            continue
        if not prompt:
            continue
        if is_external_scope(prompt, ext_envs):
            for s in BARE.findall(prompt):
                ext_skipped.add(s)
            continue
        for s in BARE.findall(prompt):
            if (scripts_dir / s).is_file() or (wb_dir / s).is_file():
                continue  # 已解析(薄壳或真身)
            broken.append((s, aid, name, status))

    # 按脚本聚合
    agg: dict[str, list] = {}
    for (s, aid, name, status) in broken:
        agg.setdefault(s, []).append({"id": aid, "name": name, "status": status})

    if args.json:
        out = {
            "clean": len(agg) == 0,
            "broken": {s: v for s, v in sorted(agg.items())},
            "external_skipped": sorted(ext_skipped),
            "paused_or_dead_excluded": dead_excluded,
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"仓库: {repo}")
        print(f"外部仓库引用跳过: {len(ext_skipped)} (env: {', '.join(sorted(ext_envs))})")
        print(f"DELETED/PAUSED 排除: {dead_excluded}")
        if agg:
            print(f"\n🔴 发现 {len(agg)} 个真·断链 scripts/X.py 引用:")
            for s, refs in sorted(agg.items()):
                print(f"  - {s}  (被 {len(refs)} 个 ACTIVE 自动化引用)")
                for r in refs:
                    print(f"      [{r['status']}] {r['id']}  {r['name']}")
            print("\n建议: 用 restore_forwarders.py 补薄壳, 或确认脚本确已迁移/废弃。")
        else:
            print("\n✅ 无真·断链 — Claw 仓库 scripts/↔.workbuddy/scripts/ 引用全部健康。")
    # ⚠️ 2026-09-25 补盲区（D2 逐仓 review 抓到）：本哨兵的正则只认 `scripts/X.py`
    #    这种**字面路径**引用，漏掉另一类真实形态 —— prompt 里
    #        `import sys; sys.path.insert(0, 'scripts')` + 裸 `import X`
    #    它不含 "scripts/X.py" 字面串，于是本脚本与 restore_forwarders.py **双双报「健康」**。
    #    实测该形态已让 2 个 ACTIVE 自动化（📊微信早报 / 📊收盘晚报）`import anysearch_helper`
    #    直接 ModuleNotFoundError，报告静默降级。
    #    → 委托给专用扫描器（单一实现，不在这里再写一份正则）。
    bare = _scan_bare_import_breaks()
    if bare:
        print(f"\n🔴 另有 {len(bare)} 个模块是「scripts/ 注入 + 裸 import」式断链"
              f"（本哨兵旧正则的盲区，已补）：")
        for mod, hits in sorted(bare.items()):
            print(f"  - {mod}.py  (被 {len(hits)} 个 ACTIVE 自动化引用: "
                  f"{', '.join(h['id'][:14] for h in hits)})")
        print("建议: 用 safe_dedup.FORWARDER_TPL 在 scripts/ 补薄壳（真身留在 .workbuddy/scripts/）。")
        return 1
    return 1 if agg else 0


def _scan_bare_import_breaks() -> dict:
    """按文件路径加载同级扫描器（**cwd 无关**），返回其断链字典；加载失败则返回 {}。"""
    import importlib.util

    p = Path(__file__).resolve().parent / "find_syspath_bareimport_breaks.py"
    if not p.is_file():
        return {}
    try:
        spec = importlib.util.spec_from_file_location("_fsbib", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.scan()["broken"]
    except Exception:  # noqa: BLE001
        return {}


if __name__ == "__main__":
    sys.exit(main())
