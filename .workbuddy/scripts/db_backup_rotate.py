#!/usr/bin/env python3
"""
db_backup_rotate.py — workbuddy.db 备份滚动清理（低优先级运维优化，2026-08-06）

设计：
  自动化治理过程中会对 ~/.workbuddy/workbuddy.db 做软停/合并前备份
  （命名 workbuddy.db.bak-<reason>-<YYYYMMDDHHMMSS> 或 workbuddy.db.bak.<YYYYMMDD_HHMMSS>，
    两种命名均支持；2026-08-30 起兼容点号命名，此前只认连字符导致静默漏扫）。
  本脚本按"保留 N 天"滚动清理过期备份，防止备份无限堆积撑爆磁盘。

  安全铁律：
  ① 只删匹配 workbuddy.db.bak-* 前缀的文件，绝不碰主库 workbuddy.db
  ② 默认 KEEP_DAYS=7，仅删 mtime 超过 7 天的备份；当前窗口内文件零删除
  ③ --dry-run 默认开启（只报告不删）；显式 --apply 才真实删除
  ④ 删除走 trash 语义（macOS: osascript 移废纸篓），不在个人目录直接 rm -rf

用法：
  python3 db_backup_rotate.py            # dry-run（默认，只报告）
  python3 db_backup_rotate.py --apply    # 真实清理过期项
  python3 db_backup_rotate.py --keep 14  # 自定义保留天数
  python3 db_backup_rotate.py --json     # JSON 输出（供中枢/周报聚合）
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

HOME = Path(os.path.expanduser("~"))
DB_DIR = HOME / ".workbuddy"
DB_PATH = DB_DIR / "workbuddy.db"
BACKUP_PREFIX = "workbuddy.db.bak-"
# 2026-08-30 修复：实际备份文件命名为 workbuddy.db.bak.<YYYYMMDD_HHMMSS>（点号），
# 而原实现只 glob 「连字符」前缀 → 长期静默 0 匹配（明明有 4 个过期备份却报"无需清理"）。
# 两种命名都支持，且严格排除主库 / -wal / -shm，避免误伤。
BACKUP_PATTERNS = ("workbuddy.db.bak-*", "workbuddy.db.bak.*")
NON_BACKUP_SUFFIXES = ("-wal", "-shm", "-journal")
KEEP_DAYS_DEFAULT = 7


def _list_backups() -> list[Path]:
    if not DB_DIR.exists():
        return []
    found: dict[Path, None] = {}
    for pat in BACKUP_PATTERNS:
        for p in DB_DIR.glob(pat):
            if not p.is_file():
                continue
            if p.name.endswith(NON_BACKUP_SUFFIXES):
                continue
            found[p] = None
    return sorted(found, key=lambda p: p.stat().st_mtime)


def _to_trash(path: Path) -> tuple[bool, str]:
    """macOS 移入废纸篓（trash 语义），失败则回退 os.remove。"""
    try:
        # 优先用系统 Finder 移废纸篓（可恢复）
        # 超时 30s→90s（2026-08-30）：首个 osascript 调用常需冷启动 Finder，
        # 30s 不够 → 超时后回退 os.remove，绕过废纸篓（不可逆）。放宽后 trash 语义才真正生效。
        script = f'tell application "Finder" to delete POSIX file "{path}"'
        subprocess.run(["osascript", "-e", script], check=True, capture_output=True, timeout=90)
        return True, "trash"
    except Exception as e:  # noqa: BLE001
        try:
            os.remove(path)
            return True, f"rm-fallback({e})"
        except Exception as e2:  # noqa: BLE001
            return False, f"fail({e2})"


def rotate(keep_days: int, apply: bool, dry_run: bool) -> dict:
    now = datetime.datetime.now()
    cutoff = now - datetime.timedelta(days=keep_days)
    backups = _list_backups()

    result = {
        "total": len(backups),
        "keep_days": keep_days,
        "expired": [],
        "deleted": [],
        "skipped_current_window": 0,
        "apply": apply,
    }

    for p in backups:
        mtime = datetime.datetime.fromtimestamp(p.stat().st_mtime)
        if mtime < cutoff:
            result["expired"].append({"file": p.name, "mtime": mtime.strftime("%Y-%m-%d %H:%M")})
            if apply and not dry_run:
                ok, how = _to_trash(p)
                result["deleted"].append({"file": p.name, "ok": ok, "how": how})
        else:
            result["skipped_current_window"] += 1

    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真实清理（默认 dry-run 只报告）")
    ap.add_argument("--keep", type=int, default=KEEP_DAYS_DEFAULT, help="保留天数")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    args = ap.parse_args()

    dry_run = not args.apply
    res = rotate(args.keep, args.apply, dry_run)

    if args.json:
        print(json.dumps(res, ensure_ascii=False))
        return 0

    mode = "APPLY(真实删除)" if args.apply else "DRY-RUN(只报告)"
    print(f"[db_backup_rotate] {mode} | 保留{args.keep}天")
    print(
        f"  备份总数: {res['total']} | 当前窗口内(保留): {res['skipped_current_window']} | 过期: {len(res['expired'])}"
    )
    for e in res["expired"]:
        print(f"    ⏰ 过期 {e['file']} (mtime {e['mtime']})")
    if res["deleted"]:
        for d in res["deleted"]:
            mark = "✅" if d["ok"] else "❌"
            print(f"    {mark} 删除 {d['file']} ({d['how']})")
    if not res["expired"]:
        print("  ✅ 无过期备份，无需清理")
    elif dry_run:
        print("  ℹ️ dry-run 模式未删除任何文件；加 --apply 执行真实清理")
    return 0


if __name__ == "__main__":
    sys.exit(main())
