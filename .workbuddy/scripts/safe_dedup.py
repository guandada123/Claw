#!/usr/bin/env python3
"""
safe_dedup.py — 带闸门的去重 / 单源化脚本 (P0 防护栏)

背景 (dual-copy-audit v1.1.0 + 2026-09-21 去重炸链事故 + 2026-09-23 血的教训):
  9-21 一次性把 run_debate.py / qts_client.py / is_trading_day.py / cost_tracker.py 等
  从 scripts/ 迁到 .workbuddy/scripts/, 但多个自动化 task prompt 仍硬编码 `scripts/X.py`,
  且存在"同目录裸导入"(import qts_client 依赖 sys.path 里的同目录)——搬走即断链。

本脚本把"去重"变成**有闸门的原子操作**, 杜绝上述断链:

  铁律 (任一不过 -> 从备份回滚并 ABORT):
    1. .workbuddy/scripts/ 是权威活目录 ($SCRIPTS 指向它); scripts/ 是可退役的旧副本侧。
       (权威方向靠实证: 读 preamble 的 $SCRIPTS, 不套用"新脚本落 scripts/"的直觉)
    2. 绝不用文件系统软链"统一"双副本 -> 用位置无关的真实转发薄壳 (importlib + runpy)。
    3. 删/换前必查调用方: 按符号 (import/from X) + 按目录 (scripts/X.py 字面路径)
       + 自动化库 prompt (workbuddy.db, 路径无关匹配文件名)。
    4. 改变后必跑两道闸门:
         闸门1 导入冒烟: 从每个调用方所在目录实际 import X 一次
         闸门2 全量测试: pytest <tests-dir>
    5. 任一闸门失败 -> 从 /tmp 备份回滚, 打印 ABORT, exit 1。
    6. 默认 --dry-run: 只打印计划, 不落磁盘任何更改。--apply 才真干。
    7. 删/换前先 cp 实体到 --backup-dir (默认 /tmp/safe_dedup_<date>/)。

用法:
    python3 safe_dedup.py                      # dry-run, 打印全部碰撞与处置计划
    python3 safe_dedup.py --apply              # 执行 (带备份 + 双闸门)
    python3 safe_dedup.py --only export_qts_regime.py
    python3 safe_dedup.py --repo /path/to/repo --authority .workbuddy/scripts --stale scripts
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------
SKILL_RULE = "dual-copy-audit v1.1.0"
EXCLUDE_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    "archive",
    "claw.egg-info",
    ".venv",
    ".mypy_cache",
}

# 位置无关转发薄壳模板。{name} = 模块名(无 .py)。运行时向上搜索权威副本。
FORWARDER_TPL = """\
# AUTO-GENERATED FORWARDER — 单源薄壳 (safe_dedup.py, rule: {rule})
# 真实实现: .workbuddy/scripts/{name}.py  (运行时向上搜索定位, 位置无关)
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
    raise RuntimeError("forwarder[{name}]: 找不到权威副本, 已从 {rule} 中断链?")


_real = _find_real()
_auth_dir = str(_real.parent)
if _auth_dir not in sys.path:
    sys.path.insert(0, _auth_dir)   # 让真实模块的同级 import 可解析 (等同 CLI 从权威目录运行)
_spec = importlib.util.spec_from_file_location("{name}", str(_real))
_mod = importlib.util.module_from_spec(_spec)
sys.modules["{name}"] = _mod          # 让 `import {name}` 直接拿到真实模块
_spec.loader.exec_module(_mod)

if __name__ == "__main__":
    runpy.run_path(str(_real), run_name="__main__")  # 兼容 `python scripts/{name}.py`
