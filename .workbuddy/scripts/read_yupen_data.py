#!/usr/bin/env python3
"""
鱼盆数据读取器 — 从 output/yupen/ 目录读取最新有效数据，处理新鲜度标注。

用法:
  python3 read_yupen_data.py                    # 输出最新有效数据的结构化 JSON
  python3 read_yupen_data.py --freshness-only   # 仅输出新鲜度状态
  python3 read_yupen_data.py --days 7           # 查找最近7天内的有效数据

输出 JSON 结构:
{
  "status": "ok" | "no_data",
  "freshness": "today" | "stale",
  "data_date": "2026-07-08",
  "stale_note": "⚠️ 鱼盆数据最后更新于 2026-07-08（公众号未发布新文章）..." 或 null,
  "sector_rotation": { ... } | null,
  "yupen_trend": { ... } | null
}
"""
import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

YUPE_DIR = Path("/Users/guan/WorkBuddy/Claw/output/yupen")
SCRIPTS_DIR = Path("/Users/guan/WorkBuddy/Claw/.workbuddy/scripts")


def find_latest_valid(days: int = 30) -> dict[str, Path | None]:
    """Find the latest valid sector_rotation and yupen_trend JSON files."""
    if not YUPE_DIR.exists():
        return {"sector_rotation": None, "yupen_trend": None}

    sr_files = sorted(YUPE_DIR.glob("yupen_*_sector_rotation.json"), reverse=True)
    yt_files = sorted(YUPE_DIR.glob("yupen_*_yupen_trend.json"), reverse=True)

    cutoff = date.today()

    # Find valid files (not no_data, within days range)
    def _is_valid(fp: Path) -> bool:
        if not fp.exists():
            return False
        try:
            data = json.loads(fp.read_text())
            if data.get("status") == "no_data":
                return False
            # Check date freshness
            data_date = data.get("date", "")
            if data_date:
                dt = datetime.strptime(data_date, "%Y-%m-%d").date()
                if (cutoff - dt).days > days:
                    return False
            return "sectors" in data
        except Exception:
            return False

    result = {}
    for key, files in [("sector_rotation", sr_files), ("yupen_trend", yt_files)]:
        found = None
        for fp in files:
            if _is_valid(fp):
                found = fp
                break
        result[key] = found
    return result


def read_yupen_data(days: int = 30) -> dict:
    """Read yupen data and return structured result with freshness info."""
    files = find_latest_valid(days)
    today = date.today()

    sr_file = files.get("sector_rotation")
    yt_file = files.get("yupen_trend")

    # Use the most recent file to determine data_date
    all_valid = [f for f in [sr_file, yt_file] if f is not None]
    if not all_valid:
        return {
            "status": "no_data",
            "freshness": "none",
            "data_date": None,
            "stale_note": "output/yupen/ 目录无有效数据文件（已排除 no_data 占位文件）",
            "sector_rotation": None,
            "yupen_trend": None,
        }

    # Extract data from files
    sector_rotation = None
    yupen_trend = None
    data_date = None

    if sr_file:
        try:
            sector_rotation = json.loads(sr_file.read_text())
            data_date = sector_rotation.get("date", data_date)
        except Exception:
            pass

    if yt_file:
        try:
            yupen_trend = json.loads(yt_file.read_text())
            data_date = yupen_trend.get("date", data_date) or data_date
        except Exception:
            pass

    # Determine freshness
    if data_date:
        try:
            dt = datetime.strptime(data_date, "%Y-%m-%d").date()
            freshness = "today" if dt == today else "stale"
        except Exception:
            freshness = "unknown"
    else:
        freshness = "unknown"

    stale_note = None
    if freshness == "stale" and data_date:
        stale_note = (
            f"⚠️ 鱼盆模型数据日期为 {data_date}，滞后于文章发布日期（通常滞后1天），以下为最新有效数据"
        )

    return {
        "status": "ok",
        "freshness": freshness,
        "data_date": data_date,
        "stale_note": stale_note,
        "sector_rotation": sector_rotation,
        "yupen_trend": yupen_trend,
    }


def main():
    parser = argparse.ArgumentParser(description="鱼盆数据读取器")
    parser.add_argument("--freshness-only", action="store_true",
                        help="仅输出新鲜度状态")
    parser.add_argument("--days", type=int, default=30,
                        help="查找最近N天内的有效数据")
    args = parser.parse_args()

    result = read_yupen_data(days=args.days)

    if args.freshness_only:
        print(json.dumps({
            "freshness": result["freshness"],
            "data_date": result["data_date"],
            "stale_note": result["stale_note"],
        }, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
