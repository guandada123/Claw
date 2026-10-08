#!/usr/bin/env python3
"""append_insights.py — 把新产出的文章精读笔记合并进 article_insights.json

为 MCP 直取链路（wechatrss MCP）服务：自动化 session 用 MCP 读文章、LLM 产出
insight 后写到 /tmp/wx_insights_new.json，再由本脚本幂等合并落库。

去重键：source_file（MCP 文章用 "mcp:<id>"）+ title 双重判重，重复运行不会翻倍。

用法：
  python3 append_insights.py /tmp/wx_insights_new.json [--dry-run]

入参文件格式：JSON 数组，每项至少含 title / account / insight（其余字段可省，
缺失字段按下方 DEFAULT 补齐）。缺 insight 或 title 的条目会被跳过并计数，
不静默丢弃（退出码 2 表示存在被跳过的条目）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent  # /Users/guan/WorkBuddy/Claw
INSIGHTS_FILE = ROOT / ".workbuddy" / "data" / "article_insights.json"


def _mk_article_id(source_file: str) -> str:
    return hashlib.md5(source_file.encode()).hexdigest()[:12]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", help="新产出 insights 的 JSON 文件路径")
    ap.add_argument("--dry-run", action="store_true", help="只打印不写盘")
    args = ap.parse_args()

    src = Path(args.src)
    if not src.exists():
        print(f"❌ 输入文件不存在: {src}", file=sys.stderr)
        return 1

    try:
        new_items = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"❌ 输入文件解析失败: {e}", file=sys.stderr)
        return 1
    if not isinstance(new_items, list):
        print("❌ 输入必须是 JSON 数组", file=sys.stderr)
        return 1

    data = json.loads(INSIGHTS_FILE.read_text(encoding="utf-8")) if INSIGHTS_FILE.exists() else []
    if not isinstance(data, list):
        print("❌ article_insights.json 不是数组，拒绝写入", file=sys.stderr)
        return 1

    existing_sf = {x.get("source_file") for x in data if isinstance(x, dict)}
    existing_title = {x.get("title") for x in data if isinstance(x, dict)}

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    added, skipped = [], 0
    for it in new_items:
        if not isinstance(it, dict) or not it.get("title") or not it.get("insight"):
            skipped += 1
            continue
        sf = it.get("source_file") or f"mcp:{it.get('article_id', it['title'])}"
        if sf in existing_sf or it["title"] in existing_title:
            continue
        rec = {
            "article_id": it.get("article_id") or _mk_article_id(sf),
            "account": it.get("account", ""),
            "title": it["title"],
            "recorded_at": it.get("recorded_at") or datetime.now().strftime("%Y-%m-%d"),
            "source_file": sf,
            "read_at": now,
            "source": it.get("source", "wechatrss-mcp"),
            "insight": it["insight"],
        }
        added.append(rec)
        existing_sf.add(sf)
        existing_title.add(rec["title"])

    print(
        f"[append] 输入 {len(new_items)} 条 | 新增 {len(added)} 条 | 已存在跳过 "
        f"{len(new_items) - len(added) - skipped} 条 | 残缺跳过 {skipped} 条"
    )
    for r in added:
        print(f"   + {r['account']}《{r['title'][:30]}》")

    if args.dry_run or not added:
        return 2 if skipped else 0

    INSIGHTS_FILE.write_text(
        json.dumps(data + added, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[append] 已写入 {INSIGHTS_FILE}（总 {len(data) + len(added)} 条）")
    return 2 if skipped else 0


if __name__ == "__main__":
    raise SystemExit(main())
