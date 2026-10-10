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
import re
import subprocess
import sys
import time
from datetime import UTC, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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
    global WX_RSS_API_KEY, WX_RSS_API_SECRET, WX_RSS_API_BASE, WX_RSS_TOKEN, WX_RSS_SUBS  # noqa: PLW0603
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
    """请求头：API Key 认证走 X-API-Key + X-API-Secret 双头。

    🔴 2026-10-01 根因修复（08-19~09-30 断供真因）：
    旧实现用 `Authorization: Bearer <JWT>`，服务端返回 401「无效的认证凭证」，
    据此误判为「凭证吊销/套餐到期」并停用整条云轨 42 天。实测真相：
      - `/api/subscriptions` 只认 **X-API-Key + X-API-Secret** 双头（→ 200，18 个订阅）
      - 只给 X-API-Key 缺 Secret 时，服务端明确回「还需要 X-API-Secret」
      - 带上 `Authorization: Bearer` 反而走 JWT 分支并 401（两者同时给也是 401）
      - `/api/rss/<fakeid>` 仍用 query `?token=<JWT>`（该接口不读这两个头）
    另注：WX_RSS_API_KEY 值未变，问题 100% 在认证姿势，不在凭证。
    """
    h = {"Accept": "application/json"}
    if WX_RSS_API_KEY and WX_RSS_API_SECRET:
        h["X-API-Key"] = WX_RSS_API_KEY
        h["X-API-Secret"] = WX_RSS_API_SECRET
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
            verify=False,  # noqa: S501  # nosec 本地RSS代理自签证书，禁用校验保障抓取(无敏感凭证)
        )
        if resp.status_code != 200:
            # 08-19 实测：token 失效时返回 401{"detail":"无效的认证凭证"}，
            # 旧逻辑静默吞成空列表 → 上游误判"未找到订阅"。此处显式暴露状态码。
            print(
                f"  ⚠️ 订阅列表接口 HTTP {resp.status_code}: {resp.text[:200]}",
                file=sys.stderr,
            )
            return {"subscriptions": []}
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
            verify=False,  # noqa: S501  # nosec 同上本地RSS代理自签证书
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
        root = ET.fromstring(xml_text)  # noqa: S314  # nosec B314  # RSS源为可信付费服务, 非用户不可信输入
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
        desc_el = item.find("description")
        if link_el is None or not link_el.text:
            continue

        link = link_el.text.strip()
        title = (title_el.text or "").strip() if title_el is not None else ""
        art_id = link  # 用文章链接作为稳定 id
        # 🔴 2026-10-01 实测：云端 feed 的 <description> 就是**完整正文 HTML**
        #   （实测单篇 1.0w~11w 字符），无需再逐篇 POST /api/article（该接口现返 401）。
        #   取到即入列表，同步脚本直接落盘，省掉「列表 + 逐篇正文」两段式搬运。
        content_html = (desc_el.text or "") if desc_el is not None else ""

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
                "content_html": content_html,
            }
        )
        if len(arts) >= limit:
            break

    return arts


# 来源前缀：wechatrss 生成的标题形如 `[猫笔刀] 服了，还有这种横财`
# 2026-10-10 实测：18 个标题前缀与 /api/subscriptions 的 18 个 nickname 完全一致；
# 而 <author> 字段 260/336 为空，不可用 → 还原来源只能靠标题前缀。
_SOURCE_PREFIX_RE = re.compile(r"^[\[【]([^\]】]{1,20})[\]】]\s*")


def _split_source(title: str) -> tuple:
    """把标题拆成 (来源名, 去前缀标题)。

    🔴 逐源接口 `/api/rss/<fakeid>` 返回的 title **不带**来源前缀，而全量 feed
    `/api/rss/all` 的 title **带** `[来源名] ` 前缀（该前缀即 nickname）。
    两条取数路径必须落盘出完全一致的 title → 此处统一剥掉前缀。

    无前缀时返回 ("", 原标题)。
    """
    t = title or ""
    m = _SOURCE_PREFIX_RE.match(t)
    if not m:
        return ("", t)
    return (m.group(1), t[m.end() :].strip())


def _group_by_source(arts: list, since: int = 0, limit: int = 200) -> tuple:
    """按标题来源前缀分组，并剥掉标题前缀。纯函数（不触网），供单测与全量 feed 复用。

    Returns:
        (groups, stats) — groups 形如 {nickname: [article, ...]}；
        stats 含 no_source（无前缀被丢弃的条数，单独计数以免污染"解析失败"口径）。
    """
    groups: dict = {}
    stats = {"no_source": 0}
    for a in arts:
        src, clean_title = _split_source(a.get("title", ""))
        if not src:
            stats["no_source"] += 1
            continue
        if since and a.get("publish_time", 0) and a["publish_time"] < since:
            continue
        a = dict(a)  # 复制，避免污染调用方对象
        a["title"] = clean_title  # 剥前缀：与逐源接口的 title 对齐
        a["_fakeid"] = ""  # 全量 feed 不含 fakeid，来源由前缀还原
        a["author"] = src
        g = groups.setdefault(src, [])
        if len(g) < limit:
            g.append(a)
    return groups, stats


