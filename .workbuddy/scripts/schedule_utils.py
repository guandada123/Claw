#!/usr/bin/env python3
"""
schedule_utils.py — 调度稳态检查（幂等/熔断/排他锁）
薄壳封装：用文件锁替代原实现，兼容所有旧自动化调用。

用法:
  python3 schedule_utils.py check --name "任务名"   # 检查锁，未锁定返回0
  python3 schedule_utils.py done --name "任务名"    # 完成标记+释放锁
  python3 schedule_utils.py stats                   # 输出统计摘要
"""
import argparse
import os
import sys
from datetime import date, datetime


def _lock_name(name: str) -> str:
    """生成文件锁路径"""
    safe = name.replace(" ", "_").replace("/", "_")[:40]
    today = date.today().strftime("%Y%m%d")
    return f"/tmp/claw_lock_{safe}_{today}"


def cmd_check(name: str) -> int:
    """检查：锁存在→跳过(1)，不存在→继续(0)"""
    lockfile = _lock_name(name)
    if os.path.exists(lockfile):
        print(f"🔒 今日已执行: {name}")
        return 1
    return 0


def cmd_done(name: str) -> int:
    """完成：写入锁文件"""
    lockfile = _lock_name(name)
    with open(lockfile, "w") as f:
        f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print(f"✅ {name} 标记完成")
    return 0


def cmd_stats() -> int:
    """统计摘要"""
    import glob
    locks = glob.glob("/tmp/claw_lock_*")
    today = date.today().strftime("%Y%m%d")
    today_locks = [l for l in locks if today in l]
    print(f"调度锁统计: {len(locks)} 个锁, 今日 {len(today_locks)} 个")
    return 0


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("check").add_argument("--name", required=True)
    sub.add_parser("done").add_argument("--name", required=True)
    sub.add_parser("stats")
    args = parser.parse_args()

    if args.cmd == "check":
        return cmd_check(args.name)
    elif args.cmd == "done":
        return cmd_done(args.name)
    elif args.cmd == "stats":
        return cmd_stats()
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
