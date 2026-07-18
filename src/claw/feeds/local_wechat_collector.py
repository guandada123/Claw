#!/usr/bin/env python3
"""
local_wechat_collector.py — 桥接本地 WeChat Download API → Claw 信号流水线

从自部署的 wechat-download-api (http://localhost:5001) 拉取公众号文章，
与 wx_collector.py 的付费 RSS 服务数据合并，共用同一套信号提取/同步逻辑。

使用方法：
    from claw.feeds.local_wechat_collector import collect_local_feeds
    articles = collect_local_feeds()  # 返回与 wx_collector 兼容的 [{title, content, account, pub_date}]
"""

import json
import logging
import os
import re
import time
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from xml.etree import ElementTree

import requests

logger = logging.getLogger(__name__)

# ── 本地 API 配置 ──────────────────────────────────────────
LOCAL_API_BASE = "http://localhost:5001"
LOCAL_API_TIMEOUT = 15  # 秒

# ── 兼容产出目录 ──────────────────────────────────────────
# 动态获取项目根（兼容 wx_collector 的导入方式）
_SCRIPT_DIR = Path(__file__).resolve().parent  # claw/feeds/
_PROJECT_ROOT = _SCRIPT_DIR.parent.parent.parent  # 项目根
OUTPUT_DIR = str(_PROJECT_ROOT / "output" / "wx_articles")


# ── 订阅列表 ──────────────────────────────────────────────


def get_local_subscriptions() -> list[dict]:
    """从本地 API 获取订阅的公众号列表"""
    try:
        resp = requests.get(
            f"{LOCAL_API_BASE}/api/rss/subscriptions",
            timeout=LOCAL_API_TIMEOUT,
        )
        if resp.status_code != 200:
            logger.warning("本地API订阅列表返回 %d", resp.status_code)
            return []
        data = resp.json()
        return data.get("data", [])
    except requests.ConnectionError:
        logger.warning("本地API不可达（localhost:5001 未运行）")
        return []
    except Exception as e:
        logger.warning("获取本地订阅列表失败: %s", e)
        return []


# ── RSS 解析 ──────────────────────────────────────────────


def fetch_rss_articles(fakeid: str, since_ts: int, limit: int = 30) -> list[dict]:
    """从本地 API 的 RSS feed 拉取文章列表。

    Returns:
        [{title, link, pubDate, author}]
    """
    try:
        resp = requests.get(
            f"{LOCAL_API_BASE}/api/rss/{fakeid}?limit={limit}",
            timeout=LOCAL_API_TIMEOUT,
        )
        if resp.status_code != 200:
            return []
        return _parse_rss_xml(resp.text, since_ts)
    except Exception as e:
        logger.debug("RSS解析失败 %s: %s", fakeid[:8], e)
        return []


def _parse_rss_xml(xml_text: str, since_ts: int) -> list[dict]:
    """解析 RSS 2.0 XML，提取文章列表，过滤时间戳。"""
    articles = []
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return articles

    for item in root.iter("item"):
        # 提取字段
        title_el = item.find("title")
        link_el = item.find("link")
        pub_el = item.find("pubDate")
        author_el = item.find("author")
        desc_el = item.find("description")

        title = (title_el.text or "") if title_el is not None else ""
        link = (link_el.text or "") if link_el is not None else ""
        author = (author_el.text or "") if author_el is not None else ""
        pub_str = (pub_el.text or "") if pub_el is not None else ""
        desc_html = (desc_el.text or "") if desc_el is not None else ""

        if not title or not link:
            continue

        # 解析发布时间
        pub_ts = 0
        if pub_str:
            try:
                pub_ts = int(parsedate_to_datetime(pub_str).timestamp())
            except Exception:
                pass

        # 时间过滤
        if pub_ts < since_ts:
            continue

        # 从 description 提取纯文本（去除 HTML）
        plain_text = re.sub(r"<[^>]+>", "", desc_html)[:3000] if desc_html else ""
        plain_text = unescape(plain_text)

        articles.append({
            "title": title[:80],
            "link": link,
            "author": author,
            "publish_time": pub_ts,
            "digest": plain_text[:500],
        })

    return articles


# ── 正文内容 ──────────────────────────────────────────────


