#!/usr/bin/env python3
"""
sync_wx_articles.py — 公众号文章全量同步（落盘 output/wx_articles/）

修复 2026-07-29 诊断的「公众号精华只有4篇」根因：
  原 fetch_wx_rss.py(本地服务) 07-13 下线后，没有任何 ACTIVE 链路把
  付费云 RSS(wechatrss.waytomaster.com) 的文章落盘到 output/wx_articles/，
  导致文章池停在 07-17，read_wx_articles.py 每天只能读到历史残渣。

本脚本：
  1. 调 wx_rss_auth.get_subscriptions() 取订阅列表
  2. 逐账号 fetch_all_articles(since=最近N天) 拉文章列表
  3. 逐篇 fetch_article_content() 拉正文
  4. 增量落盘 output/wx_articles/<date>_<time>_<title>.json
     （格式兼容 read_wx_articles.py：title/account/content_text/content_html/url/pub_time/images/fetch_time）
  5. 已存在同 url 文件则跳过（增量，不重复落盘）

用法：
  python3 sync_wx_articles.py                 # 默认拉最近3天
  python3 sync_wx_articles.py --days 7       # 拉最近7天
  python3 sync_wx_articles.py --dry-run      # 只打印不写盘
  python3 sync_wx_articles.py --limit 10     # 每账号最多10篇

依赖：wx_rss_auth（付费云 RSS，凭证 ~/.workbuddy/auth/wx_rss_api.sh）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent  # /Users/guan/WorkBuddy/Claw
WX_DIR = ROOT / "output" / "wx_articles"
SYS = Path(__file__).resolve().parent

sys.path.insert(0, str(SYS))
import wx_rss_auth as rss  # noqa: E402

# 单篇正文接口的请求间隔（秒）。服务端有防风控限流，实测 0.3s 会大量触发
# 「文章获取过快 / 请求过于频繁」，1.5s 起步较稳（2026-07-31 实测定标）。
GAP_SEC = 1.5


def _safe_name(s: str, max_len: int = 40) -> str:
    s = re.sub(r"[\\/:*?\"<>|]", "_", s).strip()
    return s[:max_len] or "untitled"


def _existing_urls() -> set:
    """扫描已落盘文件，收集 url 集合用于增量去重"""
    urls = set()
    if not WX_DIR.exists():
        return urls
    for f in WX_DIR.glob("*.json"):
        if "cache" in f.name:
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if d.get("url"):
                urls.add(d["url"])
        except (OSError, ValueError):
            continue
    return urls


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3, help="拉取最近N天的文章")
    ap.add_argument("--limit", type=int, default=15, help="每账号最多拉几篇")
    ap.add_argument("--dry-run", action="store_true", help="只打印不写盘")
    args = ap.parse_args()

    WX_DIR.mkdir(parents=True, exist_ok=True)
    existing = _existing_urls()

    # 订阅列表
    subs = rss.get_subscriptions()
    accounts = subs.get("subscriptions", []) if isinstance(subs, dict) else []
    if not accounts:
        print("⚠️ 无订阅账号（get_subscriptions 空），检查 wx_rss_auth 凭证")
        return 1
    print(f"[sync] 订阅账号 {len(accounts)} 个，拉最近 {args.days} 天")

    since_ts = int((datetime.now(timezone.utc) - timedelta(days=args.days)).timestamp())
    total_new = 0
    total_skip = 0
    total_failed = 0
    failed_detail: list[tuple[str, str, str]] = []

    for acc in accounts:
        fid = acc.get("fakeid", "")
        nickname = acc.get("nickname", fid[:8])
        if not fid:
            continue
        arts, ok = rss.fetch_all_articles(since=since_ts, limit=args.limit, fakeid=fid)
        if not ok or not arts:
            print(f"  ⚠️ {nickname}: 拉取失败/空")
            continue
        for art in arts:
            url = art.get("link") or art.get("id") or ""
            if not url:
                continue
            if url in existing:
                total_skip += 1
                continue
            # 拉正文（2026-07-31：改用 _ex 版本区分「限流失败」与「真无正文」）
            if url.startswith("http"):
                content, err = rss.fetch_article_content_ex(url)
            else:
                content, err = "", "non_http_url"

            title_preview = (art.get("title") or "")[:24]
            if not content.strip():
                # 🔴 关键修复：抓取失败绝不落盘空壳，否则该 url 进入 existing 集合后
                #    永远不会被重抓，正文永久丢失（全库 94% 空正文的根因）。
                total_failed += 1
                failed_detail.append((nickname, title_preview, err or "empty_content"))
                print(f"  ✗ {nickname}《{title_preview}》抓取失败 → {err or 'empty'}（不落盘，留待下轮重抓）")
                time.sleep(GAP_SEC)
                continue

            pub_ts = art.get("publish_time", 0) or 0
            pub_dt = datetime.fromtimestamp(pub_ts, tz=timezone.utc) if pub_ts else datetime.now(tz=timezone.utc)
            # 用发布时间命名（无则当前）
            stamp = pub_dt.strftime("%Y%m%d_%H%M%S")
            title = (art.get("title") or "").strip() or "untitled"
            fname = f"{stamp}_{_safe_name(title)}.json"

            payload = {
                "title": title,
                "account": nickname,
                "pub_time": pub_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "url": url,
                "content_html": "",
                "content_text": content,
                "content_len": len(content),
                "images": [],
                "fetch_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f"),
            }
            if args.dry_run:
                print(f"  [dry] {nickname}《{title[:30]}》{len(content)}字")
            else:
                out = WX_DIR / fname
                out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                existing.add(url)
                total_new += 1
                print(f"  + {nickname}《{title[:24]}》{len(content)}字")
            # 限流：服务端有防风控（实测 0.3s 必触发「获取过快」），提到 1.5s
            time.sleep(GAP_SEC)

    print(
        f"[sync] 完成 | 新增 {total_new} 篇 | 跳过已存在 {total_skip} 篇 | 抓取失败 {total_failed} 篇"
        + (" (dry-run)" if args.dry_run else "")
    )
    if failed_detail:
        print("[sync] 失败明细（未落盘，下轮自动重抓）：")
        for acct, title, err in failed_detail[:20]:
            print(f"   - {acct}《{title}》{err}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