"""


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------
def log(msg: str) -> None:
    print(msg, flush=True)


def is_forwarder(path: Path) -> bool:
    try:
        src = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False
    return bool(
        re.search(
            r"import\s+claw|from\s+claw|runpy|sys\.path|src/claw|spec_from_file_location|AUTO-GENERATED FORWARDER",
            src,
        )
    )


def is_symlink(path: Path) -> bool:
    return path.is_symlink()


def resolve_repo(repo: str | None) -> Path:
    if repo:
        return Path(repo).resolve()
    env = os.environ.get("CLAW")
    if env:
        return Path(env).resolve()
    return Path.cwd().resolve()


def authoritative_dir(repo: Path, authority: str | None) -> Path:
    if authority:
        return (repo / authority).resolve()
    env = os.environ.get("SCRIPTS")
    if env:
        return Path(env).resolve()
    return (repo / ".workbuddy" / "scripts").resolve()


def iter_py(repo: Path):
    for p in repo.rglob("*.py"):
        if any(part in EXCLUDE_DIRS for part in p.parts):
            continue
        yield p


def collisions(repo: Path, auth_dir: Path, stale_dir_name: str):
    """返回 [(name, auth_path, stale_path_or_None, kind)]。kind: 'collision' 表示两侧都有。"""
    out = []
    auth_map = {p.name: p for p in auth_dir.glob("*.py")} if auth_dir.is_dir() else {}
    stale_dir = repo / stale_dir_name
    stale_map = {p.name: p for p in stale_dir.glob("*.py")} if stale_dir.is_dir() else {}
    seen = set()
    for name in sorted(set(auth_map) | set(stale_map)):
        if name in seen:
            continue
        seen.add(name)
        a = auth_map.get(name)
        s = stale_map.get(name)
        if a and s:
            out.append((name, a, s, "collision"))
        elif s and not a:
            # 只存在于 stale 侧: 不属"去重"范畴, 跳过 (保留, 由人工决定)
            pass
    return out


# --------------------------------------------------------------------------
# 调用方核查 (按符号 + 按目录 + 自动化库)
# --------------------------------------------------------------------------
IMPORT_RE = re.compile(r"^\s*(import|from)\s+([A-Za-z_][\w]*)\b")
PATH_RE = re.compile(r"(scripts/\S+\.py|\.workbuddy/scripts/\S+\.py)")


def find_fs_callers(repo: Path, mod_name: str):
    """返回 (caller_dirs:set[Path], caller_files:set[Path])。"""
    caller_dirs: set[Path] = set()
    caller_files: set[Path] = set()
    pat_import = re.compile(rf"(import|from)\s+{re.escape(mod_name)}\b")
    pat_path = re.compile(
        re.escape(f"scripts/{mod_name}.py") + r"|" + re.escape(f".workbuddy/scripts/{mod_name}.py")
    )
    for p in iter_py(repo):
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if pat_import.search(text) or pat_path.search(text):
            caller_files.add(p)
            caller_dirs.add(p.parent)
    # 也扫 shell / md 里的字面路径引用 (9-21 断链源: 自动化 prompt 硬编码 scripts/X.py)
    for ext in ("*.sh", "*.md"):
        for p in repo.rglob(ext):
            if any(part in EXCLUDE_DIRS for part in p.parts):
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if pat_path.search(text):
                caller_files.add(p)
                caller_dirs.add(p.parent)
    return caller_dirs, caller_files


def find_db_callers(mod_name: str):
    """best-effort: 读 workbuddy.db prompt 列 (路径无关匹配文件名)。返回 (hits:list, ok:bool)。"""
    db = Path(os.path.expanduser("~/.workbuddy/workbuddy.db"))
    if not db.is_file():
        return ([], False)
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = con.execute(
            "SELECT id,name,status FROM automations WHERE prompt LIKE ? OR prompt LIKE ? OR prompt LIKE ?",
            (f"%{mod_name}.py%", f"%scripts/{mod_name}%", f"%.workbuddy/scripts/{mod_name}%"),
        ).fetchall()
        con.close()
        return ([(r[0], r[1], r[2]) for r in rows], True)
    except Exception as e:
        return ([(f"<db-error:{e}>", "", "")], False)


# --------------------------------------------------------------------------
# 两道闸门
# --------------------------------------------------------------------------
def gate_import_smoke(
    mod_name: str, caller_dirs: set[Path], extra_dirs: set[Path], py: str
) -> list[str]:
    """从每个调用方目录 import 一次。返回失败列表。"""
    fails = []
    dirs = set(caller_dirs) | set(extra_dirs)
    for d in sorted(dirs):
        try:
            subprocess.run(
                [py, "-c", f"import sys; sys.path.insert(0, {str(d)!r}); import {mod_name}"],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.CalledProcessError as e:
            fails.append(
                f"{d} -> {mod_name}: {e.stderr.strip().splitlines()[-1] if e.stderr.strip() else 'import failed'}"
            )
        except Exception as e:
            fails.append(f"{d} -> {mod_name}: {e}")
    return fails


def gate_pytest(tests_dir: Path, py: str) -> str | None:
    if not tests_dir.is_dir():
        return None  # 无测试目录, 跳过 (不算失败)
    try:
        r = subprocess.run(
            [py, "-m", "pytest", str(tests_dir), "-q"],
            check=True,
            capture_output=True,
            text=True,
            timeout=600,
        )
        return None
    except subprocess.CalledProcessError as e:
        last = e.stderr.strip().splitlines()[-3:]
        return "pytest 失败:\n" + "\n".join(last)
    except Exception as e:
        return f"pytest 异常: {e}"


# --------------------------------------------------------------------------
# 处置计划
# --------------------------------------------------------------------------
def plan_for(name, auth_path, stale_path, repo, auth_dir, db_checked):
    mod = name[:-3]
    caller_dirs, caller_files = find_fs_callers(repo, mod)
    db_hits, db_ok = find_db_callers(mod)

    has_caller = bool(caller_dirs) or bool(db_hits)
    stale_is_link = is_symlink(stale_path)
    stale_is_fwd = is_forwarder(stale_path)

    plan = {
        "name": name,
        "auth": str(auth_path),
        "stale": str(stale_path),
        "stale_kind": "SYMLINK" if stale_is_link else ("FORWARDER" if stale_is_fwd else "REAL"),
        "fs_callers": sorted(str(x) for x in caller_files),
        "fs_caller_dirs": sorted(str(x) for x in caller_dirs),
        "db_hits": db_hits,
        "db_checked": db_ok,
        "action": None,
        "reason": "",
    }

    if stale_is_link:
        plan["action"] = "DELETE_LINK"
        plan["reason"] = "已是软链 -> 直接删 (软链是创可贴, 消除它)"
    elif has_caller:
        plan["action"] = "REPLACE_FORWARDER"
        plan["reason"] = "有调用方 -> 备份后改为转发薄壳 (保留单源, 不删真实调用入口)"
    # 无调用方。若 DB 未核查 -> 保守: 仍用转发薄壳而非删除
    elif not db_ok:
        plan["action"] = "REPLACE_FORWARDER"
        plan["reason"] = "文件系统 0 调用方, 但自动化库未核查(db不可读) -> 保守改为转发薄壳而非删除"
    else:
        plan["action"] = "DELETE_REAL"
        plan["reason"] = "文件系统与自动化库均 0 调用方 -> 备份后删除旧副本"
    return plan


# --------------------------------------------------------------------------
# 执行
# --------------------------------------------------------------------------
def do_apply(plan, repo, auth_dir, backup_dir: Path, py: str, tests_dir: Path) -> bool:
    name = plan["name"]
    mod = name[:-3]
    stale = Path(plan["stale"])
    backup = backup_dir / name
    try:
        shutil.copy2(stale, backup)
    except Exception as e:
        log(f"  ❌ 备份失败 {stale} -> {backup}: {e}")
        return False
    log(f"  💾 备份 {stale} -> {backup}")

    action = plan["action"]
    if action in ("DELETE_LINK", "DELETE_REAL"):
        try:
            stale.unlink()
            log(f"  🗑  删除 {stale}")
        except Exception as e:
            log(f"  ❌ 删除失败: {e}")
            return False
    elif action == "REPLACE_FORWARDER":
        try:
            stale.write_text(FORWARDER_TPL.format(name=mod, rule=SKILL_RULE), encoding="utf-8")
            log(f"  🔧 改写为转发薄壳 {stale}")
        except Exception as e:
            log(f"  ❌ 改写失败: {e}")
            return False

    # 双闸门
    extra = {auth_dir, stale.parent}
    fails = gate_import_smoke(mod, {Path(x) for x in plan["fs_caller_dirs"]}, extra, py)
    if fails:
        log("  🔴 闸门1(导入冒烟)失败:\n    " + "\n    ".join(fails))
        _rollback(stale, backup)
        return False
    log("  ✅ 闸门1(导入冒烟)通过")

    perr = gate_pytest(tests_dir, py)
    if perr:
        log(f"  🔴 闸门2(全量测试)失败:\n    {perr}")
        _rollback(stale, backup)
        return False
    log("  ✅ 闸门2(全量测试)通过")
    return True


def _rollback(stale: Path, backup: Path) -> None:
    try:
        if backup.is_file():
            shutil.copy2(backup, stale)
            log(f"  ↩️  已回滚 {stale} <- {backup}")
    except Exception as e:
        log(f"  💥 回滚失败! 请手动从 {backup} 恢复: {e}")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="带闸门的去重/单源化脚本 (P0 防护栏)")
    ap.add_argument("--repo", default=None, help="仓库根 (默认 $CLAW 或 cwd)")
    ap.add_argument(
        "--authority", default=None, help="权威目录 (默认 $SCRIPTS 或 <repo>/.workbuddy/scripts)"
    )
    ap.add_argument("--stale", default="scripts", help="旧副本侧目录名 (默认 scripts)")
    ap.add_argument("--apply", action="store_true", help="默认 dry-run; 加此开关才落磁盘")
    ap.add_argument("--only", default=None, help="只处理指定文件名, 如 export_qts_regime.py")
    ap.add_argument(
        "--tests-dir",
        default=None,
        help="pytest 目录 (默认 <repo>/tests 或 <repo>/.workbuddy/tests)",
    )
    ap.add_argument(
        "--backup-dir", default=None, help="备份目录 (默认 /tmp/safe_dedup_<YYYYMMDD>/)"
    )
    ap.add_argument("--python", default=sys.executable, help="用于冒烟/测试的解释器")
    args = ap.parse_args()

    repo = resolve_repo(args.repo)
    auth_dir = authoritative_dir(repo, args.authority)
    if not auth_dir.is_dir():
        log(f"❌ 权威目录不存在: {auth_dir}")
        return 2
    log(f"仓库根     : {repo}")
    log(f"权威目录   : {auth_dir}   (rule: {SKILL_RULE})")
    log(f"旧副本侧   : {repo / args.stale}")
    log(f"模式       : {'APPLY (落磁盘)' if args.apply else 'DRY-RUN (只打印计划)'}")
    print()

    cols = collisions(repo, auth_dir, args.stale)
    if args.only:
        cols = [c for c in cols if c[0] == args.only]
    if not cols:
        log("✅ 无同名碰撞 (scripts/ <-> .workbuddy/scripts/)。当前无需去重, 仓库已是单源。")
        return 0

    tests_dir = Path(args.tests_dir) if args.tests_dir else None
    if tests_dir is None:
        for cand in (repo / "tests", repo / ".workbuddy" / "tests"):
            if cand.is_dir():
                tests_dir = cand
                break
    backup_dir = (
        Path(args.backup_dir)
        if args.backup_dir
        else Path(f"/tmp/safe_dedup_{datetime.now():%Y%m%d}")
    )
    if args.apply:
        backup_dir.mkdir(parents=True, exist_ok=True)

    ok = True
    for name, auth_path, stale_path, _ in cols:
        plan = plan_for(name, auth_path, stale_path, repo, auth_dir, db_checked=True)
        log(f"━━ {name}  [{plan['stale_kind']}]")
        log(f"   权威: {plan['auth']}")
        log(f"   旧侧: {plan['stale']}")
        if plan["fs_callers"]:
            log(f"   文件系统调用方({len(plan['fs_callers'])}):")
            for c in plan["fs_callers"]:
                log(f"     - {c}")
        else:
            log("   文件系统调用方: 无")
        if plan["db_hits"]:
            log(f"   自动化库命中({len(plan['db_hits'])}): {plan['db_hits']}")
        else:
            log("   自动化库命中: 无 (或 db 不可读)")
        log(f"   ➡️  处置: {plan['action']} — {plan['reason']}")

        if args.apply:
            log("   🔧 执行中...")
            if not do_apply(
                plan, repo, auth_dir, backup_dir, args.python, tests_dir or Path("/dev/null")
            ):
                ok = False
                log(f"   🛑 {name} 处置失败/已回滚, ABORT。后续项跳过。")
                break
            log(f"   ✅ {name} 完成")
        print()

    if args.apply:
        log(
            f"{'🎉 全部处置完成 (备份在 ' + str(backup_dir) + ')' if ok else '⚠️  存在失败项, 已回滚。请检查后重试。'}"
        )
        return 0 if ok else 1
    else:
        log("（以上为 DRY-RUN 计划。确认无误后加 --apply 执行；执行会自动备份并跑双闸门。）")
        return 0


if __name__ == "__main__":
    sys.exit(main())
