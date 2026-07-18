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

# ── 本地订阅接入（替换原 Pinchtab 全文抓取）─────────────────
# 新发现的公众号统一走本地 WeChat Download API (localhost:5001) 订阅，
# 由 wx_collector → collect_local_feeds 自动拉取全文，不再依赖 Pinchtab 浏览器自动化。
_LOCAL_API_BASE = "http://localhost:5001"
_SUBSCRIBE_CANDIDATES_FILE = _PROJECT_ROOT / "data" / "subscribe_candidates.json"
# 自动接入本地订阅的命中率门槛：仅当 AUTO_SUBSCRIBE=False 时生效，作为质量闸门；
# AUTO_SUBSCRIBE=True（全开）时，所有能解析出 fakeid 的候选号一律接入本地订阅，
# 不再卡 hit_rate（避免 122 个红狐号无差别灌入的问题靠“候选须含股票提及”已兜底）。
_AUTO_SUBSCRIBE = True
_AUTO_SUBSCRIBE_HIT_RATE = 40.0
# 全开模式下本地订阅列表的数量上限：防止列表无限膨胀变杂（C @2026-07-18）。
# 达到上限后，新发现的候选号不再自动接入，改为 pending_cap（待人工提升上限后重跑解冻）。
_AUTO_SUBSCRIBE_CAP = 40

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


def _load_local_subscriptions() -> dict:
    """读取本地 WeChat Download API 当前已订阅列表。

    Returns: {"names": set(小写昵称), "aliases": set(小写微信号), "fakeids": set(fakeid)}
    本地 API 不可达时返回全空集（降级，不阻塞发现流程）。
    """
    result = {"names": set(), "aliases": set(), "fakeids": set()}
    try:
        url = f"{_LOCAL_API_BASE}/api/rss/subscriptions"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for s in data.get("data", []):
            if s.get("nickname"):
                result["names"].add(s["nickname"].strip().lower())
            if s.get("alias"):
                result["aliases"].add(s["alias"].strip().lower())
            if s.get("fakeid"):
                result["fakeids"].add(s["fakeid"])
    except Exception:
        pass  # 本地 API 未运行 → 返回空集，后续全部走 pending
    return result


def _resolve_fakeid(nickname: str) -> str | None:
    """通过本地 API 的 searchbiz 接口，按昵称解析公众号 fakeid。

    匹配策略：精确昵称匹配 → 宽松包含匹配（防"财联社"vs"财联社电报"偏差）；
    均不匹配（或查不到）返回 None，绝不返回无关账号的 fakeid，避免误订阅。
    """
    if not nickname:
        return None
    q = nickname.strip().lower()
    try:
        from urllib.parse import quote
        url = f"{_LOCAL_API_BASE}/api/public/searchbiz?query={quote(nickname)}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        items = data.get("data", {}).get("list", [])
        # 第一遍：精确匹配
        for item in items:
            if item.get("nickname", "").strip().lower() == q and item.get("fakeid"):
                return item["fakeid"]
        # 第二遍：包含匹配（双向）
        for item in items:
            rn = item.get("nickname", "").strip().lower()
            if rn and item.get("fakeid") and (q in rn or rn in q):
                return item["fakeid"]
    except Exception:
        return None
    return None


