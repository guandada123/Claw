#!/usr/bin/env python3
"""
wx_rss_local.py — 公众号文章本地轨适配模块（wechat-download-api @ localhost:5001）

2026-08-05 新增：付费云 RSS(wechatrss.waytomaster.com) 停更 7+ 天后的兜底/替代轨。
本地 wechat-download-api 具备完整「发现+正文」能力：
  - GET  /api/rss/subscriptions        → 订阅列表（发现层账号源）
  - GET  /api/rss/all                  → 全账号聚合 RSS XML（发现层主通道，含 content:encoded 全文）
  - POST /api/admin/history/fetch      → 按公众号拉历史文章（发现层增强）
  - POST /api/article {"url": ...}     → 单篇正文（plain_content，兜底用）
  - GET  /api/login/getqrcode          → 微信扫码登录（发现层前置，过期后需重登）

接口契约与 wx_rss_auth.py 对齐（get_subscriptions / fetch_all_articles /
fetch_article_content_ex），供 sync_wx_articles.py --source local 无侵入切换。

⚠️ 已知边界（08-06 修正）：
  - 原 fetch_all_articles 用 per-account `/api/rss/{fakeid}` feed，重登后该端点对绝大多数
    账号返回 404（本地内部 ID 与微信 fakeid 映射在重登后失效）→ 同步永远 0 落盘。
    **08-06 修复**：改用聚合端点 `/api/rss/all`（一次拉全量 270+ 篇，含 content:encoded 全文），
    按标题前缀 `[昵称]` 关联账号过滤，正文直接从 content:encoded 提取（免逐篇 /api/article 慢抓）。
  - 微信登录过期(isExpired=true)时，发现层需新鲜登录；扫码重登后轮询器恢复发现。
  - 若轮询器卡住（如重登后仍未重新发现 07-20 之后的新文），需检查 wechat-download-api
    轮询进程/容器，非本模块问题。
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib import request as urlreq

LOCAL_BASE = "http://localhost:5001"
TIMEOUT = 25
# 本地 /api/article 限流预算（08-03 实测：gap 7.0s ≈8.5次/分 无限流；3.2s 会触发）
GAP_SEC = 7.0
_RETRY_SEC_RE = re.compile(r"(\d+)\s*秒")

# 模块内缓存
_ARTICLE_URL_MAP: dict[str, str] = {}          # art_id -> 文章链接（兼容契约）
_ALL_ITEMS_CACHE: list[dict] | None = None     # /api/rss/all 解析后的全量文章（含正文）
_NICK_MAP: dict[str, str] | None = None        # fakeid -> nickname


def _http_get(path: str, timeout: int = TIMEOUT) -> Any:
    """GET 本地 API，返回解析后的 JSON"""
    req = urlreq.Request(f"{LOCAL_BASE}{path}", headers={"Accept": "application/json"})
    with urlreq.urlopen(req, timeout=timeout) as resp:  # noqa: S310  # 本地可信服务
        return json.loads(resp.read().decode("utf-8"))


def _http_get_raw(path: str, timeout: int = TIMEOUT) -> str:
    """GET 本地 API，返回原始文本（用于 RSS XML）"""
    req = urlreq.Request(f"{LOCAL_BASE}{path}", headers={"Accept": "application/xml, application/rss+xml, */*"})
    with urlreq.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.read().decode("utf-8")


def _http_post(path: str, payload: dict, timeout: int = TIMEOUT) -> Any:
    """POST 本地 API，返回解析后的 JSON"""
    body = json.dumps(payload).encode("utf-8")
    req = urlreq.Request(
        f"{LOCAL_BASE}{path}",
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urlreq.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _cdata(text: str) -> str:
    """提取 CDATA 或原文本"""
    m = re.match(r"^\s*<!\[CDATA\[(.*)\]\]>\s*$", text, re.S)
    return m.group(1) if m else text


def _strip_html(html: str) -> str:
    """HTML → 纯文本（保留段落换行）"""
    if not html:
        return ""
    h = re.sub(r"<br\s*/?>", "\n", html)
    h = re.sub(r"</p>", "\n", h)
    h = re.sub(r"</div>", "\n", h)
    h = re.sub(r"<[^>]+>", "", h)
    h = re.sub(r"&nbsp;", " ", h)
    h = re.sub(r"&amp;", "&", h)
    h = re.sub(r"&lt;", "<", h)
    h = re.sub(r"&gt;", ">", h)
    h = re.sub(r"\n{3,}", "\n\n", h)
    return h.strip()


# ── 契约函数（与 wx_rss_auth 对齐）────────────────────────────
def get_subscriptions() -> dict:
    """返回 {"subscriptions": [{"fakeid":..., "nickname":...}]}"""
    try:
        data = _http_get("/api/rss/subscriptions")
        subs = data.get("data", []) if isinstance(data, dict) else []
        mapped = [
            {"fakeid": s.get("fakeid", ""), "nickname": s.get("nickname", "未知")}
            for s in subs
            if s.get("fakeid")
        ]
        return {"subscriptions": mapped}
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️ 本地获取订阅列表失败: {e}", file=sys.stderr)
        return {"subscriptions": []}


def _ensure_nick_map() -> dict[str, str]:
    """fakeid -> nickname（缓存）"""
    global _NICK_MAP
    if _NICK_MAP is None:
        _NICK_MAP = {
            s["fakeid"]: s["nickname"]
            for s in get_subscriptions().get("subscriptions", [])
        }
    return _NICK_MAP


def _ensure_all_cache(force: bool = False) -> list[dict]:
    """拉取 /api/rss/all 全量文章并解析（含正文），进程内缓存一次"""
    global _ALL_ITEMS_CACHE
    if _ALL_ITEMS_CACHE is not None and not force:
        return _ALL_ITEMS_CACHE
    xml = _http_get_raw("/api/rss/all")
    items = re.findall(r"<item>(.*?)</item>", xml, re.S)
    parsed: list[dict] = []
    for it in items:
        title_m = re.search(r"<title>(.*?)</title>", it, re.S)
        link_m = re.search(r"<link>(.*?)</link>", it, re.S)
        pub_m = re.search(r"<pubDate>(.*?)</pubDate>", it, re.S)
        enc_m = re.search(
            r"<content:encoded>(.*?)</content:encoded>", it, re.S
        )
        desc_m = re.search(r"<description>(.*?)</description>", it, re.S)
        title = _cdata(title_m.group(1)).strip() if title_m else ""
        link = link_m.group(1).strip() if link_m else ""
        if not link:
            continue
        pub_ts = 0
        if pub_m:
            try:
                pub_ts = int(parsedate_to_datetime(pub_m.group(1).strip()).timestamp())
            except Exception:  # noqa: BLE001
                pub_ts = 0
        raw_body = _cdata(enc_m.group(1)) if enc_m else (_cdata(desc_m.group(1)) if desc_m else "")
        content = _strip_html(raw_body)
        parsed.append(
            {
                "title": title,
                "link": link,
                "publish_time": pub_ts,
                "content": content,
            }
        )
        _ARTICLE_URL_MAP[link] = link
    _ALL_ITEMS_CACHE = parsed
    return parsed


def fetch_all_articles(since: int = 0, limit: int = 200, fakeid: str = "") -> tuple:
    """拉取某公众号文章列表（本地聚合 RSS），返回 (articles, ok)。

    08-06 修正：per-account feed(/api/rss/{fakeid}) 重登后 404，改用 /api/rss/all
    聚合源，按标题前缀 [昵称] 关联账号过滤；正文已在 content 字段，无需逐篇重抓。
    """
    if not fakeid:
        return ([], False)
    try:
        all_items = _ensure_all_cache()
        nick = _ensure_nick_map().get(fakeid, "")
        prefix = f"[{nick}]" if nick else None
        matched = []
        for it in all_items:
            if prefix and not it["title"].startswith(prefix):
                continue
            if since and it["publish_time"] and it["publish_time"] < since:
                continue
            matched.append(it)
        # 按发布时间倒序取最新 limit 篇
        matched.sort(key=lambda x: x["publish_time"] or 0, reverse=True)
        return matched[:limit], True
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️ 本地拉取文章列表失败 (fakeid={fakeid}): {e}", file=sys.stderr)
        return ([], False)


def fetch_article_content_ex(
    art_id: str, max_retry: int = 3, base_gap: float = GAP_SEC
) -> tuple[str, str]:
    """获取单篇正文，返回 (正文, 错误原因)。

    优先用 /api/rss/all 缓存的正文（命中即秒回，无网络）；未命中再走 /api/article 兜底。
    """
    # 1) 命中聚合缓存（最快，无网络）
    cached = _ALL_ITEMS_CACHE
    if cached is not None:
        for it in cached:
            if it.get("link") == art_id and it.get("content"):
                return it["content"], ""
    url = _ARTICLE_URL_MAP.get(art_id) or (art_id if art_id.startswith("http") else "")
    if not url:
        return "", "unresolvable_id"

    last_err = ""
    for attempt in range(max_retry):
        try:
            data = _http_post("/api/article", {"url": url})
            if data.get("success") and data.get("data"):
                d = data["data"]
                if isinstance(d, dict):
                    return (d.get("plain_content") or d.get("text") or d.get("content") or ""), ""
                return str(d), ""
            last_err = str(data.get("error") or data.get("message") or "unknown_error")
            if "安全验证" in last_err or ("验证" in last_err and "环境" in last_err):
                return "", last_err
            if "未登录" in last_err or "登录已失效" in last_err or "扫码" in last_err:
                return "", last_err  # 登录失效：需用户扫码，重试无用
            m = _RETRY_SEC_RE.search(last_err)
            if m or "过快" in last_err or "频繁" in last_err or "rate" in last_err.lower():
                wait = int(m.group(1)) + 1 if m else base_gap * (attempt + 1)
                wait = min(wait, 60)
                if attempt < max_retry - 1:
                    time.sleep(wait)
                    continue
            return "", last_err
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
            if attempt < max_retry - 1:
                time.sleep(base_gap * (attempt + 1))
                continue
    return "", last_err or "max_retry_exhausted"


def probe_status() -> dict:
    """探测本地 API 登录状态（供升级处置/诊断）"""
    try:
        return _http_get("/api/admin/status")
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


if __name__ == "__main__":
    # 自检：打印订阅数 + 登录状态 + 聚合源文章数 + 首个账号最新文章
    st = probe_status()
    print(
        "登录状态:",
        json.dumps(
            {k: st.get(k) for k in ("authenticated", "isExpired", "status") if k in st},
            ensure_ascii=False,
        ),
    )
    subs = get_subscriptions().get("subscriptions", [])
    print(f"本地订阅: {len(subs)}")
    all_items = _ensure_all_cache()
    print(f"聚合源文章总数: {len(all_items)}")
    if all_items:
        mx = max(all_items, key=lambda x: x["publish_time"] or 0)
        print(f"  最新发布: {datetime.fromtimestamp(mx['publish_time'], tz=timezone.utc).strftime('%Y-%m-%d %H:%M')} 《{mx['title'][:30]}》")
    if subs:
        arts, ok = fetch_all_articles(limit=3, fakeid=subs[0]["fakeid"])
        print(f"首个账号《{subs[0]['nickname']}》ok={ok} 匹配文章数={len(arts)}")
        for a in arts[:3]:
            print(
                f"  - {datetime.fromtimestamp(a['publish_time'], tz=timezone.utc).strftime('%m-%d %H:%M')} {a['title'][:40]} (正文{len(a.get('content',''))}字)"
            )
