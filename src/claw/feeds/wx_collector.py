#!/usr/bin/env python3
"""
微信读书早报/晚报生成器 v2.0
- 早报：公众号文章 → LLM提取股票信号 → 结合持仓+技术面信号 → 操作建议
- 晚报：复盘早报建议 → 对照今日行情 → 总结经验 → 优化策略
- 集成 sim_trade.py 的 star_signal 技术面信号
- 输出到 stdout + 推送到飞书群

用法:
  python3 wx_morning_report.py --period morning   # 早报
  python3 wx_morning_report.py --period evening   # 晚报
"""
import contextlib
import hashlib
import json
import os
import re
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ── 系统模块导入路径（必须在 import wx_rss_auth 之前！） ─────
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent.parent.parent
SCRIPTS_DIR = str(_PROJECT_ROOT / "scripts")
WORKBUDDY_SCRIPTS = str(_PROJECT_ROOT / ".workbuddy" / "scripts")
if WORKBUDDY_SCRIPTS not in sys.path:
    sys.path.append(WORKBUDDY_SCRIPTS)  # for wx_rss_auth, star_signal_adapter, sim_trade
if SCRIPTS_DIR not in sys.path:
    sys.path.append(SCRIPTS_DIR)        # for router, cost_tracker (thin wrappers)
# ────────────────────────────────────────────────────────────

# 本地 WeChat Download API 桥接（自部署公众号文章源，与付费 RSS 互补）
try:
    from claw.feeds.local_wechat_collector import collect_local_feeds, sync_local_to_cache
    _HAS_LOCAL_FEEDS = True
except ImportError:
    # 降级：本地 API 不可用时不阻塞流程
    _HAS_LOCAL_FEEDS = False
    def collect_local_feeds(*args, **kwargs): return []
    def sync_local_to_cache(*args, **kwargs): return 0

# 微信 RSS 凭证（统一从 wx_rss_auth.py 加载，凭证文件：~/.workbuddy/auth/wx_rss_api.sh）
from wx_rss_auth import (  # noqa: E402
    fetch_all_articles,
    fetch_article_content,
    get_subscriptions,
)

# AI 摘要（接入 summarize 技能）
try:
    from summarize_batch import (
        summarize_article_content,  # noqa: F401 (used by _HAS_SUMMARIZE flag)
    )
    _HAS_SUMMARIZE = True
except ImportError:
    _HAS_SUMMARIZE = False

# ── 配置 ──────────────────────────────────────────────────
OUTPUT_DIR       = str(_PROJECT_ROOT / "output" / "wx_articles")

REPORT_DIR      = str(_PROJECT_ROOT / "output" / "wx_reports")
STRATEGY_FILE   = str(_PROJECT_ROOT / ".workbuddy" / "data" / "simulation" / "STRATEGY.md")
# 持仓数据权威源（数据治理铁律：单一权威源，物理隔离）
SIM_PORTFOLIO   = str(_PROJECT_ROOT / ".workbuddy" / "data" / "simulation" / "portfolio.json")
USER_PORTFOLIO  = str(_PROJECT_ROOT / ".workbuddy" / "data" / "user" / "portfolio.json")
# ────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# ── A股代码映射 ─────────────────────────────────────────────
BUILTIN_STOCKS = {
    "600498": "烽火通信", "600522": "中天科技", "600206": "有研新材",
    "002601": "龙佰集团", "002430": "杭氧股份", "000001": "平安银行",
    "002049": "紫光国微", "600460": "士兰微", "600176": "中国巨石",
    "003009": "中天火箭", "601899": "紫金矿业",
    "600519": "贵州茅台", "000858": "五粮液", "601318": "中国平安",
    "600036": "招商银行", "000333": "美的集团", "002594": "比亚迪",
    "600900": "长江电力", "000002": "万科A", "600030": "中信证券",
    "601166": "兴业银行", "601328": "交通银行", "601398": "工商银行",
    "601288": "农业银行", "601988": "中国银行", "600016": "民生银行",
    "601668": "中国建筑", "601800": "中国交建", "601186": "中国铁建",
    "600031": "三一重工", "000651": "格力电器", "603288": "海天味业",
    "002475": "立讯精密", "300750": "宁德时代", "002415": "海康威视",
    "000725": "京东方A", "600050": "中国联通", "600104": "上汽集团",
    "002304": "洋河股份", "603259": "药明康德", "600276": "恒瑞医药",
    "000568": "泸州老窖", "002714": "牧原股份", "600809": "山西汾酒",
    "603986": "兆易创新", "002230": "科大讯飞", "600570": "恒生电子",
    "300059": "东方财富", "601012": "隆基绿能", "300274": "阳光电源",
    "002129": "TCL中环", "603806": "福斯特", "300316": "晶盛机电",
    "002459": "晶澳科技", "688599": "天合光能", "300443": "金雷股份",
    "002080": "中材科技", "600438": "通威股份", "600089": "特变电工",
    "002202": "金风科技", "300772": "运达股份", "601615": "明阳智能",
    "002531": "天顺风能", "603218": "日月股份", "300034": "钢研高纳",
    "002179": "中航光电", "600893": "航发动力", "002025": "航天电器",
    "600765": "中航重机", "002268": "电科网安", "600760": "中航沈飞",
    "000738": "航发控制", "603678": "火炬电子", "300699": "光威复材",
    "600372": "中航电子", "000063": "中兴通讯", "002281": "光迅科技",
    "600487": "亨通光电", "002902": "铭普光磁", "300308": "中际旭创",
    "002792": "通宇通讯", "000070": "特发信息", "002396": "星网锐捷",
    "600855": "航天长峰", "002413": "雷科防务", "300324": "旋极信息",
    "600703": "三安光电", "002938": "鹏鼎控股", "300136": "信维通信",
    "002273": "水晶光电", "300661": "圣邦股份", "002371": "北方华创",
    "688012": "中微公司", "603501": "韦尔股份", "688008": "澜起科技",
    "002185": "华天科技", "600584": "长电科技", "002156": "通富微电",
    "688256": "寒武纪", "300782": "卓胜微", "603160": "汇顶科技",
    "002916": "深南电路", "300666": "江丰电子", "688396": "华润微",
    "002409": "雅克科技",
    "688507": "索辰科技", "002354": "天娱数科",
}