def fetch_article_content(url: str) -> str | None:
    """从本地 API 获取文章正文（返回 plain_content 纯文本）。"""
    try:
        resp = requests.post(
            f"{LOCAL_API_BASE}/api/article",
            json={"url": url},
            timeout=LOCAL_API_TIMEOUT,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if data.get("success"):
            return data["data"].get("plain_content", "")[:3000]
    except Exception as e:
        logger.debug("获取文章正文失败: %s", str(e)[:80])
    return None


# ── 采集主入口 ────────────────────────────────────────────


def collect_local_feeds(lookback_hours: int = 48) -> list[dict]:
    """采集本地 API 所有订阅的今日/回溯时段文章。

    Args:
        lookback_hours: 回溯小时数（午前48h，午后24h，由调用方决定）

    Returns:
        与 wx_collector.load_today_articles() 兼容的 articles 列表：
            [{title, content, account, pub_date, _source}]
    """
    beijing_now = datetime.now(UTC) + timedelta(hours=8)
    today_bj = beijing_now.date()
    today_end_ts = int(datetime(
        today_bj.year, today_bj.month, today_bj.day,
        tzinfo=UTC,
    ).timestamp()) + 86400
    since_ts = today_end_ts - lookback_hours * 3600

    # 1. 获取订阅列表
    subs = get_local_subscriptions()
    if not subs:
        return []

    fakeid_map = {}
    for s in subs:
        fid = s.get("fakeid")
        if fid:
            fakeid_map[fid] = s.get("nickname", "未知")

    all_articles = []
    seen_links = set()
    account_counts: dict[str, int] = {}

    for fakeid in fakeid_map:
        nickname = fakeid_map[fakeid]
        account_counts[nickname] = 0
        # 2. RSS 拉取文章列表
        rss_articles = fetch_rss_articles(fakeid, since_ts, limit=30)
        if not rss_articles:
            continue

        for art in rss_articles:
            link = art.get("link", "")
            # 链接去重
            link_key = link.split("?")[0]
            if not link_key or link_key in seen_links:
                continue
            seen_links.add(link_key)

            # 3. 获取正文
            content = fetch_article_content(link) or art.get("digest", "")

            pub_ts = art.get("publish_time", 0)

            all_articles.append({
                "title": art.get("title", "")[:80],
                "content": content[:3000],
                "account": nickname,
                "pub_date": datetime.fromtimestamp(pub_ts, tz=UTC).isoformat() if pub_ts else "",
                "link": link,
                "_source": "local_api",
            })

            # 限制每号最多 5 篇（避免重复过多）
            account_counts[nickname] += 1
            if account_counts[nickname] >= 5:
                break

        # 温和延迟，避免冲爆本地 API
        time.sleep(0.5)

    return all_articles


def sync_local_to_cache(articles: list[dict]) -> int:
    """将本地 API 文章持久化到 output/wx_articles/，与 wx_collector 共用缓存。

    写入格式与 wx_collector.save_articles_to_cache() 完全兼容，
    确保 _load_from_local_cache() 回调时能正确读取。
    """
    if not articles:
        return 0

    try:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
    except Exception as e:
        logger.warning("创建缓存目录失败 %s: %s", OUTPUT_DIR, str(e)[:80])
        return 0

    now = datetime.now(UTC)
    stamp = now.strftime("%Y%m%d_%H%M%S")

    # 已存在标题集合
    existing = set()
    for fn in os.listdir(OUTPUT_DIR):
        if fn.endswith(".json"):
            try:
                d = json.loads(Path(OUTPUT_DIR, fn).read_text(encoding="utf-8"))
                existing.add(d.get("title", ""))
            except Exception:
                pass

    saved = 0
    for art in articles:
        title = (art.get("title") or "").strip()
        if not title or title in existing:
            continue
        safe = "".join(c for c in title if c not in r'\/:*?"<>|')[:40]
        base = f"{stamp}_{safe}"
        content = art.get("content", "") or ""
        account = art.get("account", "") or "未知公众号"
        pub_date = art.get("pub_date", "") or now.isoformat()

        payload = {
            "title": title,
            "content": content,
            "account": account,
            "pub_date": pub_date,
            "_source": art.get("_source", "local_api"),
        }
        try:
            Path(OUTPUT_DIR, f"{base}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            md = f"# {title}\n公众号：{account}\n\n{content}\n"
            Path(OUTPUT_DIR, f"{base}.md").write_text(md, encoding="utf-8")
            existing.add(title)
            saved += 1
        except Exception as e:
            logger.warning("缓存写入失败 %s: %s", art.get("title", "")[:20], str(e)[:80])
            continue
    return saved
