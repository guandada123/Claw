#!/usr/bin/env python3
"""
wx_rss_local.py — 公众号文章本地轨适配模块（wechat-download-api @ localhost:5001）

2026-08-05 新增：付费云 RSS(wechatrss.waytomaster.com) 停更 7+ 天后的兜底/替代轨。
本地 wechat-download-api 具备完整「发现+正文」能力：
  - GET  /api/rss/subscriptions        → 订阅列表（发现层账号源）
  - GET  /api/rss/{fakeid}             → 单账号 RSS XML（文章列表/发现层）
  - POST /api/admin/history/fetch      → 按公众号拉历史文章（发现层增强）
  - POST /api/article {"url": ...}     → 单篇正文（含 plain_content 纯文本）
  - GET  /api/login/getqrcode          → 微信扫码登录（发现层前置，过期后需重登）

接口契约与 wx_rss_auth.py 对齐（get_subscriptions / fetch_all_articles /
fetch_article_content_ex），供 sync_wx_articles.py --source local 无侵入切换。

⚠️ 已知边界（08-05 实测）：
  - 微信登录过期(isExpired=true)时，/api/article 取正文仍大概率可用（过期凭证
    缓存兜底），但 RSS 轮询/历史抓取（发现层）需要新鲜登录 → 扫码重登后全恢复。
  - 本地轮询器活跃（last_poll 每 ~30min），登录有效时自动发现新文。
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import request as urlreq

LOCAL_BASE = "http://localhost:5001"
TIMEOUT = 25
# 本地 /api/article 限流预算（08-03 实测：gap 7.0s ≈8.5次/分 无限流；3.2s 会触发）
GAP_SEC = 7.0
_RETRY_SEC_RE = re.compile(r"(\d+)\s*秒")

# 模块内缓存：art_id -> 文章链接（对齐 wx_rss_auth 契约）
_ARTICLE_URL_MAP: dict[str, str] = {}


def _http_get(path: str, timeout: int = TIMEOUT) -> Any:
    """GET 本地 API，返回解析后的 JSON"""
    req = urlreq.Request(f"{LOCAL_BASE}{path}", headers={"Accept": "application/json"})
    with urlreq.urlopen(req, timeout=timeout) as resp:  # noqa: S310  # 本地可信服务
        return json.loads(resp.read().decode("utf-8"))


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


def fetch_all_articles(since: int = 0, limit: int = 200, fakeid: str = "") -> tuple:
    """拉取某公众号文章列表（本地 RSS XML），返回 (articles, ok)"""
    if not fakeid:
        return ([], False)
    try:
        # 本地 RSS 无需 token；导入 _parse_rss_xml 复用（契约一致）
        import wx_rss_auth as cloud

        xml_text = (
            urlreq.urlopen(  # noqa: S310
                urlreq.Request(
                    f"{LOCAL_BASE}/api/rss/{fakeid}", headers={"Accept": "application/xml"}
                ),
                timeout=TIMEOUT,
            )
            .read()
            .decode("utf-8")
        )
        arts = cloud._parse_rss_xml(xml_text, fakeid, limit)
        return arts, True
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️ 本地拉取文章列表失败 (fakeid={fakeid}): {e}", file=sys.stderr)
        return ([], False)


def fetch_article_content_ex(
    art_id: str, max_retry: int = 3, base_gap: float = GAP_SEC
) -> tuple[str, str]:
    """获取单篇正文（本地 /api/article），返回 (正文, 错误原因)。

    成功 err=""；失败 content="" 且 err 为原因。与云端版语义一致：
    安全验证不重试；限流退避重试；绝不吞错（空正文不落盘由调用方保证）。
    """
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
                    # 优先纯文本（本地返回 HTML 在 content、纯文本在 plain_content）
                    return (d.get("plain_content") or d.get("text") or d.get("content") or ""), ""
                return str(d), ""

            last_err = str(data.get("error") or data.get("message") or "unknown_error")

            if "安全验证" in last_err or "验证" in last_err and "环境" in last_err:
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
    # 自检：打印订阅数 + 登录状态 + 首个账号最新文章
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
    if subs:
        arts, ok = fetch_all_articles(limit=3, fakeid=subs[0]["fakeid"])
        print(f"首个账号《{subs[0]['nickname']}》ok={ok} 文章数={len(arts)}")
        for a in arts[:3]:
            print(
                f"  - {datetime.fromtimestamp(a['publish_time'], tz=timezone.utc).strftime('%m-%d %H:%M')} {a['title'][:40]}"
            )