def build_name_to_code_map():
    """构建 股票简称→代码 的反向映射（含2-3字简称）"""
    m = {}
    for code, name in BUILTIN_STOCKS.items():
        m[name] = code
        for suffix in ["股份有限公司", "有限公司", "股份", "集团", "科技", "银行", "证券", "国际", "重工", "智能", "技术", "光电", "信息", "通信", "电子", "设备", "材料", "制药", "医药", "传媒"]:
            if name.endswith(suffix) and len(name) > len(suffix) + 1:
                short = name[:-len(suffix)]
                if short and short not in m:
                    m[short] = code
                break
    # 手动补充常见2-3字简称
    short_names = {
        "士兰": "600460", "士兰微": "600460",
        "紫光": "002049", "国微": "002049",
        "兆易": "603986", "兆易创新": "603986",
        "圣邦": "300661", "圣邦股份": "300661",
        "卓胜": "300782", "卓胜微": "300782",
        "韦尔": "603501", "韦尔股份": "603501",
        "澜起": "688008", "澜起科技": "688008",
        "中微": "688012", "中微公司": "688012",
        "北方": "002371", "北方华创": "002371",
        "华天": "002185", "华天科技": "002185",
        "长电": "600584", "长电科技": "600584",
        "通富": "002156", "通富微电": "002156",
        "寒武": "688256", "寒武纪": "688256",
        "江丰": "300666", "江丰电子": "300666",
        "华润": "688396", "华润微": "688396",
        "科大": "002230", "科大讯飞": "002230",
        "海康": "002415", "海康威视": "002415",
        "大华": "002236", "大华股份": "002236",
        "中际": "300308", "中际旭创": "300308",
        "天孚": "300394", "天孚通信": "300394",
        "新易": "300502", "新易盛": "300502",
        "光迅": "002281", "光迅科技": "002281",
        "华工": "000988", "华工科技": "000988",
        "联特": "301205", "联特科技": "301205",
        "宁德": "300750", "宁德时代": "300750",
        "比亚迪": "002594",
        "隆基": "601012", "隆基绿能": "601012",
        "通威": "600438", "通威股份": "600438",
        "TCL": "002129", "中环": "002129",
        "阳光": "300274", "阳光电源": "300274",
        "福斯特": "603806",
        "晶澳": "002459", "晶澳科技": "002459",
        "天合": "688599", "天合光能": "688599",
        "晶盛": "300316", "晶盛机电": "300316",
        "明阳": "601615", "明阳智能": "601615",
        "金风": "002202", "金风科技": "002202",
        "运达": "300772", "运达股份": "300772",
        "茅台": "600519", "贵州茅台": "600519",
        "五粮": "000858", "五粮液": "000858",
        "泸州": "000568", "泸州老窖": "000568",
        "洋河": "002304", "洋河股份": "002304",
        "汾酒": "600809", "山西汾酒": "600809",
        "海天": "603288", "海天味业": "603288",
        "恒瑞": "600276", "恒瑞医药": "600276",
        "药明": "603259", "药明康德": "603259",
        "康龙": "300759", "康龙化成": "300759",
        "泰格": "300347", "泰格医药": "300347",
        "平安": "601318", "中国平安": "601318",
        "招商": "600036", "招商银行": "600036",
        "兴业": "601166", "兴业银行": "601166",
        "宁波": "002142", "宁波银行": "002142",
        "江苏": "600919", "江苏银行": "600919",
        "工商": "601398", "工商银行": "601398",
        "建设": "601939", "建设银行": "601939",
        "农业": "601288", "农业银行": "601288",
        "中国银": "601988", "中国银行": "601988",
        "交通银": "601328", "交通银行": "601328",
        "航发": "600893", "航发动力": "600893",
        "航发控": "000738", "航发控制": "000738",
        "中航光": "002179", "中航光电": "002179",
        "中航沈": "600760", "中航沈飞": "600760",
        "中航电": "600372", "中航电子": "600372",
        "中航机": "002013", "中航机电": "002013",
        "中航重": "600765", "中航重机": "600765",
        "航天电": "002025", "航天电器": "002025",
        "光威": "300699", "光威复材": "300699",
        "火炬": "603678", "火炬电子": "603678",
        "电科": "002268", "电科网安": "002268",
        "美的": "000333", "美的集团": "000333",
        "格力": "000651", "格力电器": "000651",
        "海尔": "600690", "海尔智家": "600690",
        "三一": "600031", "三一重工": "600031",
        "中联": "000157", "中联重科": "000157",
        "海螺": "600585", "海螺水泥": "600585",
        "万华": "600309", "万华化学": "600309",
        "龙佰": "002601", "龙佰集团": "002601",
        "杭氧": "002430", "杭氧股份": "002430",
        "中国建": "601668", "中国建筑": "601668",
        "中国交": "601800", "中国交建": "601800",
        "中国铁": "601186", "中国铁建": "601186",
        "中国中": "601766", "中国中车": "601766",
        "京东方": "000725",
        "TCL科": "000100", "TCL科技": "000100",
        "中远海": "601919", "中远海控": "601919",
        "上汽": "600104", "上汽集团": "600104",
        "长安": "000625", "长安汽车": "000625",
        "长城汽": "601633", "长城汽车": "601633",
        "广汽": "601238", "广汽集团": "601238",
        "腾讯": "00700", "阿里": "09988", "美团": "03690", "小米": "01810",
        "索辰": "688507", "索辰科技": "688507",
        "天娱": "002354", "天娱数科": "002354",
        "深天马": "000050", "天马": "000050",
    }
    for name, code in short_names.items():
        if name not in m:
            m[name] = code
    return m

