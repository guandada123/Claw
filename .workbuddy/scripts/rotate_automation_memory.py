#!/usr/bin/env python3
"""自动化 memory 滚动归档 —— 按**时间戳**滚动，不按行号。

背景（2026-09-28 记忆维护卡片「自动化 memory 超限 40 个 / 最重 145.1KB，+4,212B/日」）：
  每条自动化的 `memory.md` 是**只追加的运行日志**（watchdog 每 3h 一条），
  没有上限 → 无限膨胀。且实测**有的文件新条目在底部、有的在顶部**（历史演化不同），
  所以「截掉前 N 行」这种按行号的策略会误删最新记录。

判据（刻意保守，三条）：
  1. **按时间戳滚动**：行首 `YYYY-MM-DDTHH:MM` 早于 cutoff 的移入归档；其余（含无法解析的行）
     **原样保留、不改顺序** —— 宁可少归档，不可错删。
  2. **只在超过体积门槛时才动**（默认 30KB），且是**移动不是删除**：旧条目写进
     `memory_archive_<cutoff>.md`（追加），可随时找回。
  3. **解析不出时间戳就跳过并如实上报**（`no_timestamps`），不猜、不按行数硬砍。
  4. **按块归档**（不是按行）：时间戳行是块首，其后无时间戳的行归入该块 ——
     否则块结构文件（`## 时间戳` + `- 正文`）会被"标题进归档、正文留原地"劈成两半。

用法：
    python3 rotate_automation_memory.py --dry-run        # 只看会做什么
    python3 rotate_automation_memory.py                  # 执行（默认 14 天 / 30KB 门槛）
    python3 rotate_automation_memory.py --json
退出码：0 有归档或无需归档；1 有文件被跳过（需人看）。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import re
import sys

TS = re.compile(r"^\s*(?:#{1,6}\s+|[-*+]\s+|\|\s*)?(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})")
DEFAULT_ROOTS = [
    pathlib.Path("/Users/guan/WorkBuddy/Claw/.workbuddy/memory/automations"),
    pathlib.Path(os.path.expanduser("~/.workbuddy/memory/automations")),
    pathlib.Path(os.path.expanduser("~/.workbuddy/automations")),
]
DEFAULT_KEEP_DAYS = 14
DEFAULT_MIN_BYTES = 30_000


def line_ts(line: str):
    m = TS.match(line)
    if not m:
        return None
    try:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def rotate_file(path: pathlib.Path, cutoff: datetime.date, dry_run: bool) -> dict:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    lines = raw.splitlines(keepends=True)

    # **按块切分**（2026-09-28 修）：时间戳行 = 块首，其后无时间戳的行归入同一块。
    # 若按行归档，块结构文件会被"标题进归档、正文留原地"劈成两半。
    blocks: list[tuple[object, list[str]]] = []      # (date|None, lines)
    for ln in lines:
        d = line_ts(ln)
        if d is not None:
            blocks.append((d, [ln]))
        elif blocks:
            blocks[-1][1].append(ln)
        else:
            blocks.append((None, [ln]))              # 首个时间戳之前的抬头 → 永久保留
    ts_count = sum(1 for d, _ in blocks if d is not None)

    older, newer = [], []
    for d, blk in blocks:
        (older if (d is not None and d < cutoff) else newer).append(blk)
    older = [ln for blk in older for ln in blk]
    newer = [ln for blk in newer for ln in blk]

    before = len(raw.encode())
    if ts_count == 0:
        return {"file": str(path), "id": path.parent.name, "before": before,
                "after": before, "archived_lines": 0, "skipped": "no_timestamps"}
    if not older:
        return {"file": str(path), "id": path.parent.name, "before": before,
                "after": before, "archived_lines": 0, "skipped": "nothing_older_than_cutoff"}

    archive = path.parent / f"memory_archive_{cutoff.isoformat()}.md"
    header = (f"<!-- 滚动归档：{cutoff.isoformat()} 之前的条目，共 {len(older)} 行；"
              f"由 rotate_automation_memory.py 于 {datetime.datetime.now():%Y-%m-%d %H:%M} 移出 -->\n")
    marker = (f"<!-- {datetime.datetime.now():%Y-%m-%d} 滚动归档：{len(older)} 行（早于 {cutoff.isoformat()}）"
              f"已移入 {archive.name} -->\n")
    after = len(("".join(newer) + marker).encode())

    if not dry_run:
        exists = archive.exists()
        with archive.open("a", encoding="utf-8") as fh:
            if not exists:
                fh.write(header)
            fh.writelines(older)
        path.write_text(marker + "".join(newer), encoding="utf-8")

    return {"file": str(path), "id": path.parent.name, "before": before, "after": after,
            "archived_lines": len(older), "archive": str(archive), "skipped": None}


def main() -> int:
    ap = argparse.ArgumentParser(description="自动化 memory 滚动归档（按时间戳）")
    ap.add_argument("--keep-days", type=int, default=DEFAULT_KEEP_DAYS)
    ap.add_argument("--min-bytes", type=int, default=DEFAULT_MIN_BYTES)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    cutoff = datetime.date.today() - datetime.timedelta(days=args.keep_days)
    results, skipped = [], []
    for root in DEFAULT_ROOTS:
        if not root.is_dir():
            continue
        for f in sorted(root.glob("*/memory.md")):
            if f.stat().st_size < args.min_bytes:
                continue
            r = rotate_file(f, cutoff, args.dry_run)
            (skipped if r["skipped"] else results).append(r)

    if args.json:
        print(json.dumps({"cutoff": cutoff.isoformat(), "dry_run": args.dry_run,
                          "rotated": results, "skipped": skipped}, ensure_ascii=False, indent=2))
    else:
        mode = "[dry-run] " if args.dry_run else ""
        print(f"{mode}滚动归档 · cutoff={cutoff}（保留最近 {args.keep_days} 天）/ 门槛 {args.min_bytes}B")
        if not results and not skipped:
            print("  ✅ 无超过门槛的文件")
        for r in results:
            print(f"  ✅ {r['id']}: {r['before']:,}B → {r['after']:,}B（移出 {r['archived_lines']} 行 → {pathlib.Path(r['archive']).name}）")
        for r in skipped:
            print(f"  ⚠️ {r['id']}: {r['before']:,}B 跳过（{r['skipped']}）")

    return 1 if skipped else 0


if __name__ == "__main__":
    raise SystemExit(main())
