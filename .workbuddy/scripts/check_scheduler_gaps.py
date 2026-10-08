#!/usr/bin/env python3
"""调度器「停机/漏派」检测（纯读，无副作用）。

背景（2026-10-04 实测发现）：
  `schedule_utils.py stats` 与「7 天成功率」都只统计**已经派发**的 run（run start /
  finished 事件对）。调度器一旦停机，那些槽位**根本没产生事件** —— 于是它们既不在
  分子也不在分母里，成功率看起来照样 98.8%「健康」。

  实测样本：2026-10-03T04:51Z ~ 2026-10-04T10:00Z 调度器停机 ≈29h08m，
  期间全部自动化槽位静默丢失（含本「记忆维护」10-04 03:30 那次），
  重启后**不补派、不告警**（missedWindowMs=86400000 形同虚设）。

  同族铁律：①「判自动化是否派发只认 run start/finished 事件对」——所以「事件对缺失」
  本身就是最强信号，只是没人去查它的**反面**（事件整体消失）；②「聚合值稳定 ≠ 状态稳定」；
  ③「行数完整性 ≠ 数值完整性」。

判据：把日志按时间排序，取相邻两条事件的时间差；差值 > --gap-min 即为一个「长静默窗口」。
      窗口内的槽位全部算「静默丢失」（不补派）。
      ⚠️ 阈值必须避开「正常静默」：调度器只在有活动时写日志，实测 30 天内正常静默
         最长 ≈2.9h（夜间隔槽）→ 默认阈值取 4h（240min），低于此值会把正常空档误报成停机。
      分级：≥6h 🔴 疑似停机；4–6h ⚠️ 长静默。

用法：
  python3 check_scheduler_gaps.py                 # 默认 7 天、阈值 240 分钟
  python3 check_scheduler_gaps.py --days 30 --gap-min 360
  python3 check_scheduler_gaps.py --json          # 机器可读
退出码：发现 🔴 疑似停机 → 1；仅 ⚠️ 或干净 → 0。
"""

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

DEFAULT_LOG = Path.home() / ".workbuddy" / "logs" / "automation.log"
TS = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z)")


def parse_ts(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=dt.timezone.utc)


def scan(log: Path, days: int, gap_min: int):
    now = dt.datetime.now(dt.timezone.utc)
    cut = now - dt.timedelta(days=days)
    stamps = []
    with log.open(encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            m = TS.match(line)
            if not m:
                continue
            try:
                ts = parse_ts(m.group(1))
            except ValueError:
                continue
            if ts >= cut:
                stamps.append(ts)
    if not stamps:
        return [], now, cut
    stamps.sort()
    gaps = []
    for a, b in zip(stamps, stamps[1:]):
        mins = (b - a).total_seconds() / 60
        if mins > gap_min:
            gaps.append(
                {
                    "from": a.isoformat(),
                    "to": b.isoformat(),
                    "minutes": round(mins, 1),
                    "hours": round(mins / 60, 2),
                }
            )
    return gaps, now, cut


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=str(DEFAULT_LOG))
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--gap-min", type=int, default=240)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    log = Path(a.log)
    if not log.exists():
        print(f"⚠️ 日志不存在: {log}")
        return 1
    gaps, now, cut = scan(log, a.days, a.gap_min)
    total = sum(g["minutes"] for g in gaps)
    hard = [g for g in gaps if g["minutes"] >= 360]
    if a.json:
        print(
            json.dumps(
                {
                    "window_days": a.days,
                    "gap_min": a.gap_min,
                    "outages": gaps,
                    "outage_total_hours": round(total / 60, 2),
                    "suspect_outage_hours": round(sum(g["minutes"] for g in hard) / 60, 2),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1 if hard else 0

    print(f"调度器停机检测 · 窗口 {cut:%m-%d %H:%M} ~ {now:%m-%d %H:%M} UTC · 阈值 {a.gap_min}min")
    if not gaps:
        print("✅ 无长静默窗口（事件流连续）")
        return 0
    print(
        f"发现 {len(gaps)} 个长静默窗口，累计 {total / 60:.2f}h —— 窗口内槽位**静默丢失且不补派**："
    )
    for g in gaps:
        tag = "🔴 疑似停机" if g["minutes"] >= 360 else "⚠️ 长静默"
        print(f"   {tag}  {g['from']} → {g['to']}  {g['hours']}h")
    print("   注意：这些窗口不会出现在「成功率」里（无事件 = 不进分子也不进分母）。")
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
