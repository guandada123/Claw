#!/usr/bin/env python3
"""
wx_rss_auth.py — 微信公众号 RSS 认证与数据访问层 (v1.0)

后端：付费 RSS 服务 wechatrss.waytomaster.com (Basic 套餐)
凭证：~/.workbuddy/auth/wx_rss_api.sh (WX_RSS_API_KEY / SECRET / BASE / TOKEN / SUBS)

本模块为 wx_morning_report.py 提供三个契约函数：
  - get_subscriptions()         -> {"subscriptions": [{"fakeid", "nickname"}]}
  - fetch_all_articles(...)      -> (articles_list, ok)
  - fetch_article_content(art_id)-> markdown string (可能为空，触发微信验证时降级)

接口实测 (2026-07-13):
  - 订阅列表： GET  {BASE}/api/subscriptions                       (Bearer Token)
  - 文章列表： GET  {BASE}/api/rss/<fakeid>?token=<TOKEN>          (RSS 2.0 XML)
  - 单篇正文： POST {BASE}/api/article  body={"url": <文章链接>}     (Bearer Token)
               返回 data.{plain_content, content, images, ...} 稳定无限流（Basic 500次/小时）

依赖：requests（项目已装），xml.etree（标准库）
"""

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

import requests

# ── 凭证加载 ───────────────────────────────────────────────
_AUTH_FILE = Path.home() / ".workbuddy" / "auth" / "wx_rss_api.sh"

# 默认值（防止 shell source 失败时崩溃）
WX_RSS_API_KEY = ""
WX_RSS_API_SECRET = ""
WX_RSS_API_BASE = "https://wechatrss.waytomaster.com"
WX_RSS_TOKEN = ""
WX_RSS_SUBS = ""