NAME_TO_CODE = build_name_to_code_map()

# 代码→名称 正向映射（用于显示）
CODE_TO_NAME = dict(BUILTIN_STOCKS.items())
for name, code in NAME_TO_CODE.items():
    if code not in CODE_TO_NAME:
        CODE_TO_NAME[code] = name

# ── 误匹配过滤 ──────────────────────────────────────────────
# 以下短词在中文文章中是通用词汇，极大概率是假阳性
FALSE_POSITIVE_SHORTS = {
    "中国", "建设", "中航", "航天", "光电", "信息", "通信",
    "电子", "国际", "材料", "智能", "重机", "股份", "制药",
    "传媒", "科技", "银行", "集团", "有限", "太极", "矿业",
    "中船", "天马", "兴业", "中环", "天孚",
}

# 以下代码对应的名称也容易误匹配
FALSE_POSITIVE_CODES = {
    "601988",  # 中国银行 — "中国"是高频词
    "601939",  # 建设银行 — "建设"是高频词
    "600519",  # 贵州茅台 — "贵州"可能误匹配
    "688507",  # 索辰科技 — "索辰"非高频但也需验证
    "000050",  # 深天马 A — "天马"常见于"天马行空"
    "002236",  # 大华股份 — "大华"常见于名称
}

def is_false_positive(name, code, text):
    """判断股票匹配是否为假阳性"""
    # 长名（>=4字）通常是完整股票名，不过滤
    if len(name) >= 4:
        return False
    # 短名在排除列表中 → 假阳性
    if name in FALSE_POSITIVE_SHORTS:
        return True
    # 特定代码直接过滤
    return code in FALSE_POSITIVE_CODES
# ────────────────────────────────────────────────────────────


