#!/usr/bin/env python3
"""微信读书公众号文章采集 - agent-browser 管理登录态 + requests 调 API
登录态通过 agent-browser state save/load 持久化，只需用户手动登录一次。
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

# 加载 Claw 公共库
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))
from error_handler import atomic_write_json

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.expanduser("~/.workbuddy/auth/weread_browser_state.json")
OUTPUT_DIR = os.path.expanduser("~/.workbuddy/auth/weread_profile")
AB = str(
    Path.home()
    / ".workbuddy"
    / "binaries"
    / "node"
    / "versions"
    / "22.22.2"
    / "bin"
    / "agent-browser"
)

TARGETS = {
    "投资明见": "MP_WXS_2394724034",
    "恩哥箴言": "MP_WXS_3686248075",
    "丹木说": "MP_WXS_3874969449",
    "好运侠客": "MP_WXS_3640837602",
    "猫笔叨": "MP_WXS_3905839574",
}

# ── agent-browser 封装 ───────────────────────────────────────────────


def ab(args, timeout=30):
    r = subprocess.run(
        [AB] + list(args),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if r.returncode != 0 and r.stderr.strip():
        sys.stderr.write(f"[ab] FAIL {args[0]}: {r.stderr.strip()[:200]}\n")
    return r.stdout.strip(), r.returncode


def ab_json(args, timeout=30):
    stdout, code = ab(args + ["--json"], timeout=timeout)
    if code != 0 or not stdout:
        return {}
    try:
        return json.loads(stdout)
    except Exception:
        return {}


# ── 从 agent-browser state 文件提取 cookies ────────────────────────


def get_cookies_from_browser():
    """让 agent-browser 保存 state，从中提取 cookies 供 requests 使用"""
    # 先确保浏览器会话存在（打开一个页面）
    ab(["open", "https://weread.qq.com/"])
    time.sleep(1)

    tmp = tempfile.mktemp(suffix=".json")
    try:
        ab(["state", "save", tmp])
        if not os.path.exists(tmp):
            return None
        with open(tmp, encoding="utf-8") as f:
            data = json.load(f)

        # agent-browser state 格式同 Playwright storage_state
        cookies = {}
        for c in data.get("cookies", []):
            cookies[c["name"]] = c["value"]
        return cookies
    except Exception as e:
        sys.stderr.write(f"[get_cookies] error: {e}\n")
        return None
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except Exception:
            import sys

            sys.stderr.write("[weread] 临时文件清理失败，可忽略\n")


# ── Cookie 快速预检（无需启动浏览器）────────────────────────────────


def quick_cookie_check():
    """从保存的 state 文件读取 cookies，通过轻量 API 请求检测是否有效"""
    if not os.path.exists(STATE_FILE):
        return False, "STATE_FILE_NOT_FOUND"

    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return False, f"STATE_FILE_CORRUPT: {e}"

    cookies = {}
    for c in data.get("cookies", []):
        cookies[c["name"]] = c["value"]

    if not cookies:
        return False, "NO_COOKIES_FOUND"

    # 用已有 cookie 请求一个轻量 API endpoint 探活
    import requests

    try:
        resp = requests.get(
            "https://i.weread.qq.com/user/notebooks?count=1", cookies=cookies, timeout=8
        )
        # 200 = 有效，302/401 = 过期需重新登录
        if resp.status_code == 200:
            return True, "OK"
        elif resp.status_code in (302, 401):
            return False, "COOKIE_EXPIRED"
        else:
            return False, f"HTTP_{resp.status_code}"
    except requests.RequestException as e:
        return False, f"NETWORK_ERROR: {e}"


def check_only_mode():
    """--check-only 模式：仅检查 Cookie 是否有效，不执行采集"""
    valid, reason = quick_cookie_check()
    if valid:
        print("OK:COOKIE_VALID")
        return 0
    else:
        print(f"COOKIE_EXPIRED:{reason}")
        print("请按以下步骤重新登录：")
        print(f"  1. 终端执行：{AB} --headed open https://weread.qq.com/")
        print("  2. 在弹出的浏览器中用微信扫码登录")
        print(f"  3. 登录成功后执行：{AB} state save {STATE_FILE}")
        return 1


# ── 登录检测 ───────────────────────────────────────────────────────


def init_browser():
    if os.path.exists(STATE_FILE):
        ab(["state", "load", STATE_FILE])
    ab(["open", "https://weread.qq.com/"])
    ab(["wait", "--load", "networkidle"], timeout=20)


def check_login():
    """扫描页面 refs，存在「登录」链接则说明未登录"""
    data = ab_json(["snapshot", "-i"])
    refs = (data.get("data") or {}).get("refs") or {}
    return all((info.get("name") or "").strip() != "登录" for info in refs.values())


# ── 文章采集（使用 requests + 浏览器 cookies）────────────────────


def fetch_articles(book_id, cookies, max_offset=2):
    import requests

    articles = []
    for offset in range(max_offset):
        url = f"https://weread.qq.com/web/mp/articles?bookId={book_id}&offset={offset}"
        try:
            resp = requests.get(url, cookies=cookies, timeout=15)
        except Exception as e:
            sys.stderr.write(f"[fetch] request failed: {e}\n")
            break

        if resp.status_code != 200:
            break

        try:
            data = resp.json()
        except Exception:
            break

        reviews = data.get("reviews", [])
        if not reviews:
            break

        for review in reviews:
            for sub in review.get("subReviews") or []:
                mp = (sub.get("review") or {}).get("mpInfo") or {}
                if not mp.get("title"):
                    continue
                articles.append(
                    {
                        "title": mp.get("title", ""),
                        "content": mp.get("content", ""),
                        "author": mp.get("mp_name", ""),
                        "time": mp.get("time", 0),
                        "readNum": mp.get("readNum", 0),
                        "likeNum": mp.get("likeNum", 0),
                        "coverUrl": mp.get("pic_url", ""),
                        "originalId": mp.get("originalId", ""),
                    }
                )
    return articles


def filter_recent(articles, hours=24):
    cutoff = (datetime.now() - timedelta(hours=hours)).timestamp()
    return [a for a in articles if a.get("time", 0) > cutoff]


# ── 主流程 ──────────────────────────────────────────────────────────


def main():
    print("=" * 50)
    print(f"📰 微信读书文章采集 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)

    # 启动浏览器前先做快速 Cookie 预检
    valid, reason = quick_cookie_check()
    if not valid:
        print(f"❌ Cookie 预检失败: {reason}")
        print("COOKIE_EXPIRED")
        if reason == "STATE_FILE_NOT_FOUND":
            print("浏览器状态文件不存在，需要首次登录")
        elif reason == "COOKIE_EXPIRED":
            print("Cookie 已过期，需要重新扫码登录")
        print("请按以下步骤操作：")
        print(f"  1. 终端执行：{AB} --headed open https://weread.qq.com/")
        print("  2. 在弹出的浏览器中用微信扫码登录")
        print(f"  3. 登录成功后执行：{AB} state save {STATE_FILE}")
        print("  4. 之后本脚本可自动运行，无需重复登录")
        sys.exit(1)

    print("✅ Cookie 预检通过")

    init_browser()

    if not check_login():
        print("❌ 浏览器未登录微信读书")
        print("COOKIE_EXPIRED")
        print("请按以下步骤操作：")
        print(f"  1. 终端执行：{AB} --headed open https://weread.qq.com/")
        print("  2. 在弹出的浏览器中用微信扫码登录")
        print(f"  3. 登录成功后执行：{AB} state save {STATE_FILE}")
        print("  4. 之后本脚本可自动运行，无需重复登录")
        sys.exit(1)

    print("✅ 登录状态有效")

    cookies = get_cookies_from_browser()
    if not cookies:
        print("❌ 无法从浏览器获取 cookies")
        sys.exit(1)
    print(f"✅ 已获取 {len(cookies)} 个 cookie")

    all_articles = []
    stats = {}

    for name, book_id in TARGETS.items():
        print(f"\n🔍 {name}")
        try:
            articles = fetch_articles(book_id, cookies)
            recent = filter_recent(articles, hours=24)
            stats[name] = {"total": len(articles), "recent": len(recent)}

            for a in recent:
                ts = datetime.fromtimestamp(a["time"]).strftime("%m-%d %H:%M")
                print(f"   📄 [{ts}] {a['title'][:50]}... (阅{a['readNum']} 赞{a['likeNum']})")

            if not recent:
                latest = max((a["time"] for a in articles), default=0)
                if latest:
                    print(
                        "   最近一篇: "
                        f"{datetime.fromtimestamp(latest).strftime('%m-%d %H:%M')} "
                        f"— 超出24h窗口"
                    )
                else:
                    print("   未获取到任何文章")

            all_articles.extend(recent)

        except Exception as e:
            print(f"   ❌ 错误: {e}")
            stats[name] = {"total": 0, "recent": 0, "error": str(e)}

    # 保存浏览器状态（刷新 cookie 有效期）
    ab(["state", "save", STATE_FILE])

    # 保存结果
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    output = {
        "date": today,
        "fetch_time": datetime.now().isoformat(),
        "stats": stats,
        "articles": all_articles,
    }
    out_path = Path(os.path.join(OUTPUT_DIR, f"articles_{today}.json"))
    atomic_write_json(out_path, output)

    print(f"\n{'=' * 50}")
    print("📊 统计:")
    for name, s in stats.items():
        if "error" in s:
            print(f"   {name}: ❌ {s['error']}")
        else:
            print(f"   {name}: {s['recent']}/{s['total']} 篇 (24h内/总计)")
    print(f"📦 共 {len(all_articles)} 篇新文章")
    print(f"💾 已保存到 {out_path}")
    print(f"\n---ARTICLES_COUNT: {len(all_articles)}")


if __name__ == "__main__":
    # 支持 --check-only 快速预检模式
    if len(sys.argv) > 1 and sys.argv[1] == "--check-only":
        sys.exit(check_only_mode())
    main()
