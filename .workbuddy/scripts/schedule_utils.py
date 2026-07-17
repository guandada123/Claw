#!/usr/bin/env python3
"""
schedule_utils.py — 调度稳态检查（幂等/熔断/排他锁）
薄壳封装：用文件锁替代原实现，兼容所有旧自动化调用。

用法:
  python3 schedule_utils.py check --name "任务名"                # 每日级锁，未锁定返回0
  python3 schedule_utils.py check --name "任务名" --interval-hours 6  # 6h槽位锁
  python3 schedule_utils.py done --name "任务名"                # 每日级锁标记
  python3 schedule_utils.py done --name "任务名" --interval-hours 6   # 6h槽位标记
  python3 schedule_utils.py stats                   # 输出统计摘要
"""
import argparse
import os
import sys
from datetime import date, datetime


def _lock_name(name: str, interval_hours: int = 0) -> str:
    """生成文件锁路径

    interval_hours=0（默认）→ 每日级锁（按自然日去重，兼容旧调用）
    interval_hours>0          → 间隔级锁（按 [当前小时//间隔] 槽位去重）
        例：interval_hours=6 → 全天 4 个槽(00-05/06-11/12-17/18-23)，
            每个槽只放行一次，匹配 FREQ=HOURLY;INTERVAL=6 的 6h 自动化
    """
    safe = name.replace(" ", "_").replace("/", "_")[:40]
    today = date.today().strftime("%Y%m%d")
    if interval_hours and interval_hours > 0:
        slot = datetime.now().hour // interval_hours
        return f"/tmp/claw_lock_{safe}_{today}_h{interval_hours}_s{slot}"
    return f"/tmp/claw_lock_{safe}_{today}"


def cmd_check(name: str, interval_hours: int = 0) -> int:
    """检查：锁存在→跳过(1)，不存在→继续(0)"""
    lockfile = _lock_name(name, interval_hours)
    if os.path.exists(lockfile):
        label = f"本{interval_hours}h槽已执行" if interval_hours else "今日已执行"
        print(f"🔒 {label}: {name}")
        return 1
    return 0


def cmd_done(name: str, interval_hours: int = 0) -> int:
    """完成：写入锁文件"""
    lockfile = _lock_name(name, interval_hours)
    with open(lockfile, "w") as f:
        f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    label = f"{interval_hours}h槽" if interval_hours else "每日"
    print(f"✅ {name} 标记完成({label})")
    return 0


def cmd_stats() -> int:
    """统计摘要（含每日锁 + 6h槽位锁）"""
    import glob
    import re
    locks = sorted(glob.glob("/tmp/claw_lock_*"))
    today = date.today().strftime("%Y%m%d")
    today_locks = [l for l in locks if today in l]
    slot_locks = [l for l in today_locks if re.search(r"_h\d+_s\d+$", l)]
    daily_locks = [l for l in today_locks if l not in slot_locks]
    print(f"调度锁统计: 共 {len(locks)} 个, 今日 {len(today_locks)} 个")
    print(f"  每日级锁: {len(daily_locks)} 个")
    for l in daily_locks:
        print(f"    🔒 {os.path.basename(l)}")
    print(f"  间隔级锁(6h槽): {len(slot_locks)} 个")
    for l in slot_locks:
        m = re.search(r"_h(\d+)_s(\d+)$", l)
        h, s = m.group(1), m.group(2)
        print(f"    🔒 {os.path.basename(l)}  (槽位 s{s}, 覆盖 {int(s)*int(h):02d}:00-{int(s)*int(h)+int(h)-1:02d}:59)")
    return 0


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    p_check = sub.add_parser("check")
    p_check.add_argument("--name", required=True)
    p_check.add_argument("--interval-hours", type=int, default=0)
    p_done = sub.add_parser("done")
    p_done.add_argument("--name", required=True)
    p_done.add_argument("--interval-hours", type=int, default=0)
    sub.add_parser("stats")
    args = parser.parse_args()

    if args.cmd == "check":
        return cmd_check(args.name, getattr(args, "interval_hours", 0))
    elif args.cmd == "done":
        return cmd_done(args.name, getattr(args, "interval_hours", 0))
    elif args.cmd == "stats":
        return cmd_stats()
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
