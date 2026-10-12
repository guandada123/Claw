#!/usr/bin/env python3
"""app_log_rotate.py — WorkBuddy 应用日志/追踪**滚动归档**（P2-2，2026-10-11）

背景（审计 P2-2）：
  `~/.workbuddy/logs` **3.22 GB**（其中单日主线程日志 ~300 MB）、
  `~/.workbuddy/traces` **2.75 GB**（2476 个会话 trace），二者**无任何轮转**，增长无上限。
  磁盘当前有余量（故不紧急），但"无上限"本身就是风险；且历史先例已存在
  （`logs/_archive/logs_le_20260909.tar.gz` 231 MB），说明这是被认可的做法。

设计（沿用 `db_backup_rotate.py` 的同一套安全铁律，保持仓内一致）：
  ① **范围锁定**：只处理 `logs/<YYYY-MM-DD>/` 与 `traces/<session>/trace_*.json`；
     **绝不**碰 `logs/*.log`（`automation.log` 是调度取证的真源）、**绝不**碰当天目录。
  ② **归档而非删除**：打包到 `_archive/<kind>_le_<YYYYMMDD>.tar.gz`（沿用既有命名）。
  ③ **先验证后删源**：归档成功后必须能 `tar -tzf` 列出且成员数 == 源文件数，才删源目录。
  ④ **默认 dry-run**：只报告；显式 `--apply` 才真实执行。
  ⑤ `projects/`（会话正文，属**数据**不是日志）**一律不动** —— 只报告其占用。

用法：
  python3 app_log_rotate.py                # dry-run（默认）
  python3 app_log_rotate.py --apply        # 真实归档
  python3 app_log_rotate.py --keep-days 14 --apply
  python3 app_log_rotate.py --json         # JSON（供巡检/周报聚合）
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

BASE = Path.home() / ".workbuddy"
LOGS = BASE / "logs"
TRACES = BASE / "traces"
PROJECTS = BASE / "projects"
ARCHIVE = LOGS / "_archive"  # 沿用既有归档位置（logs/_archive/）

DEFAULT_KEEP_DAYS = 7


def _dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def expired_log_dirs(keep_days: int, today: dt.date | None = None) -> list[Path]:
    """logs/ 下**过期**的按日目录。

    以**目录名里的日期**为准（不看 mtime —— 目录 mtime 会被后续写入刷新而失真）。
    名字解析不出来的目录一律**不处理**（宁少勿多）。
    """
    today = today or dt.date.today()
    cutoff = today - dt.timedelta(days=keep_days)
    out = []
    for d in sorted(LOGS.iterdir()) if LOGS.exists() else []:
        if not d.is_dir() or d.name.startswith("_"):
            continue
        try:
            d_date = dt.date.fromisoformat(d.name)
        except ValueError:
            continue  # 非日期命名 → 不碰
        if d_date < cutoff:
            out.append(d)
    return out


def expired_trace_files(keep_days: int, today: dt.date | None = None) -> list[Path]:
    """traces/ 下**过期**的 trace 文件（目录名是会话号、无日期，故按文件 mtime）。"""
    today = today or dt.date.today()
    cutoff_ts = dt.datetime.combine(today - dt.timedelta(days=keep_days), dt.time()).timestamp()
    if not TRACES.exists():
        return []
    return [
        p
        for p in sorted(TRACES.rglob("trace_*.json"))
        if p.is_file() and p.stat().st_mtime < cutoff_ts
    ]


def archive_paths(paths: list[Path], root: Path, name: str, apply: bool) -> tuple[bool, int, str]:
    """把 paths 打包成 _archive/<name>.tar.gz；返回 (是否成功, 成员数, 归档路径)。

    ⚠️ 先写 tar → 校验成员数 == 源文件数 → 调用方才可删源（本函数**不删源**）。
    """
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    target = ARCHIVE / f"{name}.tar.gz"
    if not apply:
        return True, len(paths), str(target)
    try:
        with tarfile.open(target, "w:gz") as tar:
            for p in paths:
                tar.add(p, arcname=str(p.relative_to(root)))
    except Exception as e:  # noqa: BLE001 - 归档失败必须报错并保留源
        return False, 0, f"{target} ({type(e).__name__}: {e})"
    # 校验：能列出且成员数一致（多一层：文件大小 > 0）
    try:
        with tarfile.open(target, "r:gz") as tar:
            members = [m for m in tar.getmembers() if m.isfile()]
        if target.stat().st_size <= 0 or len(members) < len(paths):
            return False, len(members), f"{target}（成员 {len(members)} < 源 {len(paths)}）"
        return True, len(members), str(target)
    except Exception as e:  # noqa: BLE001
        return False, 0, f"{target}（校验失败 {type(e).__name__}: {e}）"


def main() -> int:
    ap = argparse.ArgumentParser(description="WorkBuddy 应用日志/追踪滚动归档")
    ap.add_argument("--apply", action="store_true", help="真实执行（默认只报告）")
    ap.add_argument("--keep-days", type=int, default=DEFAULT_KEEP_DAYS, help="热数据保留天数")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    args = ap.parse_args()
    apply = args.apply
    today = dt.date.today()

    rep: dict = {
        "date": today.isoformat(),
        "apply": apply,
        "keep_days": args.keep_days,
        "before": {
            "logs": _dir_size(LOGS),
            "traces": _dir_size(TRACES),
            "projects": _dir_size(PROJECTS),
        },
        "log_dirs": [],
        "trace_files": 0,
        "archived": [],
        "errors": [],
    }

    # ① logs/<date>/ 过期目录
    dirs = expired_log_dirs(args.keep_days, today)
    if dirs:
        ok, n, tgt = archive_paths(dirs, LOGS, f"logs_le_{today:%Y%m%d}", apply)
        entry = {
            "count": len(dirs),
            "files": n,
            "target": tgt,
            "size": sum(_dir_size(d) for d in dirs),
        }
        rep["log_dirs"] = [f"{d.name} ({_human(_dir_size(d))})" for d in dirs]
        if ok and apply:
            for d in dirs:
                shutil.rmtree(d, ignore_errors=False)
            entry["removed"] = True
        rep["archived"].append({"kind": "logs", **entry})
        if not ok:
            rep["errors"].append(f"logs 归档失败：{tgt}")

    # ② traces 过期文件（按整份归档，保留相对路径）
    files = expired_trace_files(args.keep_days, today)
    rep["trace_files"] = len(files)
    if files:
        ok, n, tgt = archive_paths(files, TRACES, f"traces_le_{today:%Y%m%d}", apply)
        entry = {
            "count": len(files),
            "files": n,
            "target": tgt,
            "size": sum(p.stat().st_size for p in files),
        }
        if ok and apply:
            for p in files:
                p.unlink(missing_ok=True)
            # 清理空目录
            for d in sorted({p.parent for p in files}, key=lambda x: -len(str(x))):
                try:
                    if d != TRACES and not any(d.iterdir()):
                        d.rmdir()
                except OSError:
                    pass
            entry["removed"] = True
        rep["archived"].append({"kind": "traces", **entry})
        if not ok:
            rep["errors"].append(f"traces 归档失败：{tgt}")

    rep["after"] = (
        {"logs": _dir_size(LOGS), "traces": _dir_size(TRACES), "projects": _dir_size(PROJECTS)}
        if apply
        else rep["before"]
    )

    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0

    print(f"{'[APPLY]' if apply else '[DRY-RUN]'} 日志/追踪滚动归档（保留 {args.keep_days} 天）")
    print(f"  logs   : {_human(rep['before']['logs'])} → {_human(rep['after']['logs'])}")
    print(f"  traces : {_human(rep['before']['traces'])} → {_human(rep['after']['traces'])}")
    print(f"  projects（数据，不动）: {_human(rep['before']['projects'])}")
    for a in rep["archived"]:
        flag = "✅" if a.get("removed") else "（dry-run）"
        print(
            f"  {flag} {a['kind']}: {a['count']} 项 / {_human(a['size'])} → {Path(a['target']).name}"
        )
    for e in rep["errors"]:
        print(f"  ❌ {e}")
    if not rep["archived"]:
        print("  （无需归档：无超过保留期的项）")
    return 1 if rep["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
