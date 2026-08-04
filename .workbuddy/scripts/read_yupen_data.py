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

from __future__ import annotations  # 兼容 3.9: X|Y 注解字符串化

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

YUPE_DIR = Path("/Users/guan/WorkBuddy/Claw/output/yupen")
SCRIPTS_DIR = Path("/Users/guan/WorkBuddy/Claw/.workbuddy/scripts")


def _load(fp: Path, days: int = 30) -> dict | None:
    """Load a yupen json if valid & fresh enough."""
    if not fp or not fp.exists():
        return None
    try:
        data = json.loads(fp.read_text())
        if data.get("status") == "no_data":
            return None
        dd = data.get("date", "")
        if dd:
            d = datetime.strptime(dd, "%Y-%m-%d").date()
            if (date.today() - d).days > days:
                return None
        if "sectors" not in data:
            return None
        return data
    except Exception:
        return None


def _latest(glob_pat: str, exclude_sub: str | None, days: int = 30, rich: bool = False):
    """Return (data, filepath) for the best valid file matching glob.
    exclude_sub: skip filenames containing this substring (e.g. '_primary_').
    rich=True: 优先板块数最多(用于 RSS 兜底源回填缺口, 允许跨日期取最全)；
    rich=False: 优先日期最新(用于主生成 primary 选取)。"""
    if not YUPE_DIR.exists():
        return None, None
    cands = []
    for fp in YUPE_DIR.glob(glob_pat):
        if exclude_sub and exclude_sub in fp.name:
            continue
        d = _load(fp, days)
        if d:
            cands.append((d, fp))
    if not cands:
        return None, None
    if rich:
        cands.sort(key=lambda x: (len(x[0].get("sectors", [])), x[0].get("date", "")), reverse=True)
    else:
        cands.sort(key=lambda x: x[0].get("date", ""), reverse=True)
    return cands[0]


def _merge_primary_rss(primary: dict | None, rss: dict | None) -> dict | None:
    """主生成(Wind)优先；RSS 仅补 Wind 未覆盖的缺口板块。"""
    if not primary:
        return rss
    if not rss:
        return primary
    pnames = {s["name"] for s in primary.get("sectors", [])}
    extra = [s for s in rss.get("sectors", []) if s["name"] not in pnames]
    if not extra:
        return primary
    base = max((s.get("rank", 0) for s in primary["sectors"]), default=0)
    for i, s in enumerate(extra, 1):
        s = dict(s)
        s["rank"] = base + i
        s.setdefault("src", "rss")
        extra[i - 1] = s
    merged = dict(primary)
    merged["sectors"] = primary["sectors"] + extra
    merged["_merged_from_rss"] = [s["name"] for s in extra]
    return merged


def _latest_raw(days: int = 3) -> dict | None:
    """读取最新 yupen_*_raw.json (RSS 抓取原始记录)，提取真实抓取信号。

    返回 dict: {date, has_images, fetch_time, status} 或 None。
    - date: 抓取日期(文章发布日)，用于判断 RSS 是否当天有新文章
    - has_images: image_paths 长度>0 表示抓到含鱼盆图表文章
    - 这是根治 v5 双重推送的关键：软失败检查应基于 raw 真实抓取，
      而非 primary 结构化表头日期(恒等于 today)。
    """
    if not YUPE_DIR.exists():
        return None
    cands = []
    for fp in YUPE_DIR.glob("yupen_*_raw.json"):
        try:
            d = json.loads(fp.read_text())
        except Exception:
            continue
        if d.get("status") == "no_data":
            continue
        rd = d.get("date", "")
        if not rd:
            continue
        try:
            rdate = datetime.strptime(rd, "%Y-%m-%d").date()
        except Exception:
            continue
        if (date.today() - rdate).days > days:
            continue
        cands.append((rd, d, fp))
    if not cands:
        return None
    cands.sort(key=lambda x: x[0], reverse=True)
    _, d, _ = cands[0]
    imgs = d.get("image_paths") or []
    return {
        "date": d.get("date"),
        "has_images": bool(imgs),
        "image_count": len(imgs),
        "fetch_time": d.get("fetch_time"),
        "status": d.get("status", "ok"),
    }


def read_yupen_data(days: int = 30) -> dict:
    """Read yupen data: prefer primary(Wind) file, fill gaps from RSS file."""
    today = date.today()

    # RSS 真实抓取信号 (根治 v5 双重推送: 软失败检查应基于此而非 primary 表头日期)
    raw = _latest_raw(days=3)
    rss_updated = bool(raw and raw["date"] == today.isoformat() and raw["has_images"])
    raw_date = raw["date"] if raw else None
    raw_has_images = raw["has_images"] if raw else False

    # 板块轮动: primary(Wind) 优先, 缺口从 RSS 补(取板块数最多者)
    p_sr, _ = _latest("yupen_primary_*_sector_rotation.json", None, days, rich=False)
    r_sr, _ = _latest("yupen_*_sector_rotation.json", "_primary_", days, rich=True)
    sector_rotation = _merge_primary_rss(p_sr, r_sr)

    # 鱼盆趋势
    p_yt, _ = _latest("yupen_primary_*_yupen_trend.json", None, days, rich=False)
    r_yt, _ = _latest("yupen_*_yupen_trend.json", "_primary_", days, rich=True)
    yupen_trend = _merge_primary_rss(p_yt, r_yt)

    if not sector_rotation and not yupen_trend:
        return {
            "status": "no_data",
            "freshness": "none",
            "data_date": None,
            "rss_updated": rss_updated,
            "raw_date": raw_date,
            "raw_has_images": raw_has_images,
            "stale_note": "output/yupen/ 目录无有效数据文件（已排除 no_data 占位文件）",
            "sector_rotation": None,
            "yupen_trend": None,
        }

    data_date = (sector_rotation or yupen_trend).get("date")
    if data_date:
        try:
            d = datetime.strptime(data_date, "%Y-%m-%d").date()
            freshness = "today" if d == today else "stale"
        except Exception:
            freshness = "unknown"
    else:
        freshness = "unknown"

    stale_note = None
    if freshness == "stale" and data_date:
        stale_note = f"⚠️ 鱼盆模型数据日期为 {data_date}，滞后于文章发布日期（通常滞后1天），以下为最新有效数据"

    return {
        "status": "ok",
        "freshness": freshness,
        "data_date": data_date,
        "rss_updated": rss_updated,
        "raw_date": raw_date,
        "raw_has_images": raw_has_images,
        "stale_note": stale_note,
        "sector_rotation": sector_rotation,
        "yupen_trend": yupen_trend,
    }


def main():
    parser = argparse.ArgumentParser(description="鱼盆数据读取器")
    parser.add_argument("--freshness-only", action="store_true", help="仅输出新鲜度状态")
    parser.add_argument("--days", type=int, default=30, help="查找最近N天内的有效数据")
    args = parser.parse_args()

    result = read_yupen_data(days=args.days)

    if args.freshness_only:
        print(
            json.dumps(
                {
                    "freshness": result["freshness"],
                    "data_date": result["data_date"],
                    "rss_updated": result.get("rss_updated"),
                    "raw_date": result.get("raw_date"),
                    "raw_has_images": result.get("raw_has_images"),
                    "stale_note": result["stale_note"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
