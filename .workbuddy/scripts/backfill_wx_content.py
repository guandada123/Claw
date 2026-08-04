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
import re
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

# 本地兜底下载 API（SSH 隧道，curl_cffi + Chrome TLS 指纹）
# 2026-08-03：付费云 RSS 后端整体故障（发现+正文双失效），实测本地 API 仍可取正文，
# 故增加「云RSS → 本地API」二级兜底。云端恢复后本路径自动闲置，无需回退。
LOCAL_API = "http://localhost:5001/api/article"


def _html_to_text(html: str) -> str:
    """微信正文 HTML → 纯文本（保留段落换行，丢弃图片/脚本/样式）"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()
    lines = [ln.strip() for ln in soup.get_text("\n").splitlines()]
    return "\n".join(ln for ln in lines if ln)


_RETRY_SEC_RE = re.compile(r"(\d+)\s*秒后重试")


def fetch_via_local_api(url: str, timeout: int = 45, max_retry: int = 3) -> tuple[str, str]:
    """本地下载 API 兜底取正文（限流感知：按服务端提示秒数退避重试）。

    Returns:
        (text, err) — err 非空即失败；「文章被删除/访问受限」为永久失败不重试
    """
    import requests

    err = ""
    for attempt in range(max_retry):
        try:
            r = requests.post(LOCAL_API, json={"url": url}, timeout=timeout)
            r.raise_for_status()
            j = r.json()
        except Exception as e:  # noqa: BLE001 网络异常统一降级为可重试失败
            err = f"本地API异常:{type(e).__name__}"
            time.sleep(2.0 * (attempt + 1))
            continue

        if j.get("success"):
            html = (j.get("data") or {}).get("content") or ""
            if not html.strip():
                return "", "本地API空正文"
            try:
                return _html_to_text(html), ""
            except Exception as e:  # noqa: BLE001
                return "", f"HTML解析失败:{type(e).__name__}"

        msg = str(j.get("error") or j.get("detail") or "")
        err = f"本地API:{msg[:40]}"
        # 限流 → 按服务端提示退避后重试；其余（删除/受限）为永久失败，直接返回
        if "Rate limited" in msg or "过快" in msg:
            m = _RETRY_SEC_RE.search(msg)
            time.sleep((int(m.group(1)) if m else 2) + 1.5)
            continue
        return "", err
    return "", err


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
    # 按文件名（YYYYMMDD_HHMMSS_标题）排序 = 按发布时间排序。
    # 2026-08-03：原按 mtime 排序会把「同账号批量落盘」聚成簇，若该账号文章整体失效
    # 会连续几十篇空跑；按发布时间倒序则优先救最新文章（正是「公众号精华」要用的）。
    out.sort(key=lambda p: p.name, reverse=newest_first)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=40, help="本轮最多回填篇数")
    ap.add_argument("--gap", type=float, default=3.2, help="请求间隔秒（防风控，本地API约10次/分，<3s必触发限流）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--oldest", action="store_true", help="优先回填最旧的（默认最新优先）")
    ap.add_argument(
        "--source",
        choices=["auto", "cloud", "local"],
        default="auto",
        help="正文来源：auto=云RSS优先失败转本地(连续3次失败后本轮直接走本地) / cloud=仅云 / local=仅本地",
    )
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
    ok_by_src: dict[str, int] = {}
    fail_reasons: dict[str, int] = {}
    cloud_dead = args.source == "local"  # 云端不可用判定（本轮内粘滞，避免反复空等）
    cloud_miss = 0

    for i, f in enumerate(targets, 1):
        d = json.loads(f.read_text(encoding="utf-8"))
        title = (d.get("title") or "")[:22]
        acct = d.get("account", "?")

        content, err, src = "", "", ""
        # ① 云 RSS 优先（除非已判失效 / 强制 local）
        if not cloud_dead:
            content, err = rss.fetch_article_content_ex(d["url"])
            src = "cloud"
            if not content.strip():
                cloud_miss += 1
                if args.source == "auto" and cloud_miss >= 3:
                    cloud_dead = True
                    print("  ⚠️ 云RSS连续3次取不到正文 → 本轮改走本地API兜底")
        # ② 本地 API 兜底
        if not content.strip() and args.source != "cloud":
            content, err2 = fetch_via_local_api(d["url"])
            src = "local"
            if not content.strip():
                err = f"云[{err[:22]}] 本地[{err2[:22]}]" if err else err2

        if content.strip():
            d["content_text"] = content
            d["content_len"] = len(content)
            d["content_source"] = src
            d["backfill_time"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            f.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
            ok += 1
            ok_by_src[src] = ok_by_src.get(src, 0) + 1
            print(f"  [{i}/{len(targets)}] ✅[{src}] {acct}《{title}》{len(content)}字")
        else:
            fail += 1
            key = "安全验证" if "安全验证" in err else (err[:30] or "empty")
            fail_reasons[key] = fail_reasons.get(key, 0) + 1
            print(f"  [{i}/{len(targets)}] ✗ {acct}《{title}》{err[:60]}")
        time.sleep(args.gap)

    src_desc = " ".join(f"{k}={v}" for k, v in ok_by_src.items()) or "-"
    print(f"\n[backfill] 完成 | 成功 {ok} ({src_desc}) | 失败 {fail} | 剩余空正文约 {total_empty - ok}")
    if fail_reasons:
        print("[backfill] 失败原因分布: " + ", ".join(f"{k}×{v}" for k, v in fail_reasons.items()))
    print(
        "SUMMARY: "
        + json.dumps(
            {"ok": ok, "fail": fail, "remaining": total_empty - ok, "by_source": ok_by_src},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
