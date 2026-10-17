#!/usr/bin/env python3
"""WorkBuddy 日志目录清理 —— 确定性保留口径。

为什么要写这个脚本（2026-10-17 审计结论）：
    清理规则原先只写在自动化 prompt 的自然语言里，由模型临场解释"早于今天减 7 天"
    这类边界，存在两种合理解释（< today-7 与 <= today-7），删除量可差 338M，
    且无法在删除前预演。删除是不可逆操作，口径必须写死在代码里并可 dry-run。

保留口径（写死）：
    * 日期目录（目录名严格 YYYY-MM-DD）：目录日期 < 今天 - keep_days(默认 7) → 删除
      例：今天 2026-10-18、keep_days=7 → 删除 ≤2026-10-10，保留 10-11..10-18 共 7 天
    * 顶层散落文件：mtime < 今天 - stray_days(默认 30) → 删除

永不触碰：
    automation.log、_archive/、_trash/、以及任何非 YYYY-MM-DD 命名的目录
    （如 Crash-Log、Diagnostics）

安全设计：
    * 默认 dry-run，必须显式 --apply 才真正删除
    * 总大小低于 min_total_gb(默认 1.0) 时不删任何东西
    * 删除采用"同卷暂存 → 清除"两段式：先 mv 到 logs/_trash/<run_id>/，
      清单落盘后再清除，--no-purge 可保留暂存以获得回滚窗口
    * 单项目失败只跳过并报告，不中断整体
    * 每轮开头清理 24h 前的残留 _trash

用法：
    python3 cleanup_workbuddy_logs.py                 # dry-run 预演
    python3 cleanup_workbuddy_logs.py --apply         # 真正删除
    python3 cleanup_workbuddy_logs.py --apply --no-purge   # 删除但保留暂存(可回滚)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from datetime import date, datetime, timedelta

LOGS_DIR = os.path.expanduser("~/.workbuddy/logs")

DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PROTECTED_DIRS = {"_archive", "_trash", "Crash-Log", "Diagnostics"}
PROTECTED_FILES = {"automation.log"}
TRASH_TTL_SECONDS = 24 * 3600

BYTES_PER_GB = 1024**3


def human(size_bytes: float) -> str:
    """字节数转人类可读字符串（GB/MB/KB）。"""
    step = 1024.0
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < step or unit == "TB":
            if unit == "B":
                return f"{int(value)}B"
            return f"{value:.1f}{unit}"
        value /= step
    return f"{value:.1f}TB"


def dir_size(path: str) -> int:
    """递归统计目录占用字节数；单文件不可读时跳过，不影响整体。"""
    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda _e: None):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total


def total_size(path: str) -> int:
    """统计文件/目录占用字节数。"""
    return dir_size(path) if os.path.isdir(path) else os.path.getsize(path)


def scan(root: str, today: date, keep_days: int, stray_days: int) -> tuple[list[dict], int]:
    """扫描待清理项。返回 (待清理项列表, 目录总大小字节)。"""
    dir_cutoff = today - timedelta(days=keep_days)  # 目录日期需 < 此值
    file_cutoff = today - timedelta(days=stray_days)  # 文件 mtime 需 < 此值
    items: list[dict] = []
    grand_total = 0

    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)

        if os.path.isdir(path):
            size = dir_size(path)
            grand_total += size
            if name in PROTECTED_DIRS:
                continue
            if not DATE_DIR_RE.match(name):
                continue
            try:
                dir_date = datetime.strptime(name, "%Y-%m-%d").date()
            except ValueError:
                continue  # 形如 2026-13-45 的非法日期，不删
            if dir_date < dir_cutoff:
                items.append(
                    {
                        "kind": "dir",
                        "path": path,
                        "name": name,
                        "size": size,
                        "reason": f"目录日期 {name} < {dir_cutoff.isoformat()}",
                    }
                )
            continue

        # 顶层散落文件
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(path)).date()
            size = os.path.getsize(path)
        except OSError:
            continue
        grand_total += size
        if name in PROTECTED_FILES:
            continue
        if mtime < file_cutoff:
            items.append(
                {
                    "kind": "file",
                    "path": path,
                    "name": name,
                    "size": size,
                    "reason": f"mtime {mtime.isoformat()} < {file_cutoff.isoformat()}",
                }
            )

    return items, grand_total


def purge_stale_trash(root: str, now: float) -> list[str]:
    """清理 24h 前的残留暂存目录，返回被清理的暂存目录名。"""
    trash_root = os.path.join(root, "_trash")
    if not os.path.isdir(trash_root):
        return []
    removed = []
    for name in sorted(os.listdir(trash_root)):
        path = os.path.join(trash_root, name)
        if not os.path.isdir(path):
            continue
        try:
            age = now - os.path.getmtime(path)
        except OSError:
            continue
        if age > TRASH_TTL_SECONDS:
            shutil.rmtree(path, ignore_errors=True)
            if not os.path.exists(path):
                removed.append(name)
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description="WorkBuddy 日志清理（确定性保留口径）")
    parser.add_argument("--apply", action="store_true", help="真正执行删除；默认仅 dry-run 预演")
    parser.add_argument(
        "--no-purge", action="store_true", help="删除后保留 _trash 暂存，提供回滚窗口"
    )
    parser.add_argument("--keep-days", type=int, default=7, help="日期目录保留天数（默认 7）")
    parser.add_argument(
        "--stray-days", type=int, default=30, help="顶层散落文件保留天数（默认 30）"
    )
    parser.add_argument(
        "--min-total-gb",
        type=float,
        default=1.0,
        help="目录总大小低于此值(GB)时不删任何内容（默认 1.0）",
    )
    parser.add_argument(
        "--root", default=LOGS_DIR, help=f"日志根目录（默认 {LOGS_DIR}），仅用于测试"
    )
    args = parser.parse_args()

    root = os.path.abspath(os.path.expanduser(args.root))

    if not os.path.isdir(root):
        print(f"[ERROR] 日志目录不存在：{root}", file=sys.stderr)
        return 2

    today = date.today()
    items, grand_total = scan(root, today, args.keep_days, args.stray_days)
    reclaim = sum(i["size"] for i in items)

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"=== WorkBuddy 日志清理 [{mode}] ===")
    print(f"根目录       : {root}")
    print(f"今天         : {today.isoformat()}")
    print(
        f"目录保留     : {args.keep_days} 天（删除目录日期 < "
        f"{(today - timedelta(days=args.keep_days)).isoformat()}）"
    )
    print(
        f"散落文件保留 : {args.stray_days} 天（删除 mtime < "
        f"{(today - timedelta(days=args.stray_days)).isoformat()}）"
    )
    print(f"当前总大小   : {human(grand_total)}")
    print(f"待清理项     : {len(items)} 项，预计回收 {human(reclaim)}")

    if items:
        print("\n--- 待清理清单（审计留痕）---")
        for i in items:
            print(f"  [{i['kind']}] {i['path']}  {human(i['size'])}  ({i['reason']})")

    if grand_total < args.min_total_gb * BYTES_PER_GB:
        print(f"\n未达清理阈值（< {args.min_total_gb}G）：本轮不删除任何内容。")
        return 0

    if not items:
        print("\n无符合保留口径的待清理项：本轮不删除任何内容。")
        return 0

    if not args.apply:
        print("\n[dry-run] 未执行删除。确认清单无误后加 --apply。")
        return 0

    # ── 两段式删除：先同卷暂存（可回滚），再清除 ──
    now = time.time()
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    staging = os.path.join(root, "_trash", run_id)
    os.makedirs(staging, exist_ok=True)

    moved: list[dict] = []
    failed: list[dict] = []
    for idx, item in enumerate(items):
        dest = os.path.join(staging, f"{idx:03d}_{item['name']}")
        try:
            shutil.move(item["path"], dest)
            moved.append({**item, "staged_at": dest})
        except (OSError, shutil.Error) as exc:
            failed.append({**item, "error": str(exc)})
            continue  # 单项失败只跳过，不中断整体

    manifest = {
        "run_id": run_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "keep_days": args.keep_days,
        "stray_days": args.stray_days,
        "no_purge": args.no_purge,
        "moved": moved,
        "failed": failed,
    }
    manifest_path = os.path.join(staging, "manifest.json")
    try:
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        print(f"[WARN] 清单写入失败（不阻断）：{exc}", file=sys.stderr)

    moved_bytes = sum(m["size"] for m in moved)
    print(f"\n--- 已暂存 {len(moved)} 项 / {human(moved_bytes)} → {staging}")
    if failed:
        print(f"--- 跳过（失败）{len(failed)} 项：")
        for f in failed:
            print(f"    {f['path']}  {f['error']}")

    purged_now: list[str] = []
    if args.no_purge:
        print(f"[--no-purge] 保留暂存，回滚窗口至 {run_id}；下一轮将自动清除 24h 前的暂存。")
    else:
        shutil.rmtree(staging, ignore_errors=True)
        purged_now.append(run_id)

    stale = purge_stale_trash(root, now)
    if stale:
        print(f"--- 顺带清除过期暂存：{', '.join(stale)}")

    after = total_size(root)
    print("\n=== 清理结果 ===")
    print(f"删除目录/文件 : {len(moved)} 项")
    print(f"释放空间     : {human(moved_bytes)}")
    print(f"清理后总大小 : {human(after)}（原 {human(grand_total)}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