def load_today_articles():
    """从 REST API 拉取公众号文章

    优先 REST API，失败后 fallback 到本地缓存。
    - 午前(06:00~12:00)：回溯过去 48 小时（覆盖昨日/上周末）
    - 午后：当日文章
    """
    beijing_now = datetime.now(UTC) + timedelta(hours=8)
    today_bj = beijing_now.date()
    is_morning = 6 <= beijing_now.hour < 12

    # 午前回溯48小时（覆盖周末 + 昨夜晚间），午后查当日
    lookback_hours = 48 if is_morning else 24
    today_end_ts = int(datetime(
        today_bj.year, today_bj.month, today_bj.day,
        tzinfo=UTC,
    ).timestamp()) + 86400
    ts_start = today_end_ts - lookback_hours * 3600
    ts_end = today_end_ts
    target_date = today_bj

    articles = []

    # ── 方案A：REST API（主路径） ──────────────────────────────
    api_ok = False
    for attempt in range(3):
        try:
            articles, api_ok = _fetch_today_via_api(ts_start, ts_end)
            if api_ok:
                break
        except Exception as e:
            if attempt < 2:
                time.sleep(2)

    if not api_ok:
        return _load_from_local_cache(target_date)

    prefix = "过去48小时" if is_morning else "今日"
    if not articles:
        return _load_from_local_cache(target_date)

    return articles


def _fetch_today_via_api(today_ts_start, today_ts_end):
    """通过 REST API 获取今日文章（含正文 Markdown）

    Returns:
        (articles, ok) — articles 列表和是否成功标志
    """
    articles = []

    # 1. 获取 fakeid→昵称 映射
    sub_map = {}
    try:
        sub_data = get_subscriptions()
        for sub in sub_data.get("subscriptions", []):
            sub_map[sub["fakeid"]] = sub.get("nickname", "未知")
    except Exception as e:
        pass

    # 2. 逐个订阅拉取文章（必须传 fakeid，不传返回空）
    all_articles = []
    seen_ids = set()
    for fakeid in sub_map:
        try:
            arts, _ = fetch_all_articles(since=0, limit=200, fakeid=fakeid)
            for art in arts:
                art_id = art.get("id", "")
                if art_id and art_id not in seen_ids:
                    all_articles.append(art)
                    seen_ids.add(art_id)
        except Exception as e:
            nickname = sub_map.get(fakeid, fakeid)


    # 3. 过滤今日/回溯时段文章 + 拉取正文
    for art in all_articles:
        pub_ts = art.get("publish_time", 0)
        if pub_ts < today_ts_start or pub_ts >= today_ts_end:
            continue

        art_id = art.get("id", "")
        title = (art.get("title", "") or "")[:80]
        fakeid = art.get("_fakeid", art.get("fakeid", ""))
        account = art.get("author", "")
        if not account and fakeid in sub_map:
            account = sub_map[fakeid]

        if not art_id or not title:
            continue

        # 获取正文 Markdown
        content = ""
        try:
            md = fetch_article_content(art_id)
            if md:
                # 去掉 YAML frontmatter
                if md.startswith("---"):
                    parts = md.split("---", 2)
                    content = parts[2].strip()[:3000] if len(parts) >= 3 else md[:3000]
                else:
                    content = md[:3000]
        except Exception:  # noqa: S110
            pass

        articles.append({
            "title": title,
            "content": content,
            "account": account or "未知公众号",
            "pub_date": datetime.fromtimestamp(pub_ts, tz=UTC).isoformat(),
            "_source": "api",
        })

    # 4. 补充本地 WeChat API 文章（自部署源，与付费 RSS 互补）
    if _HAS_LOCAL_FEEDS:
        local_lookback = 48 if is_morning else 24
        try:
            local_arts = collect_local_feeds(lookback_hours=local_lookback)
            if local_arts:
                sync_local_to_cache(local_arts)
                # 合并到 articles（已按标题去重）
                existing_links = {
                    a.get("title", "")[:40] for a in articles
                }
                for a in local_arts:
                    # 用标题前40字符去重，比 content[:40] 更可靠
                    title_key = (a.get("title", "") or "")[:40]
                    if title_key and title_key not in existing_links:
                        articles.append(a)
                        existing_links.add(title_key)
        except Exception:  # noqa: S110
            pass

    return articles, True


