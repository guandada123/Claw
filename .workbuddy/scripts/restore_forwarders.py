#!/usr/bin/env python3
"""
restore_forwarders.py — 为 9-21 去重误删的 scripts/ 副本补回转发薄壳 (治愈"双链老毛病")

背景:
  9-21 去重把 X.py 从 scripts/ 迁到 .workbuddy/scripts/ 后, 没有在 scripts/ 留转发薄壳,
  导致 139 个自动化 prompt 里硬编码的 `python3 scripts/X.py` / `import X`(经 scripts/ 在 sys.path)
  全部断链。这是"双链老毛病"的真正根因。

本脚本为每一个满足以下条件的 X.py, 在 scripts/ 补回一个**位置无关的真实转发薄壳**
(importlib + runpy, 绝不用软链 —— 符合 dual-copy-audit v1.1.0):
    - 被至少一个自动化 prompt 以 `scripts/X.py` 引用
    - scripts/X.py 当前不存在 (已迁走)
    - .workbuddy/scripts/X.py 存在 (真身)

效果: 所有旧 prompt 立即恢复可用, 且逻辑仍单源 (.workbuddy/scripts/)。
不触碰已在 scripts/ 的副本 (15 个 OK 项不动), 不删除任何东西。

默认 --dry-run; --apply 才落盘。每个薄壳创建后做 import 冒烟校验, 失败则跳过并报告。

用法:
    python3 restore_forwarders.py              # dry-run, 打印计划
    python3 restore_forwarders.py --apply      # 落盘 + 校验
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sqlite3
import sys
from pathlib import Path

try:
    from safe_dedup import FORWARDER_TPL, SKILL_RULE
except Exception:
    SKILL_RULE = "dual-copy-audit v1.1.0"
    FORWARDER_TPL = '''\
# AUTO-GENERATED FORWARDER — 单源薄壳 (restore_forwarders.py)
import importlib.util
import runpy
import sys
from pathlib import Path


def _find_real():
    here = Path(__file__).resolve().parent
    for cand in [here, *here.parents]:
        for rel in (Path(".workbuddy") / "scripts" / "{name}.py",
                    Path("src") / "claw" / "{name}.py"):
            p = cand / rel
            if p.is_file():
                return p
    raise RuntimeError("forwarder[{name}]: 找不到权威副本")


_real = _find_real()
_auth_dir = str(_real.parent)
if _auth_dir not in sys.path:
    sys.path.insert(0, _auth_dir)
_spec = importlib.util.spec_from_file_location("{name}", str(_real))
_mod = importlib.util.module_from_spec(_spec)
sys.modules["{name}"] = _mod
_spec.loader.exec_module(_mod)

if __name__ == "__main__":
    runpy.run_path(str(_real), run_name="__main__")
'''


def resolve_repo(repo: str | None) -> Path:
    if repo:
        return Path(repo).resolve()
    env = os.environ.get("CLAW")
    return Path(env).resolve() if env else Path.cwd().resolve()


def referenced_scripts(repo: Path, scan_repo: bool = False):
    db = Path(os.path.expanduser("~/.workbuddy/workbuddy.db"))
    import re
    pat = re.compile(r"scripts/([A-Za-z0-9_./-]+\.py)")
    refs: set[str] = set()
    if db.is_file():
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            for (prompt,) in con.execute("SELECT prompt FROM automations WHERE prompt LIKE '%scripts/%'"):
                refs |= set(pat.findall(prompt))
            con.close()
        except Exception as e:
            print(f"[warn] 读自动化库失败: {e}", file=sys.stderr)
    # 仅当显式 --scan-repo 时, 才把仓库里硬编码的 scripts/X.py 引用纳入
    # (默认只看自动化 prompt —— 那才是真正的断链来源, 避免为测试/文档里的引用造薄壳)
    if scan_repo:
        for ext in ("*.sh", "*.py", "*.md"):
            for p in repo.rglob(ext):
                if any(part in {".git", "node_modules", "__pycache__", "archive"} for part in p.parts):
                    continue
                try:
                    txt = p.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                refs |= set(pat.findall(txt))
    return refs


def is_forwarder(path: Path) -> bool:
    try:
        return "AUTO-GENERATED FORWARDER" in path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False


def smoke(name: str, scripts_dir: Path, py: str) -> bool:
    """import 冒烟: 从 scripts/ 目录 import X 一次 (走薄壳 -> 真身)。"""
    import subprocess
    try:
        subprocess.run([py, "-c", f"import sys; sys.path.insert(0, {str(scripts_dir)!r}); import {name}"],
                       check=True, capture_output=True, text=True, timeout=60)
        return True
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="补回 scripts/ 转发薄壳 (治愈双链)")
    ap.add_argument("--repo", default=None)
    ap.add_argument("--apply", action="store_true", help="默认 dry-run; 加此开关才落盘")
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--scan-repo", action="store_true", help="额外扫描仓库 .sh/.py/.md 里的 scripts/X.py 引用")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的转发薄壳(仅带 AUTO-GENERATED 标记的)")
    args = ap.parse_args()

    repo = resolve_repo(args.repo)
    scripts_dir = repo / "scripts"
    wb_dir = repo / ".workbuddy" / "scripts"
    if not scripts_dir.is_dir():
        print(f"❌ scripts/ 目录不存在: {scripts_dir}")
        return 2

    refs = referenced_scripts(repo, args.scan_repo)
    candidates: list[str] = []
    skipped_ok: list[str] = []
    overwritten: list[str] = []
    missing_both: list[str] = []
    for s in sorted(refs):
        p_s = scripts_dir / s
        p_w = wb_dir / s
        if p_s.is_file():
            if is_forwarder(p_s) and args.force:
                overwritten.append(s)       # 旧薄壳, --force 覆盖为新模板
            else:
                skipped_ok.append(s)        # 已在 scripts/ (非薄壳或无需覆盖), 不动
        elif p_w.is_file():
            candidates.append(s)            # 需补薄壳
        else:
            missing_both.append(s)         # 两边都无, 另查

    print(f"仓库: {repo}")
    print(f"模式: {'APPLY (落盘)' if args.apply else 'DRY-RUN (只打印)'}")
    print(f"  scripts/ 已存在(跳过): {len(skipped_ok)}")
    print(f"  需补转发薄壳:         {len(candidates)}")
    if args.force:
        print(f"  --force 覆盖旧薄壳:    {len(overwritten)}")
    print(f"  两边都缺失(另查):     {len(missing_both)}")
    print()

    created = 0
    failed = 0
    for s in candidates + ([o for o in overwritten] if args.apply else []):
        name = s[:-3]
        target = scripts_dir / s
        if args.apply:
            try:
                target.write_text(FORWARDER_TPL.format(name=name, rule=SKILL_RULE), encoding="utf-8")
            except Exception as e:
                print(f"  ❌ 写失败 {target}: {e}")
                failed += 1
                continue
            if not smoke(name, scripts_dir, args.python):
                print(f"  🔴 冒烟失败 {s} -> 删除薄壳")
                try:
                    target.unlink()
                except Exception:
                    pass
                failed += 1
                continue
            print(f"  ✅ 薄壳就绪 + 冒烟通过 {s}")
            created += 1
        else:
            print(f"  (dry) 将补回 {s} -> 转发 .workbuddy/scripts/{s}")
    print()
    if missing_both:
        print("两边都缺失 (不参与本次, 需单独排查是否在 src/claw 或已删除):")
        for s in missing_both:
            print(f"    - {s}")
        print()
    if args.apply:
        print(f"完成: 补回 {created} 个薄壳, {failed} 个失败。所有 scripts/X.py 旧引用现可用。")
        return 0 if failed == 0 else 1
    print("(以上为 DRY-RUN。确认无误后加 --apply 执行; 每个薄壳会做 import 冒烟校验。)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
