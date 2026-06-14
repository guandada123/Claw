#!/usr/bin/env python3
"""
we-mp-rss API 客户端 — 从自部署的 we-mp-rss 服务拉取公众号文章
v4.0: 修复 API 路径 (v1/wx) + OAuth2 认证

用法:
  python3 fetch_wx_rss.py status
  python3 fetch_wx_rss.py list --limit 20
  python3 fetch_wx_rss.py list --mp-id MP_WXS_2394724034 --limit 5
  python3 fetch_wx_rss.py sync --hours 24 --output ~/articles/

认证:
  优先使用 OAuth2 Token（自动获取），
  已弃用旧的 Access Key Bearer 方式。
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

# 加载 Claw 公共库
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))
from errors import ConfigError

# --- 配置 ---
AUTH_FILE = Path.home() / ".workbuddy" / "auth" / "we_mp_rss.json"
TOKEN_FILE = Path.home() / ".workbuddy" / "cache" / "wx_rss_token.json"
CACHE_DIR = Path.home() / ".workbuddy" / "cache" / "wx_rss"


def _load_auth():
    if not AUTH_FILE.exists():
        raise ConfigError(
            f"认证文件不存在: {AUTH_FILE}。请创建该文件，包含 base_url、username、password 字段。"
        )
    creds = json.loads(AUTH_FILE.read_text())
    username = creds.get("username")
    password = creds.get("password")
    if not username or not password:
        raise ConfigError(f"认证文件 {AUTH_FILE} 中缺少 username 或 password 字段。")
    return (
        creds.get("base_url", "http://localhost:18001"),
        username,
        password,
    )


BASE_URL, WX_USER, WX_PASS = _load_auth()
API_BASE = "/api/v1/wx"

# 目标公众号 MP_ID 映射
ACCOUNT_IDS = {
    "投资明见": "MP_WXS_2394724034",
    "恩哥箴言": "MP_WXS_3686248075",
    "丹木说": "MP_WXS_3874969449",
    "好运侠客": "MP_WXS_3640837602",
    "猫笔叨": "MP_WXS_3905839574",
}

CACHE_DIR.mkdir(parents=True, exist_ok=True)


# --- Token 管理 ---
def _get_token() -> str | None:
    """获取 OAuth2 token，缓存1小时"""
    # 读缓存
    if TOKEN_FILE.exists():
        try:
            data = json.loads(TOKEN_FILE.read_text())
            if data.get("expires_at", 0) > time.time() + 60:
                return data["access_token"]
        except (json.JSONDecodeError, KeyError):
            pass

    # 重新登录
    try:
        data = urlencode(
            {
                "username": WX_USER,
                "password": WX_PASS,
            }
        ).encode()
        req = Request(
            f"{BASE_URL}{API_BASE}/auth/token",
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        with urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            token = result.get("access_token")
            if token:
                TOKEN_FILE.write_text(
                    json.dumps(
                        {
                            "access_token": token,
                            "expires_at": time.time() + 3600,  # 1小时
                        }
                    )
                )
                return token
    except Exception as e:
        print(f"⚠️ 登录失败: {e}", file=sys.stderr)
    return None


def api_request(endpoint: str, params: dict = None, method: str = "GET") -> dict:
    """调用 we-mp-rss API v1"""
    token = _get_token()
    if not token:
        return {"error": True, "message": "无法获取认证 Token"}

    url = urljoin(BASE_URL, f"{API_BASE}/{endpoint.lstrip('/')}")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    if params and method == "GET":
        query = urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{url}?{query}"
        req = Request(url, headers=headers)
    elif params and method == "POST":
        body = json.dumps(params).encode("utf-8")
        headers["Content-Type"] = "application/json"
        req = Request(url, data=body, headers=headers)
    else:
        req = Request(url, headers=headers)

    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        if e.code == 401:
            # Token 过期，清缓存重试一次
            if TOKEN_FILE.exists():
                TOKEN_FILE.unlink()
            token = _get_token()
            if token:
                return api_request(endpoint, params, method)
        return {"error": True, "status": e.code, "message": body}
    except URLError as e:
        return {"error": True, "message": str(e.reason)}


def check_status() -> dict:
    """检查 we-mp-rss 服务状态"""
    result = api_request("sys/info")
    if result.get("error"):
        return {"online": False, "error": result.get("message", "Unknown error")}
    return {"online": True, "data": result.get("data", result)}


def list_articles(mp_id: str = None, limit: int = 20, search: str = None) -> list:
    """获取文章列表"""
    params = {"limit": min(limit, 100)}
    if mp_id:
        params["mp_id"] = mp_id
    if search:
        params["search"] = search

    result = api_request("articles", params)
    if result.get("error"):
        print(f"⚠️ API 错误: {result.get('message')}", file=sys.stderr)
        return []
    data = result.get("data", result)
    return data.get("list", [])


def sync_recent(output_dir: str, hours: int = 24, mp_ids: list = None) -> list:
    """同步最近文章到本地 Markdown"""
    if mp_ids is None:
        mp_ids = list(ACCOUNT_IDS.values())

    os.makedirs(output_dir, exist_ok=True)
    downloaded = []

    for mp_id in mp_ids:
        account_name = {v: k for k, v in ACCOUNT_IDS.items()}.get(mp_id, mp_id)
        articles = list_articles(mp_id=mp_id, limit=10)

        if not articles:
            print(f"  📭 {account_name}: 无文章")
            continue

        for article in articles:
            aid = article.get("id") or article.get("article_id")
            title = article.get("title", "未知标题")
            if not aid:
                continue

            # 写 Markdown
            content = article.get("content", "")
            pub_time = article.get("publish_time", article.get("pub_time", ""))
            mp_name = article.get("mp_name", account_name)
            url = article.get("url", "")

            safe_title = "".join(c for c in title[:50] if c.isalnum() or c in " _-").strip() or aid
            filename = f"{safe_title}.md"
            filepath = os.path.join(output_dir, filename)

            # 如果已有同名文件，追加 ID
            counter = 1
            while os.path.exists(filepath):
                filename = f"{safe_title}_{counter}.md"
                filepath = os.path.join(output_dir, filename)
                counter += 1

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(f"# {title}\n\n")
                f.write(f"**公众号**: {mp_name}\n")
                f.write(f"**发布时间**: {pub_time}\n")
                if url:
                    f.write(f"**原文链接**: {url}\n")
                f.write("\n---\n\n")
                if content:
                    f.write(content)
                else:
                    f.write("> 正文暂未缓存，请通过原文链接查看\n")

            downloaded.append(
                {
                    "id": aid,
                    "title": title,
                    "mp_name": mp_name,
                    "mp_id": mp_id,
                    "filepath": filepath,
                    "published_at": pub_time,
                }
            )
            print(f"  ✅ [{mp_name}] {title[:40]}...")

    return downloaded


def cmd_status():
    status = check_status()
    if status["online"]:
        data = status.get("data", {})
        print(f"✅ we-mp-rss 服务在线 ({BASE_URL})")
        if isinstance(data, dict):
            for k, v in data.items():
                if not isinstance(v, (dict, list)):
                    print(f"   {k}: {v}")
    else:
        print(f"❌ 服务离线: {status.get('error')}")
        sys.exit(1)


def cmd_list(args):
    mp_id = args.mp_id
    if args.account:
        mp_id = ACCOUNT_IDS.get(args.account, args.account)

    articles = list_articles(mp_id=mp_id, limit=args.limit, search=args.search)
    if not articles:
        print("📭 无文章")
        return

    print(f"📰 共 {len(articles)} 篇文章:\n")
    for i, a in enumerate(articles, 1):
        title = a.get("title", "无标题")
        mp_name = a.get("mp_name", "未知")
        pub_time = a.get("publish_time", a.get("pub_time", ""))
        print(f"  {i}. [{mp_name}] {title}")
        if pub_time:
            print(f"     发布时间: {pub_time}")
        print()


def cmd_sync(args):
    mp_ids = None
    if args.accounts:
        mp_ids = [ACCOUNT_IDS.get(a, a) for a in args.accounts.split(",")]
    results = sync_recent(
        output_dir=args.output,
        hours=args.hours,
        mp_ids=mp_ids,
    )
    print(f"\n✅ 同步完成: {len(results)} 篇文章 → {args.output}")
    print(json.dumps(results, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description="we-mp-rss API 客户端 v4.0",
        epilog="认证: OAuth2 Token (凭据从 AUTH_FILE 读取)",
    )

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="检查服务状态")

    p_list = sub.add_parser("list", help="列出文章")
    p_list.add_argument("--account", help="公众号名称（投资明见/恩哥箴言等）")
    p_list.add_argument("--mp-id", help="公众号 MP_ID")
    p_list.add_argument("--limit", type=int, default=20)
    p_list.add_argument("--search", help="搜索关键词")

    p_sync = sub.add_parser("sync", help="同步最近文章到本地")
    p_sync.add_argument("--hours", type=int, default=24, help="无用（保留兼容）")
    p_sync.add_argument("--output", required=True, help="输出目录")
    p_sync.add_argument("--accounts", help="指定公众号，逗号分隔（默认全部5个）")

    args = parser.parse_args()

    if args.command == "status":
        cmd_status()
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "sync":
        cmd_sync(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