def _load_from_local_cache(today_bj):
    """RSS 失败时的 fallback：从 output/wx_articles/ 读取今日缓存文章"""
    articles = []
    if not os.path.isdir(OUTPUT_DIR):
        return articles

    seen_titles = set()
    for fname in sorted(os.listdir(OUTPUT_DIR)):
        if not (fname.endswith((".json", ".md"))):
            continue
        fpath = os.path.join(OUTPUT_DIR, fname)

        # 从文件名取日期
        fdate_str = fname[:8]
        try:
            fdate = datetime.strptime(fdate_str, "%Y%m%d").date()
        except ValueError:
            continue
        if fdate != today_bj:
            continue

        try:
            title, content, account, pub_date = "", "", "", ""
            has_content = False

            if fname.endswith(".json"):
                with open(fpath, encoding="utf-8") as f:
                    data = json.load(f)
                title = data.get("title", "")
                content = (data.get("content", "") or data.get("description", "") or "")[:3000]
                account = data.get("account", data.get("author", ""))
                pub_date = data.get("pub_date", "")
                has_content = bool(content)
            else:
                # .md 文件：只有标题和公众号名，无正文
                with open(fpath, encoding="utf-8") as f:
                    for raw_line in f:
                        stripped = raw_line.strip()
                        if stripped.startswith("# "):
                            title = stripped[2:].strip()
                        elif stripped.startswith(("- 公众号：", "公众号：")):
                            account = stripped.split("：", 1)[-1].strip()
                has_content = False

            if not title or title in seen_titles:
                continue
            seen_titles.add(title)
            if not has_content:
                content = "（仅有标题，无完整正文）"

            articles.append({
                "title": title[:80],
                "content": content[:3000] if content else "",
                "account": account or "未知公众号",
                "pub_date": pub_date,
                "_source": "cache",
            })
        except Exception:
            continue

    return articles


def extract_article_stocks(title, content, account):
    """
    从文章中提取提及的股票（关键词匹配，含假阳性过滤）。
    返回 [{"code": "600522", "name": "中天科技"}, ...]
    注：name 优先使用 CODE_TO_NAME 中的全称
    """
    text = f"{title}\n{content[:500]}"
    mentioned_stocks = []
    seen_codes = set()
    for match_name, code in NAME_TO_CODE.items():
        if code in seen_codes:
            continue
        if match_name not in text:
            continue
        if is_false_positive(match_name, code, text):
            continue
        # 用全称显示，短名只用于匹配
        display_name = CODE_TO_NAME.get(code, match_name)
        mentioned_stocks.append({"code": code, "name": display_name})
        seen_codes.add(code)
    return mentioned_stocks


# ── 技术面信号（集成 sim_trade.py）────────────────────────────
def get_technical_signal(code):
    """
    获取股票技术面信号（通过 star_signal_adapter 获取五角星综合评分）
    返回：{
        "signal": "bullish/bearish/neutral",
        "reason": "...",
        "score": float,     # 0-100 五角星评分
        "trend": str,       # 多头/震荡/空头
        "rsi": float,       # RSI指标
        "atr_stop": float,  # ATR动态止损价
        "atr_stop_pct": float,  # ATR止损距离%
        "strength": int,    # 信号强度1-5
    }
    """
    try:
        # 临时移除 sys.path[0] 避免 numpy C 扩展加载冲突
        _saved_path0 = sys.path[0] if sys.path else None
        if _saved_path0 and ('scripts' in _saved_path0 or 'WorkBuddy' in _saved_path0):
            sys.path.pop(0)

        from star_signal_adapter import get_star_signal  # noqa: F811

        star = get_star_signal(code)
        if star.get("score") is not None:
            score = star["score"]
            trend = star.get("trend", "震荡")
            rsi = star.get("rsi", 50)
            strength = star.get("strength", 0)
            atr_stop = star.get("atr_stop", 0)
            atr_stop_pct = star.get("atr_stop_pct", 0)

            # 评分→多空映射
            if score >= 65:
                signal = "bullish"
                reason = (f"⭐{score:.0f}分/{trend} RSI{rsi:.0f} "
                         f"强度{'⭐'*strength}{'✩'*(5-strength)} "
                         f"ATR止损¥{atr_stop:.2f}({atr_stop_pct:+.1f}%)")
            elif score <= 35:
                signal = "bearish"
                reason = (f"⭐{score:.0f}分/{trend} RSI{rsi:.0f} "
                         f"强度{'⭐'*strength}{'✩'*(5-strength)} "
                         f"ATR止损¥{atr_stop:.2f}({atr_stop_pct:+.1f}%)")
            else:
                signal = "neutral"
                reason = (f"⭐{score:.0f}分/{trend} RSI{rsi:.0f} "
                         f"多空均衡，观望")

            return {
                "signal": signal,
                "reason": reason,
                "score": score,
                "trend": trend,
                "rsi": rsi,
                "atr_stop": atr_stop,
                "atr_stop_pct": atr_stop_pct,
                "strength": strength,
            }
    except ImportError as e:
        pass
    except Exception as e:
        pass
    finally:
        if _saved_path0 and sys.path[0] != _saved_path0:
            sys.path.insert(0, _saved_path0)

    # 降级：用价格趋势判断
    kline = fetch_today_kline(code)
    if kline:
        if kline["change"] > 2:
            return {"signal": "bullish", "reason": f"今日上涨{kline['change']:.1f}%",
                    "score": 0, "trend": "未知", "rsi": 0}
        elif kline["change"] < -2:
            return {"signal": "bearish", "reason": f"今日下跌{kline['change']:.1f}%",
                    "score": 0, "trend": "未知", "rsi": 0}
        else:
            return {"signal": "neutral", "reason": f"今日涨跌{kline['change']:+.1f}%",
                    "score": 0, "trend": "未知", "rsi": 0}

    return {"signal": "neutral", "reason": "无技术面数据",
            "score": 0, "trend": "未知", "rsi": 0}