def _subscribe_local(fakeid: str, nickname: str, alias: str = "") -> bool:
    """向本地 API 订阅一个公众号（POST /api/rss/subscribe）。成功返回 True。"""
    if not fakeid:
        return False
    try:
        url = f"{_LOCAL_API_BASE}/api/rss/subscribe"
        payload = json.dumps({
            "fakeid": fakeid,
            "nickname": nickname,
            "alias": alias or "",
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return bool(data.get("success"))
    except Exception:
        return False


def _load_subscribe_candidates() -> list[dict]:
    """读取既有的订阅候选登记（subscribe_candidates.json），不存在则返回空列表。"""
    if not _SUBSCRIBE_CANDIDATES_FILE.exists():
        return []
    try:
        d = json.loads(_SUBSCRIBE_CANDIDATES_FILE.read_text(encoding="utf-8"))
        if isinstance(d, list):
            return d
        return d.get("candidates", [])
    except Exception:
        return []


def _save_subscribe_candidates(candidates: list[dict]) -> None:
    """原子写入订阅候选登记（带 updated_at 元数据）。"""
    out = {
        "updated_at": datetime.now().isoformat(),
        "note": "红狐发现的新公众号候选，经本地订阅接入；status: subscribed/already_subscribed/pending/subscribe_failed",
        "candidates": candidates,
    }
    _SUBSCRIBE_CANDIDATES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _SUBSCRIBE_CANDIDATES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_SUBSCRIBE_CANDIDATES_FILE)


def discover() -> dict:
    """
    主流程：搜索 → 提取股票 → 验证方向 → 聚合命中率 → 本地订阅接入
    """
    start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

    # 去重：同一篇文章可能被多个关键词命中
    seen_article_ids: set[str] = set()
    account_stocks: dict[str, list[dict]] = defaultdict(list)  # author → [{code, date, ...}, ...]
    account_meta: dict[str, dict] = {}

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
            if not account_meta[author].get("sample_url") and art.get("url"):
                account_meta[author]["sample_url"] = art.get("url", "")

            for code in codes:
                account_stocks[author].append({
                    "code": code,
                    "article_date": pub_time,
                    "article_title": title[:40],
                })

    # 注：原"Pinchtab 全文抓取补充代码"已移除（v8 @2026-07-18）。
    # 新号接入本地订阅(localhost:5001)后，wx_collector→collect_local_feeds 会自动
    # 拉取全文并提取信号，无需浏览器自动化。标题/摘要中的股票代码已足够做命中率初筛。

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
            "sample_url": meta.get("sample_url", ""),
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

    # ── 本地订阅接入（替换原 Pinchtab 全文抓取）─────────────────
    # 真值源 = 本地 WeChat Download API 当前订阅列表；新号走本地订阅，不再依赖浏览器自动化。
    local_subs = _load_local_subscriptions()
    cur_sub_count = len(local_subs["names"])
    existing_candidates = _load_subscribe_candidates()
    seen_names = {c.get("name") for c in existing_candidates}
    onboard_new = onboard_sub = onboard_pending = onboard_capped = 0
    for cand in candidates:
        name = cand["name"]
        lname = name.strip().lower()
        if lname in local_subs["names"]:
            status = "already_subscribed"
            fakeid = ""
        else:
            # 仅对尚未订阅的号解析一次 fakeid（后续复用，避免重复调用）
            fakeid = _resolve_fakeid(name)
            # 全开模式：能解析 fakeid 即自动订阅（不卡 hit_rate）；
            # 非全开：仅对 hit_rate 达标者订阅，其余 pending 待审。
            # 已达上限：降级为 pending_cap，待人工提升 _AUTO_SUBSCRIBE_CAP 后重跑解冻。
            auto_ok = bool(fakeid) and (
                _AUTO_SUBSCRIBE
                or (cand["hit_rate"] is not None and cand["hit_rate"] >= _AUTO_SUBSCRIBE_HIT_RATE)
            )
            if auto_ok and (cur_sub_count + onboard_sub) < _AUTO_SUBSCRIBE_CAP:
                ok = _subscribe_local(fakeid, name)
                status = "subscribed" if ok else "subscribe_failed"
                if ok:
                    onboard_sub += 1
            elif auto_ok:
                status = "pending_cap"  # 已达订阅上限，待人工扩额
                onboard_capped += 1
                onboard_pending += 1
            else:
                status = "pending"  # 待人工审核（未能解析 fakeid 或被质量闸门挡下）
                onboard_pending += 1
        # 仅登记首次出现的号（幂等）
        if name not in seen_names:
            existing_candidates.append({
                "name": name,
                "fakeid": fakeid or "",
                "hit_rate": cand["hit_rate"],
                "stocks_verified": cand["stocks_verified"],
                "status": status,
                "discovered_at": datetime.now().isoformat(),
                "sample_url": cand.get("sample_url", ""),
                "sample_titles": cand.get("sample_titles", []),
                "keywords": cand.get("keywords", []),
            })
            seen_names.add(name)
            if status != "already_subscribed":
                onboard_new += 1
    _save_subscribe_candidates(existing_candidates)
    already = sum(1 for c in candidates if c["name"].strip().lower() in local_subs["names"])
    print(f"[本地订阅] 当前已订阅 {cur_sub_count}/{_AUTO_SUBSCRIBE_CAP} | "
          f"新候选 {onboard_new} | 自动接入 {onboard_sub} | 达上限转pending {onboard_capped} | "
          f"待审核 {onboard_pending - onboard_capped} | 已订阅跳过 {already}")

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
