#!/usr/bin/env python3
"""
investor_rss.py — InvestorRSS 投资信息聚合源 · 数据层 (v1.0, 2026-09-29)
================================================================
后端：https://investorrss.waytomaster.com/api/rss/feed?token=xxx
凭证：~/.workbuddy/auth/investor_rss.sh (INVESTOR_RSS_BASE / INVESTOR_RSS_FEED_PATH / INVESTOR_RSS_TOKEN)

原始源特点（09-29 实测 64 条 / 6.3h）：
  - 通道：财联社·电报快讯（一句话快讯，高频刷新）/ 财联社·深度文章（头条，700+ 字）/
          r/StockMarket（英文社区讨论）
  - title 带 `[来源] [类型]` 双前缀 → 解析时剥离，保留原始 title 到 title_raw
  - 同一事件会被反复刷新（快讯迭代）、同一政策会有多篇多角度（深度文章）→ 必须去重

本模块解决两件事（其余交给上层报告）：
  ① 主题级去重 —— L1 相似度合并（同事件反复刷新）+ L2 主题聚类（同政策多角度，只打标签不合并）
  ② 主题分流 —— 每条打 primary topic + tags，供早/晚报按段落择优取用，避免堆 3 条房贷贴息

契约函数：
  fetch_feed()                  -> (xml_text, err)
  parse_feed(xml_text)          -> (items, integrity)      字段完整性双闸
  dedupe_items(items, ...)      -> (kept, merges)          L1 相似度合并
  classify(topic_text)          -> (primary, tags)         L2 主题分流
  build(...)                    -> payload dict（可直接落盘/消费）

CLI:
  python3 investor_rss.py fetch  [--hours 24] [--out PATH] [--dry-run]
  python3 investor_rss.py brief  [--hours 24] [--since-last] [--dry-run]
  python3 investor_rss.py check  [--max-age-hours 12]     仅新鲜度/完整性自检
  python3 investor_rss.py selftest                        内置断言（去重/分类/解析）

⚠️ 纪律：非 200 / XML 解析失败 / 字段缺失 一律显式暴露，禁止静默返空。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

try:
    import requests
except ImportError:  # pragma: no cover - 环境缺依赖时给出可读错误而非 ImportError 堆栈
    print("🔴 缺少依赖 requests（Claw 环境应已安装）", file=sys.stderr)
    raise SystemExit(2) from None

# ── 路径 / 常量 ────────────────────────────────────────────
CLAW_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = CLAW_ROOT / ".workbuddy" / "data"
AUTH_FILE = Path.home() / ".workbuddy" / "auth" / "investor_rss.sh"
SEEN_FILE = DATA_DIR / "investor_rss_seen.json"
TZ_CN = timezone(timedelta(hours=8))

# 默认参数
DEF_HOURS = 24
DEF_MAX_AGE_HOURS = 12  # 最新条目距今超过此值 → 标记 STALE（源侧停摆/未刷新）
DEF_SIM_THRESHOLD = 0.72  # L1 合并阈值：只合并「同事件」，不合并「同主题不同角度」
MIN_SHORT_GRAMS = 8  # 短串 gram 数下限：太短则退回对称 Jaccard，不启用包含度
MIN_LEN_RATIO = 0.25  # 短/长 字符比下限：量级差过大则退回对称 Jaccard
REQUIRED_FIELDS = ("guid", "title", "link", "pub_ts")
TITLE_SHORT_LEN = 58  # 报告展示用标题截断长度（财联社深度文章 title 常含正文首段）
SEEN_KEEP_DAYS = 7

# ── 主题词表（顺序 = primary 优先级，先命中者胜）──────────
# 设计要点：
#   1) primary 只看**标题**（正文关键词会污染—— 实测「数据看盘」因正文提及房地产ETF 被判为房地产）
#      标题无命中时才回落到「标题+正文」，最后回落到通道（r/StockMarket → 海外社区）
#   2) 「海外宏观」须排在「债券市场」之前：否则「美国30年期国债收益率」会被国内债市规则抢走
#   3) 「港股」须排在「房地产」之前：否则「内房股走高」会被判成房地产
TOPIC_RULES: list[tuple[str, tuple[str, ...]]] = [
    (
        "货币政策",
        (
            "PSL",
            "抵押补充贷款",
            "再贷款",
            "逆回购",
            "MLF",
            "降准",
            "降息",
            "LPR",
            "存款准备金",
            "货币政策",
            "央行",
            "公开市场操作",
            "银行",
            "揽储",
            "存款",
            "居民购房贷款",
        ),
    ),
    (
        "海外宏观",
        (
            "美国",
            "美债",
            "美联储",
            "美财政部",
            "加息",
            "非农",
            "消费者信心",
            "职位空缺",
            "通胀",
            "CPI",
            "达拉斯联储",
            "美国财政部",
        ),
    ),
    ("债券市场", ("债市", "国债收益率", "中债", "信用债", "转债")),
    ("港股", ("恒指", "恒生", "南向", "港股通", "港股", "港交所", "内房股")),
    (
        "房地产",
        (
            "房贷",
            "贴息",
            "楼市",
            "内房",
            "首套",
            "保障房",
            "房企",
            "购房",
            "住房",
            "房地产",
            "银十",
            "金九",
        ),
    ),
    (
        "国内政策",
        (
            "财政部",
            "国资委",
            "发改委",
            "十五五",
            "商务部",
            "国务院",
            "证监会",
            "金融街论坛",
            "进博会",
            "政策性金融",
            "政策",
            "通知",
            "规划",
            "答记者问",
            "政府债",
            "净融资",
        ),
    ),
    (
        "A股盘面",
        (
            "涨停",
            "封板",
            "炸板",
            "成交额",
            "沪指",
            "深成指",
            "创业板",
            "复盘",
            "龙虎榜",
            "主力资金",
            "北向",
            "沪深股通",
            "连板",
            "板块",
            "数据看盘",
            "两融",
            "融资余额",
            "微盘股",
            "新股",
            "打新",
            "A股",
        ),
    ),
    (
        "海外市场",
        (
            "美股",
            "纳斯达克",
            "标普",
            "道指",
            "期指",
            "苹果",
            "英伟达",
            "微软",
            "甲骨文",
            "特斯拉",
            "谷歌",
            "亚马逊",
            "Meta",
            "OpenAI",
            "Anthropic",
            "NVDA",
            "星舰",
            "IPO",
            "黄仁勋",
            "苏姿丰",
        ),
    ),
    (
        "商品能源",
        (
            "原油",
            "布伦特",
            "油价",
            "黄金",
            "金矿",
            "可可",
            "铜",
            "天然气",
            "炼油",
            "油气",
            "期市",
            "期货",
            "石油",
        ),
    ),
    (
        "公司与个股",
        (
            "公告",
            "回购",
            "增持",
            "减持",
            "递表",
            "收购",
            "并购",
            "入股",
            "分红",
            "业绩",
            "市值",
            "子公司",
            "股东大会",
            "解禁",
            "中标",
            "拟购买",
        ),
    ),
    (
        "科技产业",
        (
            "人工智能",
            "算力",
            "半导体",
            "芯片",
            "固态电池",
            "创新药",
            "医疗器械",
            "机器人",
            "海风",
            "光伏",
            "储能",
            "数据中心",
            "低空经济",
            "GPU",
            "科创债",
        ),
    ),
    (
        "地缘政治",
        ("俄罗斯", "乌克兰", "伊朗", "关税", "301", "欧盟", "制裁", "黑海", "地缘", "委内瑞拉"),
    ),
]

# 归一化时须剥离的样板串（保留为文档参考；实际剥离见 _strip_boilerplate）
BOILERPLATE_NOTE = "见 _strip_boilerplate：剥 [标签] 外壳 / 保留 【】 文本 / 去发布套话"

_CN_DIGIT_MAP = {
    "一": "1",
    "二": "2",
    "三": "3",
    "四": "4",
    "五": "5",
    "六": "6",
    "七": "7",
    "八": "8",
    "九": "9",
    "十": "10",
}


class InvestorRssError(RuntimeError):
    """源侧失败（网络/非 200/解析）——上层须显式处理，不得当成「无内容」。"""


# ── 凭证加载 ───────────────────────────────────────────────
def load_creds() -> tuple[str, str, str]:
    """source 凭据脚本并取回三个变量；失败抛 InvestorRssError（不静默返空）。"""
    if not AUTH_FILE.exists():
        raise InvestorRssError(f"凭证文件不存在: {AUTH_FILE}")
    try:
        r = subprocess.run(
            [
                "bash",
                "-c",
                f"source '{AUTH_FILE}' && printf '%s\\n%s\\n%s' "
                '"$INVESTOR_RSS_BASE" "$INVESTOR_RSS_FEED_PATH" "$INVESTOR_RSS_TOKEN"',
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception as e:  # noqa: BLE001
        raise InvestorRssError(f"读取凭证异常: {e}") from e
    if r.returncode != 0:
        raise InvestorRssError(f"source 凭证失败(rc={r.returncode}): {r.stderr.strip()[:200]}")
    parts = r.stdout.split("\n")
    if len(parts) < 3:
        raise InvestorRssError("凭证变量读取不完整（期望 3 个值）")
    base, path, token = (p.strip() for p in parts[:3])
    if not base or not path or not token:
        raise InvestorRssError("凭证存在空值：BASE/PATH/TOKEN 均不可为空")
    return base, path, token


# ── 拉取 / 解析 ────────────────────────────────────────────
def fetch_feed(timeout: int = 45) -> str:
    """拉取 RSS XML 文本。非 200 / 空响应 一律抛错（铁律：禁止静默吞非 200）。"""
    base, path, token = load_creds()
    url = f"{base}{path}"
    try:
        resp = requests.get(
            url,
            params={"token": token},
            timeout=timeout,
            headers={"Accept": "application/xml, text/xml, */*"},
        )
    except Exception as e:  # noqa: BLE001
        raise InvestorRssError(f"请求失败: {type(e).__name__}: {e}") from e
    if resp.status_code != 200:
        raise InvestorRssError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    if not resp.text.strip():
        raise InvestorRssError("HTTP 200 但响应体为空")
    return resp.text


def _strip_boilerplate(title: str) -> str:
    """剥离 `[来源] [类型] [重要] 【标题】财联社X月X日电，正文` 这类叠层样板。

    与「整段剥 【】」的粗暴做法不同：这里**保留 【】 内部文本**（它常是真正的新闻眼），
    只去掉括号外壳；同时清掉句中夹着的「财联社X月X日电，」发布套话。
    """
    s = title.strip()
    s = re.sub(r"^(?:\s*\[[^\]]{0,30}\]\s*)+", "", s)  # 开头连续 [标签]
    s = re.sub(r"^【([^】]*)】\s*", r"\1 ", s)  # 【标题】 → 标题
    s = re.sub(r"财联社\s*\d{1,2}月\d{1,2}日(?:讯|电)[，,、:：]?\s*", "", s)
    s = re.sub(r"^财联社报道[，,]?\s*", "", s)
    s = re.sub(r"^据《[^》]+》报道[，,]?\s*", "", s)
    s = s.strip("　 \t，,。、：:；;！!？?—")
    return s or title.strip()


def _parse_pub(pub_text: str) -> int:
    try:
        dt = parsedate_to_datetime(pub_text)
    except (TypeError, ValueError):
        return 0
    if dt is None:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp())


def parse_feed(xml_text: str, limit: int = 500) -> tuple[list[dict], dict]:
    """解析 RSS 2.0 → (items, integrity)。integrity 记录总量与缺失字段统计。"""
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml_text)  # noqa: S314  # nosec B314 源为可信付费服务
    except ET.ParseError as e:
        raise InvestorRssError(f"XML 解析失败: {e}") from e

    channel = root.find("channel")
    if channel is None:
        raise InvestorRssError("XML 中缺少 <channel> 节点")

    build_date = (channel.findtext("lastBuildDate") or "").strip()
    items: list[dict] = []
    missing: dict[str, int] = dict.fromkeys(REQUIRED_FIELDS, 0)
    dropped = 0
    dropped_reasons: dict[str, int] = {}
    guid_fallback = 0

    def _drop(reason: str) -> None:
        nonlocal dropped
        dropped += 1
        dropped_reasons[reason] = dropped_reasons.get(reason, 0) + 1

    for it in channel.findall("item"):
        raw_title = (it.findtext("title") or "").strip()
        guid_el = (it.findtext("guid") or "").strip()
        link = (it.findtext("link") or "").strip()
        used_fallback = not guid_el and bool(link)  # 无 <guid> 时用 link 作稳定 id
        guid = guid_el or link
        pub_text = (it.findtext("pubDate") or "").strip()
        pub_ts = _parse_pub(pub_text)
        source = (it.findtext("source") or "").strip() or "未知来源"
        content = it.findtext("{http://purl.org/rss/1.0/modules/content/}encoded") or ""
        desc = it.findtext("description") or ""

        rec = {
            "guid": guid,
            "title": _strip_boilerplate(raw_title),
            "title_raw": raw_title,
            "link": link,
            "pub_ts": pub_ts,
            "pub_text": pub_text,
            "source": source,
            "text": _clean_html(content or desc),
        }
        for f in REQUIRED_FIELDS:
            if not rec.get(f):
                missing[f] += 1

        # 双闸：可定位（id）+ 可排序（时间），缺一不可；二者缺失必须显式计数而非静默通过
        if not guid:
            _drop("no_id")
            continue
        if not pub_ts:
            _drop("no_pubdate")
            continue
        if not raw_title:
            _drop("no_title")
            continue
        items.append(rec)
        if used_fallback:
            guid_fallback += 1  # 仅统计**实际可用**条目里的回落次数，避免被丢弃项污染口径
        if len(items) >= limit:
            break

    integrity = {
        "total": len(items) + dropped,
        "usable": len(items),
        "dropped": dropped,
        "dropped_reasons": dropped_reasons,
        "guid_fallback_to_link": guid_fallback,
        "missing_fields": {k: v for k, v in missing.items() if v},
        "last_build_date": build_date,
        "ok": bool(items) and dropped == 0,
    }
    return items, integrity


def _clean_html(s: str) -> str:
    s = re.sub(r"<br\s*/?>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    for ent, ch in (
        ("&nbsp;", " "),
        ("&amp;", "&"),
        ("&lt;", "<"),
        ("&gt;", ">"),
        ("&quot;", '"'),
        ("&#39;", "'"),
        ("&mdash;", "—"),
    ):
        s = s.replace(ent, ch)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


# ── L1 相似度合并 ──────────────────────────────────────────
def normalize_for_sim(text: str) -> str:
    """归一化：剥样板 → 去标点/空白 → 保留中日韩+字母+数字。"""
    s = _strip_boilerplate(text)
    s = s.lower()
    s = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", s)
    return s


def _grams(s: str, n: int = 2) -> set[str]:
    if len(s) < n:
        return {s} if s else set()
    return {s[i : i + n] for i in range(len(s) - n + 1)}


def jaccard(a: str, b: str, n: int = 2) -> float:
    ga, gb = _grams(a, n), _grams(b, n)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def containment(short: str, long_: str, n: int = 2) -> float:
    """短串 gram 被长串覆盖的比例（非对称）。用于「同一快讯，标题被截断」的场景。"""
    gs, gl = _grams(short, n), _grams(long_, n)
    if not gs:
        return 0.0
    return len(gs & gl) / len(gs)


def sim_score(a: dict, b: dict) -> float:
    """事件相似度 = 标题包含度（非对称）。

    为什么用非对称包含度而不是 Jaccard：同一事件被反复刷新时，常出现「一条短标题
    = 另一条长标题的子集」（如 `连涨6日！…5.60%…` 与 `…升至5.597%…`），
    Jaccard 会被长度差稀释到阈值以下而漏并。

    为什么**不**纳入正文：长文（尤其 r/StockMarket 英文长帖）共享金融词汇，
    正文包含度会把两条无关帖子并成一条。
    09-29 实测：`美光公司周二公布报告…`(1222字) 与 `$NVDA 正与保险公司合作…`(749字)
    正文包含度 0.776 > 阈值 → 假阳。正文信号收益低、风险高，故只判标题。
    """
    return _overlap(a["_simtitle"], b["_simtitle"])


def _overlap(x: str, y: str) -> float:
    """长度鲁棒的重叠度：短的一方 gram 被长的一方覆盖的比例。

    两道护栏防止「短串恰好被长串吞掉」的假阳：
      - 短串 gram 数 ≥ MIN_SHORT_GRAMS（太短无从判断 → 退回对称 Jaccard）
      - 短/长 字符比 ≥ MIN_LEN_RATIO（量级差太大 → 退回对称 Jaccard）
    """
    short, long_ = (x, y) if len(x) <= len(y) else (y, x)
    if len(_grams(short)) < MIN_SHORT_GRAMS or len(short) < MIN_LEN_RATIO * len(long_):
        return jaccard(x, y)
    return containment(short, long_)


def dedupe_items(
    items: list[dict], threshold: float = DEF_SIM_THRESHOLD
) -> tuple[list[dict], list[dict]]:
    """L1 主题级去重。

    规则：
      1. guid 精确去重（同 guid 保留 pub_ts 最新）
      2. 同一 `source` 内做相似度聚类（见 sim_score）：≥ threshold → 判为同事件
         保留较新者（事件最新状态）；仅当旧条目正文 ≥1.5 倍长时改留旧条目（信息量优先）
      3. 不同 source 之间不合并（快讯 vs 深度文章视角不同，保留两条更利于报告取用）

    Returns:
        (kept_items, merges) — merges 记录每次合并的 (kept_guid, dropped_guid, sim)
    """
    by_guid: dict[str, dict] = {}
    for it in items:
        old = by_guid.get(it["guid"])
        if old is None or it["pub_ts"] > old["pub_ts"]:
            by_guid[it["guid"]] = it
    # 按 pub_ts 降序 ⇒ 遍历时 `it` 恒比桶内候选**旧**
    uniq = sorted(by_guid.values(), key=lambda x: x["pub_ts"], reverse=True)

    for it in uniq:
        it["merged_count"] = 1
        it["merged_guids"] = []
        it["_simtitle"] = normalize_for_sim(it["title"])
        it["_simtext"] = normalize_for_sim(it["text"])

    kept: list[dict] = []
    merges: list[dict] = []
    buckets: dict[str, list[dict]] = {}
    for it in uniq:
        src = it["source"]
        target = None
        for cand in buckets.get(src, []):
            sim = sim_score(cand, it)
            if sim >= threshold:
                target = (cand, sim)
                break
        if target is None:
            buckets.setdefault(src, []).append(it)
            kept.append(it)
            continue
        cand, sim = target
        # 角色：`it` 较旧、`cand`（已在桶中）较新 → 默认留较新，旧条目正文 ≥1.5 倍长才改留旧
        len_older, len_newer = len(it["text"]), len(cand["text"])
        keep, drop = (it, cand) if len_older >= len_newer * 1.5 else (cand, it)
        keep["merged_count"] = keep.get("merged_count", 1) + drop.get("merged_count", 1)
        keep["merged_guids"] = [
            *keep.get("merged_guids", []),
            drop["guid"],
            *drop.get("merged_guids", []),
        ]
        if keep is it:  # 代表者由较新换成较旧 → 换桶
            buckets[src].remove(cand)
            buckets[src].append(it)
            kept.remove(cand)
            kept.append(it)
        merges.append(
            {
                "kept": keep["guid"],
                "dropped": drop["guid"],
                "sim": round(sim, 3),
                "kept_title": keep["title"][:60],
                "dropped_title": drop["title"][:60],
            }
        )
    kept.sort(key=lambda x: x["pub_ts"], reverse=True)
    for it in kept:
        it.pop("_simtitle", None)
        it.pop("_simtext", None)
    return kept, merges


# ── L2 主题分流 ────────────────────────────────────────────
def _kw_hit(kw: str, hay_lower: str) -> bool:
    """ASCII 关键词按词边界匹配，避免 `AI` 命中 `chairman` 这类子串假阳。"""
    if kw.isascii():
        return (
            re.search(rf"(?<![a-z0-9]){re.escape(kw.lower())}(?![a-z0-9])", hay_lower) is not None
        )
    return kw in hay_lower


def _match_topics(text: str) -> list[str]:
    hay = text.lower()
    return [topic for topic, kws in TOPIC_RULES if any(_kw_hit(k, hay) for k in kws)]


def classify(item: dict) -> tuple[str, list[str]]:
    """返回 (primary_topic, tags)。

    primary：先只看标题；标题无命中再放宽到「标题+正文」；仍无命中按通道回落。
    tags：标题+正文的全量命中，供上层做二级筛选（多标签，不参与排序）。
    """
    title_hits = _match_topics(item.get("title", ""))
    if title_hits:
        primary = title_hits[0]
    else:
        body_hits = _match_topics(f"{item.get('title', '')} {item.get('text', '')[:200]}")
        primary = body_hits[0] if body_hits else ""
    tags = _match_topics(f"{item.get('title', '')} {item.get('text', '')[:200]}")

    is_community = "stockmarket" in item.get("source", "").lower()
    if not primary:
        primary = "海外社区" if is_community else "其他"
    if primary == "其他" and is_community:
        primary = "海外社区"
    if not tags:
        tags = ["海外社区"] if is_community else ["其他"]
    return primary, tags


def channel_of(source: str) -> str:
    """从 source 文本推通道名（用于报告里标注来源）。"""
    s = source.lower()
    if "stockmarket" in s:
        return "r/StockMarket"
    if "深度" in source:
        return "财联社·深度文章"
    if "快讯" in source or "电报" in source:
        return "财联社·快讯"
    return source or "未知"


# ── 组装 ───────────────────────────────────────────────────
def build(
    hours: int = DEF_HOURS,
    threshold: float = DEF_SIM_THRESHOLD,
    max_age_hours: int = DEF_MAX_AGE_HOURS,
    now: datetime | None = None,
) -> dict:
    """完整链路：拉取 → 解析 → 时间窗裁剪 → 默认标注 → 去重 → 主题分流。"""
    now = now or datetime.now(TZ_CN)
    xml = fetch_feed()
    items, integrity = parse_feed(xml)
    # 时间基准：源侧 lastBuildDate 通常=抓取时刻，用「最新条目」更稳（避免时区假象）
    newest_ts = max((i["pub_ts"] for i in items), default=0)
    ref_ts = newest_ts or int(now.timestamp())
    cutoff = ref_ts - hours * 3600
    windowed = [i for i in items if i["pub_ts"] >= cutoff]

    for it in windowed:
        it["pub_time"] = datetime.fromtimestamp(it["pub_ts"], TZ_CN).strftime("%m-%d %H:%M")
        it["channel"] = channel_of(it["source"])
        it["is_important"] = "[重要]" in it["title_raw"] or "【重要】" in it["title_raw"]
        # 财联社深度文章的 title 常把正文首段也塞进来 → 报告里显示需截断
        it["title_short"] = it["title"][:TITLE_SHORT_LEN] + (
            "…" if len(it["title"]) > TITLE_SHORT_LEN else ""
        )

    kept, merges = dedupe_items(windowed, threshold=threshold)
    topic_groups: dict[str, list[str]] = {}
    for it in kept:
        primary, tags = classify(it)
        it["topic"], it["tags"] = primary, tags
        topic_groups.setdefault(primary, []).append(it["guid"])

    age_min = (ref_ts - newest_ts) / 60 if newest_ts else None
    stale = age_min is None or age_min > max_age_hours * 60
    stale_reason = ""
    if stale:
        stale_reason = (
            "窗口内无可用条目"
            if age_min is None
            else f"最新条目距今 {age_min:.0f} 分钟（阈值 {max_age_hours}h）"
        )

    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S%z"),
        "source": "InvestorRSS",
        "window": {
            "hours": hours,
            "start": datetime.fromtimestamp(ref_ts - hours * 3600, TZ_CN).strftime(
                "%Y-%m-%d %H:%M"
            ),
            "end": datetime.fromtimestamp(ref_ts, TZ_CN).strftime("%Y-%m-%d %H:%M"),
        },
        "raw_count_total": integrity["total"],
        "raw_count_window": len(windowed),
        "deduped_count": len(kept),
        "merged_count": len(merges),
        "integrity": integrity,
        "freshness": {
            "newest_age_min": round(age_min, 1) if age_min is not None else None,
            "stale": stale,
            "stale_reason": stale_reason,
        },
        "topic_counts": {k: len(v) for k, v in sorted(topic_groups.items())},
        "merges": merges,
        "items": kept,
    }


# ── seen 状态（跨早晚报不重复推送）─────────────────────────
def _load_seen() -> dict:
    if not SEEN_FILE.exists():
        return {"guids": {}}
    try:
        d = json.loads(SEEN_FILE.read_text())
        return d if isinstance(d, dict) and "guids" in d else {"guids": {}}
    except (json.JSONDecodeError, OSError):
        return {"guids": {}}


def save_seen(guids: list[str], now: datetime | None = None) -> None:
    now = now or datetime.now(TZ_CN)
    d = _load_seen()
    stamp = now.strftime("%Y-%m-%d")
    for g in guids:
        d["guids"][g] = stamp
    cutoff = (now - timedelta(days=SEEN_KEEP_DAYS)).strftime("%Y-%m-%d")
    d["guids"] = {k: v for k, v in d["guids"].items() if v >= cutoff}
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2))


def filter_unseen(items: list[dict]) -> list[dict]:
    seen = _load_seen()["guids"]
    out = []
    for it in items:
        if it["guid"] in seen:
            continue
        out.append(it)
    return out


# ── 渲染 ───────────────────────────────────────────────────
TOPIC_ORDER = [t for t, _ in TOPIC_RULES] + ["海外社区", "其他"]

TOPIC_EMOJI = {
    "货币政策": "🏦",
    "房地产": "🏠",
    "国内政策": "📜",
    "A股盘面": "🇨🇳",
    "港股": "🇭🇰",
    "海外宏观": "🌐",
    "海外市场": "🇺🇸",
    "商品能源": "🛢️",
    "公司与个股": "🏢",
    "科技产业": "🔬",
    "地缘政治": "🌍",
    "海外社区": "💬",
    "其他": "📌",
}


def render_brief(payload: dict, per_topic: int = 6, with_stale: bool = True) -> str:
    lines: list[str] = []
    w = payload["window"]
    suppressed = payload.get("suppressed_count")
    head = (
        f"### 📰 外部信息源 · InvestorRSS（{w['start']} → {w['end']}）\n"
        f"- 原始 {payload['raw_count_window']} 条 → 去重后 **{payload['deduped_count']}** 条"
        f"（合并同事件 {payload['merged_count']} 条）"
    )
    if suppressed:
        head += f"；已跳过早报已消费的 {suppressed} 条"
    lines.append(head)
    if with_stale and payload["freshness"]["stale"]:
        lines.append(
            f"- ⚠️ **源新鲜度异常**：{payload['freshness']['stale_reason']}，"
            "以下内容视为存量信息，不得当实时信号"
        )
    if not payload["items"]:
        lines.append("- 📭 窗口内无可用条目")
        return "\n".join(lines)

    for topic in TOPIC_ORDER:
        group = [i for i in payload["items"] if i["topic"] == topic]
        if not group:
            continue
        lines.append(f"\n**{TOPIC_EMOJI.get(topic, '📌')} {topic}**（{len(group)} 条）")
        group.sort(key=lambda x: (x["is_important"], x["pub_ts"]), reverse=True)
        for it in group[:per_topic]:
            mark = "🔴 " if it["is_important"] else ""
            merge = (
                f"（+{it['merged_count'] - 1} 条同事件）" if it.get("merged_count", 1) > 1 else ""
            )
            title = it.get("title_short") or it["title"]
            lines.append(f"- [{it['pub_time']}] {mark}{title}{merge}")
        if len(group) > per_topic:
            lines.append(f"- …（另有 {len(group) - per_topic} 条同类）")
    return "\n".join(lines)


# ── CLI ────────────────────────────────────────────────────
def _out_path(now: datetime | None = None) -> Path:
    now = now or datetime.now(TZ_CN)
    return DATA_DIR / f"investor_rss_{now.strftime('%Y%m%d')}.json"


def cmd_fetch(args) -> int:
    payload = build(hours=args.hours, threshold=args.threshold)
    print(
        f"📡 原始 {payload['raw_count_window']} 条（全量 {payload['raw_count_total']}）"
        f" → 去重 {payload['deduped_count']} 条（合并 {payload['merged_count']}）"
    )
    print(f"🧭 主题分布: {payload['topic_counts']}")
    if payload["freshness"]["stale"]:
        print(f"⚠️ 新鲜度异常: {payload['freshness']['stale_reason']}")
    if not payload["integrity"]["ok"]:
        print(f"⚠️ 字段完整性告警: {json.dumps(payload['integrity'], ensure_ascii=False)}")
    if args.dry_run:
        print("🧪 dry-run：不落盘")
        return 0
    out = Path(args.out) if args.out else _out_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"✅ 已写入 {out}")
    return 0


def cmd_brief(args) -> int:
    payload = build(hours=args.hours, threshold=args.threshold)
    items = payload["items"]
    if args.since_last:
        before = len(items)
        items = filter_unseen(items)
        topic_groups: dict[str, int] = {}
        for it in items:
            topic_groups[it["topic"]] = topic_groups.get(it["topic"], 0) + 1
        payload = {
            **payload,
            "items": items,
            "deduped_count": len(items),
            "merged_count": sum(1 for i in items if i.get("merged_count", 1) > 1),
            "suppressed_count": before - len(items),
            "topic_counts": topic_groups,
        }
    print(render_brief(payload, per_topic=args.per_topic))
    if args.since_last and not args.dry_run and items:
        save_seen([i["guid"] for i in items])
        print(f"\n💾 seen 状态已更新（+{len(items)} guid）")
    return 0


def cmd_check(args) -> int:
    payload = build(hours=args.hours, threshold=args.threshold, max_age_hours=args.max_age_hours)
    ok = payload["integrity"]["ok"] and not payload["freshness"]["stale"] and payload["items"]
    print(
        json.dumps(
            {
                "freshness": payload["freshness"],
                "integrity": payload["integrity"],
                "window_count": payload["raw_count_window"],
                "deduped_count": payload["deduped_count"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print("✅ 源健康" if ok else "⚠️ 源异常（见上）")
    return 0 if ok else 1


def cmd_selftest(_args) -> int:
    """内置断言：不触网，只验解析/去重/分类逻辑。"""
    fails = []

    def check(name: str, cond: bool, extra: str = "") -> None:
        print(f"  {'✅' if cond else '🔴'} {name}{'' if cond else '  ' + extra}")
        if not cond:
            fails.append(name)

    # 1) 样板剥离
    s = _strip_boilerplate("[财联社 · 电报快讯] [电报快讯] [重要] 【连涨6日】美债升至5.60%")
    check("样板剥离", "美债升至5.60%" in s and "财联社" not in s, f"got={s!r}")

    # 2) 归一化 + 相似度：同事件（仅数字变化）应过阈值，异事件不应过
    a = normalize_for_sim("美国30年期国债收益率升至5.597%，创下自2002年以来的最高水平。")
    b = normalize_for_sim("美国30年期国债收益率升至5.60%，创下自2002年以来的最高水平。")
    c = normalize_for_sim("卡塔尔UCC油气公司洽谈进入委内瑞拉石油领域。")
    sim_ab, sim_ac = jaccard(a, b), jaccard(a, c)
    check("同事件相似度 > 阈值", sim_ab >= DEF_SIM_THRESHOLD, f"sim={sim_ab:.3f}")
    check("异事件相似度 < 阈值", sim_ac < DEF_SIM_THRESHOLD, f"sim={sim_ac:.3f}")

    # 3) L1 去重：同 source 同事件合并、异 source 不合并
    def mk(g: str, t: str, src: str, ts: int) -> dict:
        return {
            "guid": g,
            "title": t,
            "title_raw": t,
            "text": t,
            "source": src,
            "pub_ts": ts,
            "link": g,
        }

    same_event = [
        mk(
            "g1", "美国30年期国债收益率升至5.597%，创自2002年以来最高水平", "财联社 · 电报快讯", 100
        ),
        mk("g2", "美国30年期国债收益率升至5.60%，创自2002年以来最高水平", "财联社 · 电报快讯", 200),
        mk("g3", "卡塔尔UCC油气公司洽谈进入委内瑞拉石油领域", "财联社 · 电报快讯", 300),
    ]
    kept, merges = dedupe_items(same_event)
    check(
        "L1 合并同事件",
        len(kept) == 2 and len(merges) == 1,
        f"kept={len(kept)} merges={len(merges)}",
    )
    check(
        "L1 等长时保留较新 guid",
        any(k["guid"] == "g2" for k in kept),
        f"kept={[k['guid'] for k in kept]}",
    )

    # 3b) 旧条目正文明显更全（≥1.5 倍）→ 保留旧条目（信息量优先）
    long_old = mk("old", "美国30年期国债收益率创2002年以来最高", "财联社 · 电报快讯", 100)
    long_old["text"] = "美国30年期国债收益率升至5.60%，创下自2002年以来的最高水平。" * 3
    short_new = mk("new", "美国30年期国债收益率创2002年以来最高", "财联社 · 电报快讯", 200)
    kept_b, merges_b = dedupe_items([long_old, short_new])
    check(
        "L1 旧条目更全时保留旧条目",
        len(kept_b) == 1 and kept_b[0]["guid"] == "old" and merges_b[0]["dropped"] == "new",
        f"kept={[k['guid'] for k in kept_b]}",
    )

    cross = [
        mk("d1", "部分首套房10月起享国家贴息：100万贷款最高省5万", "财联社 · 深度文章", 100),
        mk("d2", "部分首套房10月起享国家贴息：100万贷款最高省5万", "财联社 · 电报快讯", 200),
    ]
    kept2, _ = dedupe_items(cross)
    check("跨 source 不合并", len(kept2) == 2, f"kept={len(kept2)}")

    # 3c) 标题被 `...` 截断（短标题是长标题的子集）→ 包含度兜底合并
    trunc_a = mk(
        "t1",
        "装运计划显示，构成即期布伦特基准的五个北海原油品种11月的供应...",
        "财联社 · 电报快讯",
        100,
    )
    trunc_b = mk(
        "t2",
        "装运计划显示，构成即期布伦特基准的五个北海原油品种11月的供应量预计平均为51.4万桶/日",
        "财联社 · 电报快讯",
        200,
    )
    kept_c, merges_c = dedupe_items([trunc_a, trunc_b])
    check(
        "标题截断时包含度兜底合并",
        len(kept_c) == 1 and len(merges_c) == 1,
        f"kept={[k['guid'] for k in kept_c]}",
    )

    # 3d) 回归：社区长帖共享金融词汇，不得因正文重叠被误并
    #     （09-29 实测假阳：美光帖 1222 字 vs NVDA 帖 749 字，正文包含度 0.776）
    p1 = mk(
        "p1", "美光公司周二公布报告。今年涨幅达到270%后，它无法承受任何失误", "r/StockMarket", 100
    )
    p1["text"] = (
        "Micron reports Tuesday. After a 270% run this year, it cannot afford any mistake. "
        "The company has been one of the best performers in the semiconductor space. " * 4
    )
    p2 = mk(
        "p2",
        "$NVDA 正与保险公司合作，通过在借款人违约时保护贷款人，使小型云服务提供商更容易获得GPU融资",
        "r/StockMarket",
        200,
    )
    p2["text"] = (
        "Nvidia has held talks with insurers about structures that could make it easier for "
        "smaller cloud providers to finance purchases of its GPUs. " * 4
    )
    kept_d, merges_d = dedupe_items([p1, p2])
    check(
        "社区长帖不因正文重叠误并",
        len(kept_d) == 2 and not merges_d,
        f"kept={[k['guid'] for k in kept_d]}",
    )

    # 4) guid 精确去重
    dup = [mk("x", "同一标题", "s", 100), mk("x", "同一标题", "s", 500)]
    kept3, _ = dedupe_items(dup)
    check("guid 精确去重", len(kept3) == 1 and kept3[0]["pub_ts"] == 500)

    # 5) 分类
    t1, _ = classify(
        {"title": "央行：下调抵押补充贷款（PSL）利率0.25个百分点", "text": "", "source": ""}
    )
    t2, _ = classify({"title": "【焦点复盘】市场极致地量反弹结束四连阴", "text": "", "source": ""})
    t3, _ = classify({"title": "Today's stock", "text": "", "source": "r/StockMarket"})
    check("分类-货币政策", t1 == "货币政策", t1)
    check("分类-A股盘面", t2 == "A股盘面", t2)
    check("分类-海外社区兜底", t3 == "海外社区", t3)

    # 6) 字段完整性双闸（可定位 id + 可排序时间，缺一即 drop 并计数）
    bad = """<?xml version="1.0"?><rss version="2.0"><channel><lastBuildDate>x</lastBuildDate>
      <item><title>ok</title><link>L1</link><guid>G1</guid>
        <pubDate>Tue, 29 Sep 2026 23:33:07 +0800</pubDate></item>
      <item><title>link-only</title><link>L2</link>
        <pubDate>Tue, 29 Sep 2026 23:33:07 +0800</pubDate></item>
      <item><title>no-id</title>
        <pubDate>Tue, 29 Sep 2026 23:33:07 +0800</pubDate></item>
      <item><title>no-date</title><link>L4</link></item>
      </channel></rss>"""
    parsed, integ = parse_feed(bad)
    check(
        "缺 <guid> 回落 link 仍可用并计数",
        integ["guid_fallback_to_link"] == 1 and any(p["guid"] == "L2" for p in parsed),
        f"fallback={integ['guid_fallback_to_link']}",
    )
    check(
        "无 id / 无 pubDate 条目被丢弃并分类计数",
        len(parsed) == 2
        and integ["dropped"] == 2
        and integ["dropped_reasons"] == {"no_id": 1, "no_pubdate": 1}
        and not integ["ok"],
        f"parsed={len(parsed)} dropped={integ['dropped']} reasons={integ['dropped_reasons']}",
    )

    print(f"\n{'✅ 全部通过' if not fails else '🔴 失败项: ' + ', '.join(fails)}")
    return 0 if not fails else 1


def main() -> int:
    p = argparse.ArgumentParser(description="InvestorRSS 投资信息聚合源 · 数据层")
    sub = p.add_subparsers(dest="cmd")

    for name, fn, helptext in (
        ("fetch", cmd_fetch, "拉取+去重+落盘"),
        ("brief", cmd_brief, "输出 Markdown 摘要（供报告取用）"),
    ):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("--hours", type=int, default=DEF_HOURS)
        sp.add_argument("--threshold", type=float, default=DEF_SIM_THRESHOLD)
        sp.add_argument("--dry-run", action="store_true")
        if name == "fetch":
            sp.add_argument("--out", help="输出 JSON 路径")
        else:
            sp.add_argument("--since-last", action="store_true", help="只输出未见过的条目")
            sp.add_argument("--per-topic", type=int, default=6)
        sp.set_defaults(func=fn)

    sp = sub.add_parser("check", help="新鲜度/完整性自检")
    sp.add_argument("--hours", type=int, default=DEF_HOURS)
    sp.add_argument("--threshold", type=float, default=DEF_SIM_THRESHOLD)
    sp.add_argument("--max-age-hours", type=int, default=DEF_MAX_AGE_HOURS)
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("selftest", help="内置断言（不触网）")
    sp.set_defaults(func=cmd_selftest)

    args = p.parse_args()
    if not getattr(args, "func", None):
        p.print_help()
        return 2
    try:
        return args.func(args)
    except InvestorRssError as e:
        print(f"🔴 InvestorRSS 源失败: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