def _load_auth():
    """从 wx_rss_api.sh 读取凭证（source 后导出环境变量）"""
    global WX_RSS_API_KEY, WX_RSS_API_SECRET, WX_RSS_API_BASE, WX_RSS_TOKEN, WX_RSS_SUBS
    if not _AUTH_FILE.exists():
        print(f"  ⚠️ RSS 凭证文件不存在: {_AUTH_FILE}", file=sys.stderr)
        return
    try:
        # source 脚本并把 export 的变量注入当前环境
        result = subprocess.run(
            ["bash", "-c", f"source '{_AUTH_FILE}' && env"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        for line in result.stdout.splitlines():
            if line.startswith("WX_RSS_API_KEY="):
                WX_RSS_API_KEY = line.split("=", 1)[1].strip().strip('"')
            elif line.startswith("WX_RSS_API_SECRET="):
                WX_RSS_API_SECRET = line.split("=", 1)[1].strip().strip('"')
            elif line.startswith("WX_RSS_API_BASE="):
                WX_RSS_API_BASE = line.split("=", 1)[1].strip().strip('"')
            elif line.startswith("WX_RSS_TOKEN="):
                WX_RSS_TOKEN = line.split("=", 1)[1].strip().strip('"')
            elif line.startswith("WX_RSS_SUBS="):
                WX_RSS_SUBS = line.split("=", 1)[1].strip().strip('"')
    except Exception as e:
        print(f"  ⚠️ 读取 RSS 凭证失败: {e}", file=sys.stderr)


_load_auth()

# 模块内缓存：art_id -> 文章链接 (供 fetch_article_content 使用)
_ARTICLE_URL_MAP = {}


def _headers() -> dict:
    h = {"Accept": "application/json"}
    if WX_RSS_TOKEN:
        h["Authorization"] = f"Bearer {WX_RSS_TOKEN}"
    return h


# ── 契约函数 ───────────────────────────────────────────────
def get_subscriptions() -> dict:
    """返回 {"subscriptions": [{"fakeid":..., "nickname":...}]}"""
    if not WX_RSS_API_BASE:
        return {"subscriptions": []}
    try:
        resp = requests.get(
            f"{WX_RSS_API_BASE}/api/subscriptions",
            headers=_headers(),
            timeout=15,
        )
        data = resp.json()
        subs = data.get("subscriptions", [])
        # 只取 wx_morning_report.py 需要的字段
        mapped = [
            {"fakeid": s.get("fakeid", ""), "nickname": s.get("nickname", "未知")}
            for s in subs
            if s.get("fakeid")
        ]
        return {"subscriptions": mapped}
    except Exception as e:
        print(f"  ⚠️ 获取订阅列表失败: {e}", file=sys.stderr)
        return {"subscriptions": []}


def fetch_all_articles(since: int = 0, limit: int = 200, fakeid: str = "") -> tuple:
    """拉取某公众号文章列表（RSS XML）

    Returns:
        (articles, ok) — articles 元素含 id/title/publish_time/_fakeid/author/link
    """
    if not WX_RSS_API_BASE or not fakeid:
        return ([], False)

    try:
        resp = requests.get(
            f"{WX_RSS_API_BASE}/api/rss/{fakeid}",
            params={"token": WX_RSS_TOKEN} if WX_RSS_TOKEN else {},
            timeout=20,
        )
        resp.raise_for_status()
        return _parse_rss_xml(resp.text, fakeid, limit), True
    except Exception as e:
        print(f"  ⚠️ 拉取文章列表失败 (fakeid={fakeid}): {e}", file=sys.stderr)
        return ([], False)


def _parse_rss_xml(xml_text: str, fakeid: str, limit: int) -> list:
    """解析 RSS 2.0 XML → 文章列表（含 id 映射缓存）"""
    import xml.etree.ElementTree as ET

    arts = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"  ⚠️ RSS XML 解析失败: {e}", file=sys.stderr)
        return arts

    channel = root.find("channel")
    if channel is None:
        return arts

    for item in channel.findall("item"):
        link_el = item.find("link")
        title_el = item.find("title")
        pub_el = item.find("pubDate")
        if link_el is None or not link_el.text:
            continue

        link = link_el.text.strip()
        title = (title_el.text or "").strip() if title_el is not None else ""
        art_id = link  # 用文章链接作为稳定 id

        pub_ts = 0
        if pub_el is not None and pub_el.text:
            try:
                dt = parsedate_to_datetime(pub_el.text)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                pub_ts = int(dt.timestamp())
            except Exception:
                pass

        _ARTICLE_URL_MAP[art_id] = link
        arts.append(
            {
                "id": art_id,
                "title": title,
                "publish_time": pub_ts,
                "_fakeid": fakeid,
                "author": "",  # nickname 由调用方按 fakeid 映射补
                "link": link,
            }
        )
        if len(arts) >= limit:
            break

    return arts


def fetch_article_content(art_id: str) -> str:
    """获取单篇文章正文（纯文本优先，适合 LLM 摘要）

    付费 RSS (Basic 套餐) 单篇接口稳定、无限流（实测 500次/小时配额，
    早报单次仅十余篇，远不会触发）。仅当接口返回失败 / 触发微信安全验证时降级为空。

    Returns:
        正文文本（plain_content 优先，回退 content HTML），失败返回空字符串
    """
    if not WX_RSS_API_BASE or not art_id:
        return ""

    url = _ARTICLE_URL_MAP.get(art_id)
    if not url:
        # 尝试把 art_id 当 url 直接用
        if art_id.startswith("http"):
            url = art_id
        else:
            return ""

    try:
        resp = requests.post(
            f"{WX_RSS_API_BASE}/api/article",
            headers={**_headers(), "Content-Type": "application/json"},
            json={"url": url},
            timeout=15,
        )
        data = resp.json()
        if data.get("success") and data.get("data"):
            d = data["data"]
            if isinstance(d, dict):
                # plain_content 纯文本最适 LLM；回退 content(HTML)
                return d.get("plain_content") or d.get("content") or d.get("text") or ""
            return str(d)
        else:
            # 触发安全验证等极少情况：静默降级
            return ""
    except Exception:
        return ""


# ── 兼容旧接口（若有直接调用）─────────────────────────────
def get_token() -> str:
    return WX_RSS_TOKEN


if __name__ == "__main__":
    # 自检：打印订阅数与首账号文章数
    subs = get_subscriptions()
    print(f"✅ 订阅数: {len(subs.get('subscriptions', []))}")
    for s in subs.get("subscriptions", [])[:3]:
        arts, ok = fetch_all_articles(fakeid=s["fakeid"], limit=3)
        print(f"  [{s['nickname']}] 文章 {len(arts)} 篇, ok={ok}")
