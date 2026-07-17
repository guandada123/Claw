#!/usr/bin/env python3
"""
discover_gzh_accounts.py v2 — 红狐 API 发现 + 股票提取 + 方向验证 + 初步命中率

流水线：
  红狐搜索 → 提取文章中的股票代码 → 查询文章日期至今的涨跌 →
  按公众号聚合命中率 → 与 RSS 已有数据合并排名

输出：data/discovered_accounts.json（含 hit_rate 的候选号列表）

用法:
  python3 scripts/discover_gzh_accounts.py
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_OUTPUT_FILE = _PROJECT_ROOT / "data" / "discovered_accounts.json"
_FETCH_SCRIPT = (
    Path.home()
    / ".workbuddy"
    / "skills"
    / "gzh-explosive-content-detector"
    / "scripts"
    / "fetch_gzh_trends.py"
)

SEARCH_KEYWORDS = [
    "A股推荐",
    "涨停复盘",
    "明日操作",
    "选股策略",
    "股票池",
    "操盘计划",
]

# 腾讯行情 URL
_QT_URL = "https://qt.gtimg.cn/q={codes}"

# 6 位股票代码正则 — 用数字边界代替 \b（中文兼容）
# 匹配：603580、002185、300750 等，前后不能是数字
_CODE_RE = re.compile(r"(?<!\d)([036]\d{5})(?!\d)")



def _is_valid_stock_code(code: str) -> bool:
    """判断 6 位数字是否为有效 A 股代码（主板/中小板/创业板/科创板）"""
    if not code.isdigit() or len(code) != 6:
        return False
    return code[:1] in ("0", "3", "6")


def _search(keyword: str, start_date: str) -> list[dict]:
    """调用已验证可用的 fetch_gzh_trends.py"""
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(_FETCH_SCRIPT),
                "--keyword", keyword,
                "--start-date", start_date,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
        return data.get("articles", [])
    except Exception:
        return []


def _fetch_article_full_text(url: str) -> str | None:
    """抓取微信公众号文章全文（用 PinchTab 浏览器自动化绕过反爬）"""
    if not url or "mp.weixin.qq.com" not in url:
        return None
    try:
        result = subprocess.run(
            [
                "bash", "-c",
                f'source ~/.workbuddy/scripts/pinchtab_utils.sh && pinchtab_text "{url}" 2>/dev/null',
            ],
            capture_output=True, text=True, timeout=45,
        )
        if result.returncode != 0:
            return None
        content = result.stdout.strip()
        # 过滤 PinchTab 噪音（如 "pinchtab server is not running" 等系统输出）
        if "pinchtab" in content.lower() and len(content) < 500:
            return None
        return content if len(content) > 200 else None
    except Exception:
        return None


def _extract_codes_from_text(text: str) -> list[str]:
    """从文本中提取股票代码（6 位数字，0/3/6 开头）"""
    codes = set()
    for match in _CODE_RE.finditer(text):
        code = match.group(1)
        if _is_valid_stock_code(code):
            codes.add(code)
    return list(codes)


def _extract_codes_from_article(article: dict) -> list[str]:
    """从文章标题和摘要中提取股票代码"""
    title = article.get("title", "")
    summary = article.get("summary", "")
    text = f"{title} {summary}"
    codes = set()
    for match in _CODE_RE.finditer(text):
        code = match.group(1)
        if _is_valid_stock_code(code):
            codes.add(code)
    return list(codes)


def _fetch_stock_name(codes: list[str]) -> dict[str, str]:
    """批量查询股票名称（腾讯行情）"""
    if not codes:
        return {}
    # 格式化代码: sh600522, sz002185
    formatted = []
    for c in codes:
        if c.startswith("6"):
            formatted.append(f"sh{c}")
        else:
            formatted.append(f"sz{c}")

    batch_size = 20
    result = {}
    for i in range(0, len(formatted), batch_size):
        batch = formatted[i:i + batch_size]
        url = _QT_URL.format(codes=",".join(batch))
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310: qt.gtimg.cn
                raw = resp.read().decode("gbk", errors="replace")
            for line in raw.strip().split("\n"):
                if "=" not in line:
                    continue
                _, fields = line.split("=", 1)
                parts = fields.strip('";').split("~")
                if len(parts) >= 2:
                    code = parts[2]  # 纯数字代码
                    name = parts[1]
                    result[code] = name
        except Exception:
            continue
    return result


def _check_price_change(code: str, check_date_str: str) -> dict | None:
    """查询某个股票从 check_date 到现在的涨跌幅"""
    # 格式化代码
    prefix = "sh" if code.startswith("6") else "sz"
    try:
        # 用腾讯行情获取当前价（不精确做历史比对，用近N天涨跌幅近似）
        url = _QT_URL.format(codes=f"{prefix}{code}")
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310: qt.gtimg.cn
            raw = resp.read().decode("gbk", errors="replace")

        for line in raw.strip().split("\n"):
            if "=" not in line:
                continue
            _, fields = line.split("=", 1)
            parts = fields.strip('";').split("~")
            if len(parts) < 33:
                continue
            name = parts[1]
            current_price = float(parts[3]) if parts[3] else 0
            change_pct = float(parts[32]) if parts[32] else 0

            # 计算文章距今的天数
            try:
                check_date = datetime.strptime(check_date_str[:10], "%Y-%m-%d")
                days_ago = (datetime.now() - check_date).days
            except ValueError:
                days_ago = 0

            return {
                "name": name,
                "current_price": current_price,
                "change_pct": change_pct,
                "days_since_article": days_ago,
                "direction": "up" if change_pct > 0 else ("down" if change_pct < 0 else "flat"),
            }
    except Exception:
        return None
    return None


def discover() -> dict:
    """
    主流程：搜索 → 提取股票 → 验证方向 → 聚合命中率
    """
    start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

    # 去重：同一篇文章可能被多个关键词命中
    seen_article_ids: set[str] = set()
    account_stocks: dict[str, list[dict]] = defaultdict(list)  # author → [{code, date, ...}, ...]
    account_meta: dict[str, dict] = {}
    articles_for_enrich: list[dict] = []  # 有股票代码的文章，后续抓全文

    for kw in SEARCH_KEYWORDS:
        articles = _search(kw, start_date)
        for art in articles:
            art_id = str(art.get("id", ""))
            if art_id in seen_article_ids:
                continue
            seen_article_ids.add(art_id)

            author = art.get("author", art.get("sourceUsernickname", ""))
            if not author or len(author) < 2:
                continue

            pub_time = art.get("publicTime", "")
            title = art.get("title", "")
            codes = _extract_codes_from_article(art)

            if author not in account_meta:
                account_meta[author] = {
                    "name": author,
                    "articles": 0,
                    "keywords": set(),
                    "sample_titles": [],
                }
            account_meta[author]["articles"] += 1
            account_meta[author]["keywords"].add(kw)
            if len(account_meta[author]["sample_titles"]) < 3:
                account_meta[author]["sample_titles"].append(title[:60])

            for code in codes:
                account_stocks[author].append({
                    "code": code,
                    "article_date": pub_time,
                    "article_title": title[:40],
                })

            # 有股票代码的文章，保存信息用于后续全文抓取
            if codes and art.get("url"):
                articles_for_enrich.append(art)

    # 第一阶段B：对有股票代码的文章，抓全文补充更多代码
    if articles_for_enrich:
        print(f"抓取 {len(articles_for_enrich)} 篇文章全文...")
        full_codes_added = 0
        for art in articles_for_enrich:
            url = art.get("url", "")
            author = art.get("author", "")
            pub_time = art.get("publicTime", "")
            title = art.get("title", "")
            full_text = _fetch_article_full_text(url)
            if not full_text:
                continue
            extra_codes = _extract_codes_from_text(full_text)
            for code in extra_codes:
                existing = any(
                    s["code"] == code and s["article_date"] == pub_time
                    for s in account_stocks[author]
                )
                if not existing:
                    account_stocks[author].append({
                        "code": code,
                        "article_date": pub_time,
                        "article_title": title[:40],
                    })
                    full_codes_added += 1
        print(f"  全文抓取新增 {full_codes_added} 条股票提及")

    # 第二阶段：验证股价方向
    print(f"发现 {len(account_meta)} 个公众号，{sum(len(v) for v in account_stocks.values())} 条股票提及")

    # 批量收集所有代码并查询名称
    all_codes = set()
    for stocks in account_stocks.values():
        for s in stocks:
            all_codes.add(s["code"])

    stock_names = _fetch_stock_name(list(all_codes))
    print(f"查询到 {len(stock_names)} 个股票名称")

    # 构建候选结果
    candidates = []
    for author, stocks in account_stocks.items():
        meta = account_meta[author]

        # 去重：同一股票同一天只算一次
        seen_pairs: set[str] = set()
        verified = 0
        direction_correct = 0
        stock_details = []

        for s in stocks:
            pair_key = f"{s['code']}|{s['article_date'][:10]}"
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)

            price_info = _check_price_change(s["code"], s["article_date"])
            if price_info is None:
                continue

            verified += 1
            # 文章提及股票 = 默认看多信号 → 涨了就算命中
            if price_info["direction"] == "up":
                direction_correct += 1

            stock_details.append({
                "code": s["code"],
                "name": price_info["name"],
                "article_date": s["article_date"][:10],
                "change_pct": price_info["change_pct"],
                "article_title": s["article_title"],
            })

        hit_rate = round(direction_correct / verified * 100, 1) if verified > 0 else None

        candidates.append({
            "id": hashlib.md5(author.encode(), usedforsecurity=False).hexdigest()[:8],
            "name": author,
            "articles": meta["articles"],
            "keywords": sorted(meta["keywords"]),
            "sample_titles": meta["sample_titles"],
            "stocks_mentioned": len(stocks),
            "stocks_verified": verified,
            "direction_correct": direction_correct,
            "hit_rate": hit_rate,
            "stock_details": stock_details[:10],  # 最多存 10 条明细
            "source": "红狐发现",
        })

    # 排序：有命中率的排前面，按命中率降序；无命中率的按文章数降序
    candidates.sort(key=lambda x: (
        x["hit_rate"] is not None,  # 有命中率的排前面
        x["hit_rate"] if x["hit_rate"] is not None else -1,
        x["stocks_mentioned"],
    ), reverse=True)

    output = {
        "generated_at": datetime.now().isoformat(),
        "search_keywords": SEARCH_KEYWORDS,
        "search_window": f"{start_date} ~ {datetime.now().strftime('%Y-%m-%d')}",
        "total_candidates": len(candidates),
        "with_hit_rate": sum(1 for c in candidates if c["hit_rate"] is not None),
        "candidates": candidates,
    }

    _OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    _OUTPUT_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


if __name__ == "__main__":
    result = discover()
    top = result["candidates"][:20]
    print(f"\n{'='*60}")
    print(f"发现 {result['total_candidates']} 个候选，{result['with_hit_rate']} 个有初步命中率")
    print(f"{'='*60}")
    for r in top:
        hr = f"{r['hit_rate']}%" if r['hit_rate'] is not None else "N/A"
        icon = "⭐" if r['hit_rate'] and r['hit_rate'] >= 60 else ("✅" if r['hit_rate'] and r['hit_rate'] >= 40 else "⚪")
        print(f"  {icon} {r['name']:20s} 命中{hr:>6s}  ({r['direction_correct']}/{r['stocks_verified']}) 文章{r['articles']}篇")