def call_sim_trade_auto_check():
    """
    调用 sim_trade.py 的 auto-check 命令，检查所有持仓的止损止盈条件
    返回：建议交易列表
    """
    try:
        import subprocess
        result = subprocess.run(
            ["python3", str(_PROJECT_ROOT / ".workbuddy" / "scripts" / "sim_trade.py"), "auto-check"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            output = result.stdout.strip()
            # 解析 JSON 输出
            import json
            start = output.find('{')
            end = output.rfind('}') + 1
            if start >= 0 and end > start:
                data = json.loads(output[start:end])
                return data
    except Exception as e:
        pass

    return {"ok": False, "has_suggestions": False, "suggestions": []}


# ── 行情获取 ────────────────────────────────────────────────
def fetch_current_price(code):
    """获取股票当前价格（腾讯API）"""
    try:
        prefix = "sh" if code.startswith("6") else "sz"
        url = f"https://qt.gtimg.cn/q={prefix}{code}"
        r = requests.get(url, headers=HEADERS, timeout=8)
        r.encoding = "gbk"
        m = re.search(r'v_[^=]*="([^"]+)"', r.text)
        if m:
            fields = m.group(1).split("~")
            if len(fields) > 5:
                return float(fields[3])
    except Exception:  # noqa: S110
        pass
    return None


def fetch_today_kline(code):
    """获取今日K线数据"""
    try:
        prefix = "sh" if code.startswith("6") else "sz"
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code},day,,,10,qfq"
        r = requests.get(url, headers=HEADERS, timeout=8)
        data = r.json()
        klines = data.get("data", {}).get(f"{prefix}{code}", {}).get("qfqday", [])
        if klines and len(klines) > 0:
            today_k = klines[-1]
            return {
                "open": float(today_k[1]),
                "close": float(today_k[2]),
                "high": float(today_k[3]),
                "low": float(today_k[4]),
                "change": round((float(today_k[2]) - float(today_k[1])) / float(today_k[1]) * 100, 2) if float(today_k[1]) > 0 else 0
            }
    except Exception:  # noqa: S110
        pass
    return None


# ── 热讯抓取 ────────────────────────────────────────────────
def fetch_macro_calendar():
    """抓取今日宏观数据"""
    items = []
    try:
        url = "https://www.cls.cn/v2/telegraph"
        r = requests.get(url, headers=HEADERS, timeout=10, params={"refresh": "1", "page": "1"})
        if r.status_code == 200:
            data = r.json()
            raw = data.get("data", {}).get("list", []) or data.get("data", [])
            for item in raw[:8]:
                text = (item.get("content") or item.get("title", ""))[:120]
                if text and ("GDP" in text or "CPI" in text or "PMI" in text or "利率" in text or "降准" in text or "加息" in text):
                    items.append(text)
    except Exception:  # noqa: S110
        pass
    return items[:3]


def fetch_sector_hot():
    """抓取今日板块热度"""
    items = []
    try:
        url = "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=20&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:90+t:2&fields=f12,f14,f2,f3,f62"
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code == 200:
            data = r.json()
            for item in (data.get("data", {}).get("diff", []) or [])[:5]:
                name = item.get("f14", "")
                pct  = item.get("f3", 0)
                if pct and pct > 0:
                    items.append(f"{name} +{pct:.2f}%")
    except Exception:  # noqa: S110
        pass
    return items


def fetch_cls_telegraph():
    """抓取财联社7x24快讯Top 5"""
    items = []
    try:
        api = "https://www.cls.cn/v2/telegraph"
        r = requests.get(api, headers=HEADERS, timeout=12, params={"refresh": "1", "page": "1"})
        if r.status_code == 200:
            data = r.json()
            raw = data.get("data", {}).get("list", []) or data.get("data", [])
            for item in raw[:8]:
                text = (item.get("content") or item.get("title", ""))[:140]
                if text:
                    items.append(text)
    except Exception:  # noqa: S110
        pass
    if not items:
        try:
            url = "https://www.cls.cn/telegraph"
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.encoding = "utf-8"
            soup = BeautifulSoup(r.text, "lxml")
            for li in soup.select("li.telegraph-item, .telegraph__item")[:8]:
                text = li.get_text(strip=True)[:140]
                if text:
                    items.append(text)
        except Exception:  # noqa: S110
            pass
    return items[:5]


def fetch_eastmoney_news():
    """抓取东方财富财经快讯Top 5"""
    items = []
    try:
        url = "https://kuaixun.eastmoney.com/ajax/getlist.aspx?pageindex=1&pagesize=8"
        r = requests.get(url, headers=HEADERS, timeout=12)
        r.encoding = "utf-8"
        data = r.json()
        for item in data.get("LiveList", [])[:8]:
            text = re.sub(r'<[^>]+>', '', item.get("content", "") or item.get("title", ""))[:140]
            if text:
                items.append(text)
    except Exception:  # noqa: S110
        pass
    return items[:5]


# ── 持仓读取 ────────────────────────────────────────────────
def load_portfolios():
    sim_pos   = {}
    user_pos  = {}
    sim_cash  = 0
    user_cash = 0
    if os.path.exists(SIM_PORTFOLIO):
        with open(SIM_PORTFOLIO, encoding="utf-8") as f:
            d = json.load(f)
        sim_cash = d.get("cash", 0)
        for code, pos in d.get("positions", {}).items():
            sim_pos[code] = pos
    if os.path.exists(USER_PORTFOLIO):
        with open(USER_PORTFOLIO, encoding="utf-8") as f:
            d = json.load(f)
        user_cash = d.get("summary", {}).get("cash_available", 0)
        for h in d.get("holdings", []):
            user_pos[h["code"]] = h
    return {"sim": {"positions": sim_pos, "cash": sim_cash},
            "user": {"positions": user_pos, "cash": user_cash}}

def save_articles_to_cache(articles, now=None):
    """将采集到的文章持久化到 output/wx_articles/（供早报/鱼盆提取器复用）。

    写入格式兼容 _load_from_local_cache：
      - {YYYYMMDD}_{HHMMSS}_{title}.json  -> {"title","content","account","pub_date"}
      - {YYYYMMDD}_{HHMMSS}_{title}.md     -> "# title\\n公众号：xxx\\n\\n{content}"
    仅写入尚未存在的文章（按标题去重），避免重复落盘。
    """
    if not articles:
        return 0
    try:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
    except Exception:
        return 0

    now = now or datetime.now()
    stamp = now.strftime("%Y%m%d_%H%M%S")
    # 已存在标题集合（防重复）
    existing = set()
    for fn in os.listdir(OUTPUT_DIR):
        if fn.endswith(".json"):
            try:
                d = json.loads(Path(OUTPUT_DIR, fn).read_text(encoding="utf-8"))
                existing.add(d.get("title", ""))
            except Exception:
                pass

    saved = 0
    for art in articles:
        title = (art.get("title") or "").strip()
        if not title or title in existing:
            continue
        # 文件名安全化（截断 + 去非法字符）
        safe = "".join(c for c in title if c not in r'\/:*?"<>|')[:40]
        base = f"{stamp}_{safe}"
        content = art.get("content", "") or ""
        account = art.get("account", "") or "未知公众号"
        pub_date = art.get("pub_date", "") or now.isoformat()

        payload = {
            "title": title,
            "content": content,
            "account": account,
            "pub_date": pub_date,
            "_source": art.get("_source", "api"),
        }
        try:
            Path(OUTPUT_DIR, f"{base}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            md = f"# {title}\n公众号：{account}\n\n{content}\n"
            Path(OUTPUT_DIR, f"{base}.md").write_text(md, encoding="utf-8")
            existing.add(title)
            saved += 1
        except Exception:
            continue
    return saved


def collect_data():
    """
    采集所有原始数据，输出结构化 JSON（供 Agent 做 LLM 分析）。
    不生成报告，不推送飞书。
    """
    now = datetime.now()
    articles = load_today_articles()
    # 持久化到 output/wx_articles/，供早报/鱼盆提取器复用（修复缓存冻结）
    with contextlib.suppress(Exception):
        save_articles_to_cache(articles, now)

    # 文章中的股票提及
    articles_data = []
    for art in articles:
        content = art.get("content", "")
        title   = art.get("title", "")
        account = art.get("account", "")
        stocks = extract_article_stocks(title, content, account)
        articles_data.append({
            "title": title,
            "account": account,
            "content": content[:2000],  # 供Agent分析用
            "mentioned_stocks": stocks,  # [{code, name}]
            "pub_date": art.get("pub_date", ""),
        })

    # 持仓
    portfolios = load_portfolios()
    sim_pos = portfolios["sim"]["positions"]
    user_pos = portfolios["user"]["positions"]

    # 持仓技术面信号
    holdings_data = []
    for code, pos in sim_pos.items():
        price = fetch_current_price(code)
        star = get_technical_signal(code)
        holdings_data.append({
            "code": code,
            "name": pos["name"],
            "source": "sim",
            "shares": pos["shares"],
            "avg_cost": pos["avg_cost"],
            "current_price": price,
            "star_signal": star,
        })
    for code, pos in user_pos.items():
        if code not in sim_pos:
            price = fetch_current_price(code)
            star = get_technical_signal(code)
            holdings_data.append({
                "code": code,
                "name": pos["name"],
                "source": "user",
                "shares": float(pos["shares"]) if str(pos["shares"]).strip() else 0,
                "avg_cost": float(pos["avg_cost"]) if str(pos["avg_cost"]).strip() else 0.0,
                "current_price": price,
                "star_signal": star,
            })

    # 市场数据
    cls_news = fetch_cls_telegraph()
    em_news = fetch_eastmoney_news()
    sector_hot = fetch_sector_hot()
    macro = fetch_macro_calendar()

    # 止损止盈检查
    sim_trade_check = call_sim_trade_auto_check()

    # 信号同步：将文章提取的股票提及写入 article_signals.json
    sync_article_signals(articles_data, now)

    # COMBO信号同步：将 QTS 策略信号也写入 article_signals.json
    with contextlib.suppress(Exception):
        from sync_combo_signals import sync_combo  # noqa: E402
        sync_combo()

    data = {
        "collected_at": now.isoformat(),
        "date": now.strftime("%Y-%m-%d"),
        "articles_count": len(articles),
        "articles": articles_data,
        "holdings": holdings_data,
        "sim_cash": portfolios["sim"]["cash"],
        "user_cash": portfolios["user"]["cash"],
        "market_news": {
            "cls_top": cls_news,
            "eastmoney_top": em_news,
            "sector_hot": sector_hot,
            "macro_calendar": macro,
        },
        "stop_loss_check": sim_trade_check,
    }

    return data


# ── 信号仓库同步 ─────────────────────────────────────────
SIGNALS_FILE = str(_PROJECT_ROOT / ".workbuddy" / "data" / "article_signals.json")


def _atomic_write_json(filepath, data):
    """原子写入 JSON：先写临时文件再 rename，防止并发写丢数据。"""
    import tempfile
    dirname = Path(filepath).parent
    dirname.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".json", dir=str(dirname))
    try:
        os.write(fd, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, filepath)  # 跨卷原子 rename


def sync_article_signals(articles_data, now):
    """将文章提取的股票提及增量写入 article_signals.json（供 signal_verify 验证）。

    - 以 article_id（标题+账号的md5短hash）去重
    - 信号默认 neutral，置信度 0（待 LLM 分析后升级）
    - 仅增量追加，不修改已有条目
    """
    if not articles_data:
        return 0

    try:
        if Path(SIGNALS_FILE).exists():
            existing = json.loads(Path(SIGNALS_FILE).read_text(encoding="utf-8"))
        else:
            existing = []
    except (json.JSONDecodeError, OSError):
        existing = []

    existing_ids = {s.get("article_id", "") for s in existing}
    new_count = 0
    record_date = now.strftime("%Y年%m月%d日")

    for art in articles_data:
        title = art.get("title", "")
        if not title:
            continue
        raw = f"{title}|{art.get('account', '')}|{record_date}"
        article_id = hashlib.md5(raw.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]  # noqa: S324
        if article_id in existing_ids:
            continue

        stocks = art.get("mentioned_stocks", [])
        if not stocks:
            continue

        n = 0
        for s in stocks:
            code = s.get("code", "")
            name = s.get("name", "")
            if not code:
                continue
            existing.append({
                "article_id": article_id,
                "account": art.get("account", ""),
                "title": title[:60],
                "stock_code": code,
                "stock_name": name,
                "signal": "neutral",
                "target_price": None,
                "confidence": 0,
                "recorded_at": record_date,
                "source": "RSS自动提取",
            })
            n += 1
        existing_ids.add(article_id)
        new_count += n

    if new_count > 0:
        _atomic_write_json(SIGNALS_FILE, existing)
    return new_count