def fetch_all_articles_grouped(since: int = 0, limit: int = 200) -> tuple:
    """一次请求 `/api/rss/all` 取回**全部订阅**的文章并按来源分组。

    🔴 存在意义（2026-10-11 增加）：`/api/subscriptions` 依赖**会轮换的 API Key**
    （2026-10-10 就因 key 轮换导致整条链路 401 断掉），而 `/api/rss/all` 只依赖
    **不轮换的 JWT**（该 token 无 exp，实测跨凭据轮换后仍有效）。
    另：N 个来源由「1 次订阅列表 + N 次逐源」压缩为 **1 次请求**。

    Returns:
        (groups, ok) — ok=False 表示请求失败或解析异常。
        调用方**必须**显式区分 ok=False 与"今天确实没文章"，不得把源故障静默成空结果。
    """
    if not WX_RSS_API_BASE or not WX_RSS_TOKEN:
        print("  ⚠️ 全量 feed 取数缺少 BASE/TOKEN", file=sys.stderr)
        return ({}, False)

    try:
        resp = requests.get(
            f"{WX_RSS_API_BASE}/api/rss/all",
            params={"token": WX_RSS_TOKEN},
            timeout=60,  # 全量 feed 为 7MB 量级，比逐源接口宽松
            verify=False,  # noqa: S501  # nosec B501 同上：RSS 代理自签证书
        )
        resp.raise_for_status()
        # limit 传"不截断"的大值：必须先解析完全量，才能按来源分组
        arts = _parse_rss_xml(resp.text, "_all_", 1_000_000_000)
    except Exception as e:
        print(f"  ⚠️ 拉取全量 feed 失败: {e}", file=sys.stderr)
        return ({}, False)

    if not arts:
        print("  ⚠️ 全量 feed 解析后为空", file=sys.stderr)
        return ({}, False)

    groups, stats = _group_by_source(arts, since=since, limit=limit)
    total = len(arts)
    if total and stats["no_source"] / total > 0.5:
        # 🔴 护栏：来源识别完全依赖标题前缀。若上游改了标题格式，绝大多数条目会解析不出
        # 来源 → 必须显式失败，绝不能"部分成功"地静默丢内容（这正是本系统最高频事故类型）。
        print(
            f"  ⚠️ 全量 feed 中 {stats['no_source']}/{total} 条无来源前缀（>50%），"
            "疑似上游标题格式变更 → 视为取数失败，本轮不落盘",
            file=sys.stderr,
        )
        return ({}, False)
    if stats["no_source"]:
        print(
            f"  ℹ️ 全量 feed 中 {stats['no_source']} 条无来源前缀，已跳过",
            file=sys.stderr,
        )
    return (groups, True)


_RETRY_SEC_RE = re.compile(r"(\d+)\s*秒后重试")


def fetch_article_content_ex(
    art_id: str, max_retry: int = 4, base_gap: float = 1.5
) -> tuple[str, str]:
    """获取单篇文章正文，返回 (正文, 错误原因)。

    2026-07-31 根因修复：付费 RSS 服务端**有防风控限流**（原注释「无限流」是错的）。
    实测失败返回 success=False + error 文案：
      - "请求过于频繁，请 N 秒后重试"   → 可退避重试，解析 N 秒后重试
      - "文章获取过快，请1秒后重试（防风控）" → 同上
      - "触发微信安全验证..."           → 需人工/等待30分钟，不重试
    旧实现 `except: return ""` 把限流与真失败一律吞成空正文，
    上游 sync_wx_articles 又把空正文当成功落盘 → 全库 94% 空正文（600/641）。

    Returns:
        (content, err)。成功时 err="" ；失败时 content="" 且 err 为原因文案。
    """
    if not WX_RSS_API_BASE or not art_id:
        return "", "no_api_base_or_id"

    url = _ARTICLE_URL_MAP.get(art_id)
    if not url:
        if art_id.startswith("http"):
            url = art_id
        else:
            return "", "unresolvable_id"

    last_err = ""
    for attempt in range(max_retry):
        try:
            resp = requests.post(
                f"{WX_RSS_API_BASE}/api/article",
                headers={**_headers(), "Content-Type": "application/json"},
                json={"url": url},
                timeout=20,
                verify=False,  # noqa: S501  # nosec 同上本地RSS代理自签证书
            )
            if resp.status_code == 401:
                # 认证失效（token 过期/吊销/套餐到期）：重试无意义，直接返回
                return "", f"auth_401: {resp.text[:120]}"
            data = resp.json()
            if data.get("success") and data.get("data"):
                d = data["data"]
                if isinstance(d, dict):
                    return (d.get("plain_content") or d.get("content") or d.get("text") or ""), ""
                return str(d), ""

            last_err = str(data.get("error") or data.get("message") or "unknown_error")

            # 微信安全验证：重试无用，立即返回
            if "安全验证" in last_err:
                return "", last_err

            # 限流：解析「请 N 秒后重试」，退避后再试
            m = _RETRY_SEC_RE.search(last_err)
            if m or "过快" in last_err or "频繁" in last_err:
                wait = int(m.group(1)) + 1 if m else base_gap * (attempt + 1)
                wait = min(wait, 40)  # 上限保护，避免单篇卡死
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


def fetch_article_content(art_id: str) -> str:
    """向后兼容包装：只要正文，失败返回空字符串。

    新代码请优先用 fetch_article_content_ex() 以拿到失败原因，
    避免把「限流失败」误当成「文章无正文」落盘。
    """
    content, _err = fetch_article_content_ex(art_id)
    return content


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
