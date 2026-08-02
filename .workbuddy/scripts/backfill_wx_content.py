#!/usr/bin/env python3
"""
backfill_wx_content.py — 回填 output/wx_articles/ 中正文缺失的历史文章

背景（2026-07-31 根因定位）：
  付费 RSS 单篇正文接口 /api/article 有**服务端防风控限流**，失败时返回
  success=False + error="请求过于频繁，请 N 秒后重试" / "文章获取过快..."。
  但旧 wx_rss_auth.fetch_article_content() 用 `except: return ""` 静默吞错，
  旧 sync_wx_articles.py 又把空正文照常落盘并把 url 记入去重集合，
  → 该文章永远不会被重抓，正文永久丢失。全库 641 篇中 600 篇（94%）是空壳。

  抓取层已修（退避重试 + 失败不落盘），本脚本负责把**历史空壳**救回来。

用法：
  python3 backfill_wx_content.py --dry-run          # 只统计不写盘
  python3 backfill_wx_content.py --max 50           # 本轮最多回填 50 篇
  python3 backfill_wx_content.py --max 50 --newest  # 优先回填最新的（默认最新优先）
  python3 backfill_wx_content.py --max 200 --gap 2.0

安全性：
  - 只修改 content_text / content_html / content_len / backfill_time 四个字段，
    不动 title/account/url/pub_time，不删除任何文件。
  - 抓取失败的文件原样保留，等下次再试（幂等，可反复执行）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
WX_DIR = ROOT / "output" / "wx_articles"
SYS = Path(__file__).resolve().parent

sys.path.insert(0, str(SYS))
import wx_rss_auth as rss  # noqa: E402

EMPTY_THRESHOLD = 50  # 正文短于此长度视为「缺失」


def collect_empty(newest_first: bool = True) -> list[Path]:
    out = []
    for f in WX_DIR.glob("*.json"):
        if "cache" in f.name:
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        body = (d.get("content_text") or "").strip()
        if len(body) < EMPTY_THRESHOLD and (d.get("url") or "").startswith("http"):
            out.append(f)
    out.sort(key=lambda p: p.stat().st_mtime, reverse=newest_first)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=40, help="本轮最多回填篇数")
    ap.add_argument("--gap", type=float, default=1.6, help="请求间隔秒（防风控）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--oldest", action="store_true", help="优先回填最旧的（默认最新优先）")
    args = ap.parse_args()

    if not WX_DIR.exists():
        print(f"⚠️ 目录不存在: {WX_DIR}")
        return 1

    empties = collect_empty(newest_first=not args.oldest)
    total_empty = len(empties)
    targets = empties[: args.max]
    print(f"[backfill] 空正文 {total_empty} 篇，本轮处理 {len(targets)} 篇，间隔 {args.gap}s")

    if args.dry_run:
        for f in targets[:20]:
            d = json.loads(f.read_text(encoding="utf-8"))
            print(f"  [dry] {d.get('account')}《{(d.get('title') or '')[:24]}》")
        return 0

    ok = fail = 0
    fail_reasons: dict[str, int] = {}
    for i, f in enumerate(targets, 1):
        d = json.loads(f.read_text(encoding="utf-8"))
        title = (d.get("title") or "")[:22]
        acct = d.get("account", "?")
        content, err = rss.fetch_article_content_ex(d["url"])
        if content.strip():
            d["content_text"] = content
            d["content_len"] = len(content)
            d["backfill_time"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            f.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
            ok += 1
            print(f"  [{i}/{len(targets)}] ✅ {acct}《{title}》{len(content)}字")
        else:
            fail += 1
            key = "安全验证" if "安全验证" in err else (err[:30] or "empty")
            fail_reasons[key] = fail_reasons.get(key, 0) + 1
            print(f"  [{i}/{len(targets)}] ✗ {acct}《{title}》{err[:40]}")
            # 撞上安全验证说明整体被风控，继续跑没意义，提前收工
            if "安全验证" in err and fail_reasons.get("安全验证", 0) >= 3:
                print("  ⚠️ 连续触发微信安全验证，提前终止本轮（30分钟后再试）")
                break
        time.sleep(args.gap)

    print(f"\n[backfill] 完成 | 成功 {ok} | 失败 {fail} | 剩余空正文约 {total_empty - ok}")
    if fail_reasons:
        print("[backfill] 失败原因分布: " + ", ".join(f"{k}×{v}" for k, v in fail_reasons.items()))
    print(f"SUMMARY: {json.dumps({'ok': ok, 'fail': fail, 'remaining': total_empty - ok}, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
