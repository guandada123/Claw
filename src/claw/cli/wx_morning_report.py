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
import argparse
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

# 微信 RSS 凭证（统一从 wx_rss_auth.py 加载，凭证文件：~/.workbuddy/auth/wx_rss_api.sh）
from wx_rss_auth import (  # noqa: E402
    fetch_all_articles,
    fetch_article_content,
    get_subscriptions,
)

# AI 摘要（接入 summarize 技能）
try:
    from summarize_batch import summarize_article_content
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
            print(f"  ⚠️ REST API 获取失败(第{attempt+1}次): {e}", file=sys.stderr)
            if attempt < 2:
                time.sleep(2)

    if not api_ok:
        print("  ⚠️ REST API 全部失败，fallback 到本地缓存", file=sys.stderr)
        return _load_from_local_cache(target_date)

    prefix = "过去48小时" if is_morning else "今日"
    print(f"  📡 REST API 获取到 {len(articles)} 篇{prefix}文章", file=sys.stderr)
    if not articles:
        print(f"  ⚠️ REST API 正常返回但无{prefix}文章，尝试本地缓存", file=sys.stderr)
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
        print(f"  ⚠️ 获取订阅列表失败: {e}", file=sys.stderr)

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
            print(f"  ⚠️ 拉取 {nickname} 文章失败: {e}", file=sys.stderr)

    print(f"  📦 共拉取 {len(all_articles)} 篇（去重）", file=sys.stderr)

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

    return articles, True


def _load_from_local_cache(today_bj):
    """RSS 失败时的 fallback：从 output/wx_articles/ 读取今日缓存文章"""
    articles = []
    if not os.path.isdir(OUTPUT_DIR):
        print("  ⚠️ 本地缓存目录不存在", file=sys.stderr)
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
                    for line in f:
                        line = line.strip()
                        if line.startswith("# "):
                            title = line[2:].strip()
                        elif line.startswith(("- 公众号：", "公众号：")):
                            account = line.split("：", 1)[-1].strip()
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
            print(f"  ⚠️  跳过异常文章: {fname}", file=sys.stderr)
            continue

    print(f"  📂 本地缓存读到 {len(articles)} 篇今日文章", file=sys.stderr)
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

        from star_signal_adapter import get_dynamic_stop_loss, get_star_signal

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
        print(f"  ⚠️ star_signal_adapter 导入失败({code}): {e}", file=sys.stderr)
    except Exception as e:
        print(f"  ⚠️ 五角星信号获取失败({code}): {e}", file=sys.stderr)
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
        print(f"  ⚠️ sim_trade auto-check 调用失败: {e}", file=sys.stderr)

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


# ── 早报生成 ────────────────────────────────────────────────
def build_morning_report():
    now = datetime.now()
    articles = load_today_articles()
    print(f"[早报] 读取到 {len(articles)} 篇今日文章", file=sys.stderr)

    # ── 接入 summarize 技能：为每篇文章生成 200 字摘要 ────
    article_summaries = {}
    if _HAS_SUMMARIZE and articles:
        print("  📝 生成文章摘要（summarize skill）...", file=sys.stderr)
        for art in articles:
            try:
                s = summarize_article_content(art)
                if s:
                    article_summaries[art.get("title", "")] = s
            except Exception as e:
                print(f"  ⚠️  摘要失败: {art.get('title','?')[:20]}: {e}", file=sys.stderr)
        print(f"  ✅ 成功生成 {len(article_summaries)} 篇摘要", file=sys.stderr)
    # ────────────────────────────────────────────────────────

    # 提取文章中的股票信号（用关键词匹配，LLM分析由Agent完成）
    articles_stocks = []
    for i, art in enumerate(articles):
        content = art.get("content", "")
        title   = art.get("title", "")
        account = art.get("account", "")

        # 提取提及的股票
        stocks = extract_article_stocks(title, content, account)

        if stocks:
            # 包装为兼容格式（无LLM信号，让Agent分析）
            signals = [{"code": s["code"], "name": s["name"],
                        "signal": "neutral", "confidence": 0, "reason": "待Agent分析"}
                       for s in stocks]
            articles_stocks.append({
                "title": title,
                "account": account,
                "signals": signals
            })

        # 进度提示（每分析5篇输出一次）
        if (i + 1) % 5 == 0:
            print(f"  已分析 {i+1}/{len(articles)} 篇...", file=sys.stderr)

    # 财经热讯 + 板块热度 + 宏观数据
    print("  📡 抓取财经数据...", file=sys.stderr)
    cls_news    = fetch_cls_telegraph()
    em_news     = fetch_eastmoney_news()
    sector_hot  = fetch_sector_hot()
    macro_calendar = fetch_macro_calendar()

    # 持仓
    portfolios = load_portfolios()
    sim_pos   = portfolios["sim"]["positions"]
    user_pos  = portfolios["user"]["positions"]
    sim_cash  = portfolios["sim"]["cash"]
    user_cash = portfolios["user"]["cash"]
    total_cash = sim_cash + user_cash

    all_positions = {}
    for code, pos in sim_pos.items():
        all_positions[code] = {"name": pos["name"], "shares": pos["shares"],
                                "cost": pos["avg_cost"], "source": "模拟仓"}
    for code, pos in user_pos.items():
        if code not in all_positions:
            all_positions[code] = {"name": pos["name"], "shares": pos["shares"],
                                    "cost": float(pos["avg_cost"]) if str(pos["avg_cost"]).strip() else 0.0, "source": "实盘"}

    # ── 组装早报 ──────────────────────────────────────────
    lines = []
    lines.append(f"📊 微信早报 — {now.strftime('%Y-%m-%d')}")
    lines.append("=" * 40)

    # 一、公众号文章汇总
    lines.append(f"\n一、公众号文章汇总（{len(articles)}篇新增）")
    if articles_stocks:
        for i, art in enumerate(articles_stocks[:12]):
            signals_desc = ", ".join(
                f"{sig['name']}({sig['code']})[{'多' if sig['signal']=='bullish' else '空' if sig['signal']=='bearish' else '中'}]"
                for sig in art["signals"][:4]
            )
            lines.append(f"  {i+1}. [{art['account']}] {art['title'][:28]}...")
            lines.append(f"     提及：{signals_desc}")
    else:
        lines.append("  （今日文章未提取到明确股票代码）")

    # 二、热票汇总（多空统计，只考虑高置信度信号）
    lines.append("\n二、热票汇总（公众号多空信号，置信度≥4）")
    stock_stats = {}
    for art in articles_stocks:
        for sig in art["signals"]:
            code = sig["code"]
            name = sig["name"]
            signal = sig["signal"]
            confidence = sig.get("confidence", 0)

            # 只统计置信度>=4的信号
            if confidence < 4:
                continue

            if code not in stock_stats:
                stock_stats[code] = {"name": name, "bullish": 0, "bearish": 0, "neutral": 0, "reasons": []}
            if signal == "bullish":
                stock_stats[code]["bullish"] += 1
            elif signal == "bearish":
                stock_stats[code]["bearish"] += 1
            else:
                stock_stats[code]["neutral"] += 1
            if sig.get("reason"):
                stock_stats[code]["reasons"].append(sig["reason"])

    if stock_stats:
        sorted_stocks = sorted(stock_stats.items(),
                               key=lambda x: x[1]["bullish"] - x[1]["bearish"], reverse=True)
        for code, stat in sorted_stocks[:10]:
            name = stat["name"]
            b = stat["bullish"]
            s = stat["bearish"]
            n = stat["neutral"]
            signal = "🔴偏空" if s > b else ("🟢偏多" if b > s else "🟡中性")
            lines.append(f"  {signal} {name}({code})  看多{b}/看空{s}/中性{n}")
            if stat["reasons"]:
                lines.append(f"    理由：{stat['reasons'][0][:30]}")
    else:
        lines.append("  （无高置信度信号）")

    # 三、技术面信号（集成 sim_trade.py 的 star_signal）
    lines.append("\n三、技术面信号（持仓股）")
    if all_positions:
        for code, pos in all_positions.items():
            tech_signal = get_technical_signal(code)
            name = pos["name"]
            signal_icon = "🟢" if tech_signal["signal"] == "bullish" else ("🔴" if tech_signal["signal"] == "bearish" else "🟡")
            lines.append(f"  {signal_icon} {name}({code})  技术面：{tech_signal['reason']}")
    else:
        lines.append("  （当前无持仓）")

    # 四、今日操作建议（结合持仓 + 技术面）
    lines.append("\n四、今日操作建议")
    lines.append(f"  可用资金：模拟仓¥{sim_cash:.0f} + 实盘¥{user_cash:.0f} = ¥{total_cash:.0f}")

    # 持仓股建议
    holding_advice = []
    watching_advice = []
    for code, stat in stock_stats.items():
        name       = stat["name"]
        is_holding = code in all_positions
        bullish    = stat["bullish"]
        bearish    = stat["bearish"]

        if is_holding:
            pos      = all_positions[code]
            cost     = pos["cost"]
            shares   = pos["shares"]
            cur_price = fetch_current_price(code)
            if cur_price is None:
                cur_price = cost
            pnl_pct  = (cur_price - cost) / cost * 100 if cost else 0
            val      = cur_price * shares

            if bullish > bearish:
                action = "🟢 持有/加仓"
                add_shares = 100
                add_cost   = add_shares * cur_price
                if add_cost <= total_cash * 0.25:
                    detail = f"建议加{add_shares}股，约¥{add_cost:.0f}，分批：09:35/10:30/13:30 各1/3"
                else:
                    detail = f"现金不足，建议持有{shares}股观望，回调再补"
            elif bearish > bullish:
                action = "🔴 减仓/止损"
                sell_shares = min(100, shares)
                detail = f"建议卖出{sell_shares}股（约¥{sell_shares*cur_price:.0f}），操作时间09:30-09:35"
            else:
                action = "🟡 观望"
                detail = f"多空不明，维持{shares}股，观察1-2天"

            holding_advice.append(
                f"  {action} {name}({code}) 成本¥{cost:.2f} 现价¥{cur_price:.2f} 浮盈{pnl_pct:+.1f}%\n"
                f"       → {detail}"
            )
        # 新关注股
        elif bullish > bearish and total_cash > 5000:
            cur_price = fetch_current_price(code) or 0
            if cur_price > 0:
                buy_shares = 100
                buy_cost   = buy_shares * cur_price
                watching_advice.append(
                    f"  🟢 可关注 {name}({code}) 现价¥{cur_price:.2f}\n"
                    f"       → 建议买入{buy_shares}股，占用¥{buy_cost:.0f}，时间09:35（等开盘企稳）"
                )

    if holding_advice:
        lines.append("\n  【持仓股建议】")
        lines.extend(holding_advice)
    if watching_advice:
        lines.append("\n  【新关注股建议】")
        lines.extend(watching_advice[:5])
    if not holding_advice and not watching_advice:
        lines.append("\n  （文章未触发操作信号，今日观察为主）")

    # 五、今日宏观数据
    lines.append("\n五、今日宏观数据")
    if macro_calendar:
        for item in macro_calendar:
            lines.append(f"  · {item}")
    else:
        lines.append("  （暂无重要宏观数据发布）")

    # 六、板块热度
    lines.append("\n六、板块热度（东方财富）")
    if sector_hot:
        for item in sector_hot:
            lines.append(f"  🔥 {item}")
    else:
        lines.append("  （数据获取中...）")

    # 七、今日公众号热文（含 AI 摘要）
    lines.append("\n七、今日公众号热文（附AI摘要）")
    seen = set()
    count = 0
    for art in articles:
        title = art.get("title", "")
        account = art.get("account", "")
        key = title[:20]
        if key not in seen and count < 8:
            summary = article_summaries.get(title, "")
            lines.append(f"  · [{account}] {title[:50]}")
            if summary:
                lines.append(f"    📝 {summary[:100]}")
            seen.add(key)
            count += 1

    lines.append("\n" + "=" * 40)
    lines.append("⚠️ 以上为公众号文章观点汇总，操作请结合实时行情，止损纪律优先")

    report = "\n".join(lines)

    # 保存早报（供晚报复盘用）
    os.makedirs(REPORT_DIR, exist_ok=True)
    date_str = now.strftime("%Y%m%d")
    with open(os.path.join(REPORT_DIR, f"{date_str}_morning.txt"), "w", encoding="utf-8") as f:
        f.write(report)

    return report


# ── 晚报生成（复盘+策略优化）────────────────────────────────
def build_evening_report():
    now = datetime.now()
    date_str = now.strftime("%Y%m%d")

    lines = []
    lines.append(f"📊 微信晚报 — {now.strftime('%Y-%m-%d')}")
    lines.append("=" * 40)

    # 读取早报建议
    morning_path = os.path.join(REPORT_DIR, f"{date_str}_morning.txt")
    morning_advice = []
    if os.path.exists(morning_path):
        with open(morning_path, encoding="utf-8") as f:
            morning_text = f.read()
            for line in morning_text.split("\n"):
                if "建议" in line or "🟢" in line or "🔴" in line or "🟡" in line:
                    morning_advice.append(line.strip())
            lines.append("\n一、早报建议回顾")
            lines.append(f"  （早报于 07:30 生成，共{len(morning_advice)}条建议）")
            for adv in morning_advice[:10]:
                if adv:
                    lines.append(f"  {adv}")
    else:
        lines.append("\n一、早报建议回顾")
        lines.append("  （未找到今日早报，可能是首次运行）")

    # 读取当前持仓
    lines.append("\n二、今日持仓变化复盘")
    portfolios = load_portfolios()
    sim_pos   = portfolios["sim"]["positions"]
    user_pos  = portfolios["user"]["positions"]
    sim_cash  = portfolios["sim"]["cash"]

    all_positions = {}
    for code, pos in sim_pos.items():
        all_positions[code] = {"name": pos["name"], "shares": pos["shares"],
                                "cost": pos["avg_cost"], "source": "模拟仓"}
    for code, pos in user_pos.items():
        if code not in all_positions:
            all_positions[code] = {"name": pos["name"], "shares": pos["shares"],
                                    "cost": float(pos["avg_cost"]) if str(pos["avg_cost"]).strip() else 0.0, "source": "实盘"}

    if not all_positions:
        lines.append("  当前无持仓")
    else:
        for code, pos in all_positions.items():
            name      = pos["name"]
            shares    = pos["shares"]
            cost      = pos["cost"]
            cur_price = fetch_current_price(code)
            if cur_price is None:
                cur_price = cost
            pnl_pct   = (cur_price - cost) / cost * 100 if cost else 0
            val       = cur_price * shares

            kline = fetch_today_kline(code)
            kline_desc = ""
            if kline:
                kline_desc = f"  今开¥{kline['open']:.2f} 最高¥{kline['high']:.2f} 最低¥{kline['low']:.2f} 收盘¥{kline['close']:.2f} 涨跌{kline['change']:+.2f}%"

            status = "🟢盈利" if pnl_pct > 0 else ("🔴亏损" if pnl_pct < 0 else "➖平")
            lines.append(f"  {status} {name}({code}) {shares}股 成本¥{cost:.2f} 现价¥{cur_price:.2f} 浮盈{pnl_pct:+.1f}% 市值¥{val:.0f}")
            if kline_desc:
                lines.append(kline_desc)

    # 二点五、技术面信号评分（五角星）
    lines.append("\n三、技术面信号评分（五角星战法）")
    if all_positions:
        for code, pos in all_positions.items():
            signal = get_technical_signal(code)
            name = pos["name"]
            score = signal.get("score", 0)
            trend = signal.get("trend", "未知")
            rsi = signal.get("rsi", 0)
            atr_stop = signal.get("atr_stop", 0)
            strength = signal.get("strength", 0)
            signal_s = signal.get("signal", "neutral")
            icon = "🟢" if signal_s == "bullish" else ("🔴" if signal_s == "bearish" else "🟡")
            strength_bar = "⭐" * strength + "✩" * (5 - strength)
            lines.append(f"  {icon} {name}({code})  ⭐{score:.0f}分  {strength_bar}  趋势:{trend}  RSI:{rsi:.0f}")
            if atr_stop > 0:
                lines.append(f"     ATR动态止损: ¥{atr_stop:.2f}({signal.get('atr_stop_pct', 0):+.1f}%)")
    else:
        lines.append("  （当前无持仓）")

    # 三、止损止盈检查（调用 sim_trade.py auto-check）
    lines.append("\n四、止损止盈检查（sim_trade.py）")
    auto_check_result = call_sim_trade_auto_check()
    if auto_check_result.get("ok") and auto_check_result.get("has_suggestions"):
        suggestions = auto_check_result.get("suggestions", [])
        lines.append(f"  ⚠️ 发现 {len(suggestions)} 条止损止盈建议：")
        for sug in suggestions[:5]:
            action_icon = "🔴" if sug["action"] == "SELL" else "🟢"
            lines.append(f"  {action_icon} {sug['name']}({sug['code']})  {sug['reason']}  （优先级：{sug['priority']}）")
    else:
        lines.append("  ✅ 所有持仓均未触发止损止盈条件")

    # 四、早报建议 vs 今日实际走势（复盘核心）
    lines.append("\n五、早报建议复盘（信号质量评估）")
    articles = load_today_articles()
    articles_stocks = []
    for art in articles:
        content = art.get("content", "")
        title   = art.get("title", "")
        account = art.get("account", "")
        stocks = extract_article_stocks(title, content, account)
        if stocks:
            # 统一输出为兼容格式
            signals = [{"code": s["code"], "name": s["name"],
                        "signal": "neutral", "confidence": 0, "reason": "待Agent分析"}
                       for s in stocks]
            articles_stocks.append({"title": title, "account": account, "signals": signals})

    stock_stats = {}
    for art in articles_stocks:
        for sig in art["signals"]:
            code = sig["code"]
            name = sig["name"]
            signal = sig["signal"]
            confidence = sig.get("confidence", 0)

            if code not in stock_stats:
                stock_stats[code] = {"name": name, "bullish": 0, "bearish": 0, "signals": []}
            if signal == "bullish":
                stock_stats[code]["bullish"] += 1
            elif signal == "bearish":
                stock_stats[code]["bearish"] += 1
            stock_stats[code]["signals"].append(sig)

    if stock_stats:
        correct = 0
        total_signal = 0
        for code, stat in stock_stats.items():
            name     = stat["name"]
            bullish  = stat["bullish"]
            bearish  = stat["bearish"]
            cur_price = fetch_current_price(code)
            kline     = fetch_today_kline(code)

            if kline and (bullish > 0 or bearish > 0):
                total_signal += 1
                actual_up = kline["close"] >= kline["open"]
                suggested_up = bullish > bearish
                is_correct = (suggested_up and actual_up) or (not suggested_up and not actual_up)
                if is_correct:
                    correct += 1
                status_icon = "✅" if is_correct else "❌"
                signal_str = "看多" if bullish > bearish else ("看空" if bearish > bullish else "中性")
                actual_str = "上涨" if actual_up else "下跌"
                lines.append(f"  {status_icon} {name}({code}) 早报信号:{signal_str}  实际:{actual_str}  涨跌{kline['change']:+.2f}%")

        if total_signal > 0:
            acc = correct / total_signal * 100
            lines.append(f"\n  今日信号准确率：{correct}/{total_signal} = {acc:.0f}%")
        else:
            lines.append("\n  （无足够信号供复盘）")
    else:
        lines.append("  （今日无股票信号，无需复盘）")

    # 五、策略迭代记录
    lines.append("\n六、策略迭代记录")
    strategy_log_path = os.path.join(REPORT_DIR, "strategy_log.json")
    strategy_history = []
    if os.path.exists(strategy_log_path):
        with open(strategy_log_path, encoding="utf-8") as f:
            strategy_history = json.load(f)

    accuracy = 0
    if total_signal > 0:
        accuracy = round(correct / total_signal * 100, 1)

    today_log = {
        "date": date_str,
        "accuracy": accuracy,
        "correct": correct,
        "total": total_signal,
    }
    strategy_history.append(today_log)

    if len(strategy_history) > 30:
        strategy_history = strategy_history[-30:]

    with open(strategy_log_path, "w", encoding="utf-8") as f:
        json.dump(strategy_history, f, ensure_ascii=False, indent=2)

    lines.append(f"  今日信号准确率：{accuracy:.0f}% ({correct}/{total_signal})")
    if len(strategy_history) >= 2:
        prev_acc = strategy_history[-2]["accuracy"]
        trend = "📈提升" if accuracy > prev_acc else ("📉下降" if accuracy < prev_acc else "➡️持平")
        lines.append(f"  准确率趋势：{trend}（昨日{prev_acc:.0f}% → 今日{accuracy:.0f}%）")

    # 六、策略优化建议
    lines.append("\n七、策略优化建议")
    if accuracy < 50 and total_signal >= 3:
        lines.append("  ⚠️ 今日信号准确率<50%，明日建议：")
        lines.append("    1. 降低仓位至半仓以下，观望为主")
        lines.append("    2. 只操作高置信度信号（confidence>=7）")
    elif accuracy >= 70:
        lines.append("  ✅ 今日信号准确率>=70%，策略有效，明日可：")
        lines.append("    1. 维持当前仓位水平")
        lines.append("    2. 可适当提高个股关注度")
    else:
        lines.append("  ➡️ 今日信号准确率中等，维持当前策略：")
        lines.append("    1. 严格执行止损纪律（持仓股浮亏>5%必须止损）")
        lines.append("    2. 记录每笔操作的买入理由，周复盘时总结")

    if os.path.exists(STRATEGY_FILE):
        lines.append("\n  当前策略摘要：")
        with open(STRATEGY_FILE, encoding="utf-8") as f:
            content = f.read()
            non_empty = [l.strip() for l in content.split("\n") if l.strip()][:3]
            for l in non_empty:
                lines.append(f"    {l}")

    lines.append("\n" + "=" * 40)
    lines.append("📝 明日操作计划：结合今日复盘结果，明日早报将更新建议")
    lines.append("💡 每周日晚报后将生成本周策略迭代总结")

    report = "\n".join(lines)

    # 保存晚报
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(os.path.join(REPORT_DIR, f"{date_str}_evening.txt"), "w", encoding="utf-8") as f:
        f.write(report)

    return report


# ── 输出 & 推送 ─────────────────────────────────────────────
FEISHU_CHAT_ID = "oc_9ee5303497f5e0e71666b610d6bdc346"

def push_to_feishu(report_text):
    """用 lark-cli (bot身份) 将报告推送到飞书群"""
    max_len = 1800
    chunks = []
    current = ""
    for line in report_text.split("\n"):
        if len(current) + len(line) + 1 > max_len:
            chunks.append(current)
            current = line + "\n"
        else:
            current += line + "\n"
    if current:
        chunks.append(current)

    for i, chunk in enumerate(chunks):
        prefix = f"[第{i+1}/{len(chunks)}页]\n" if len(chunks) > 1 else ""
        content = prefix + chunk
        try:
            import subprocess
            r = subprocess.run(
                ["lark-cli", "im", "+messages-send", "--as", "bot",
                 "--chat-id", FEISHU_CHAT_ID, "--text", content],
                capture_output=True, text=True, timeout=30
            )
            result = json.loads(r.stdout)
            if result.get("ok"):
                print(f"  ✅ 飞书推送第{i+1}页成功", file=sys.stderr)
            else:
                print(f"  ⚠️ 飞书推送第{i+1}页失败: {result}", file=sys.stderr)
        except Exception as e:
            print(f"  ⚠️ 飞书推送异常: {e}", file=sys.stderr)


def print_report(report_text, push: bool = False):
    """打印报告到 stdout（供调试）。

    注意：本函数默认【不推送飞书群】。直接 `build_morning_report` 的输出
    是「公众号文章聚合」原始格式，并非最终早报标准格式（标准格式由
    自动化 prompt 定义的飞书文档 + 结构化群卡片生成）。若误推到群会造成
    格式混乱。如需推群，必须显式传 push=True（仅限自动化流程内部调用）。
    """
    if push:
        push_to_feishu(report_text)
    print(report_text)


# ── 入口 ────────────────────────────────────────────────────
def collect_data():
    """
    采集所有原始数据，输出结构化 JSON（供 Agent 做 LLM 分析）。
    不生成报告，不推送飞书。
    """
    now = datetime.now()
    articles = load_today_articles()

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

    print(json.dumps(data, ensure_ascii=False, indent=2))
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", choices=["morning", "evening"], help="生成早报或晚报")
    parser.add_argument("--collect-only", action="store_true", help="仅采集数据输出JSON，不做LLM分析和推送")
    parser.add_argument("--push", action="store_true", help="【仅自动化内部用】生成后推送飞书群。默认不推送，避免误推错误格式到群")
    args = parser.parse_args()

    if args.collect_only:
        collect_data()
        return

    report = build_morning_report() if args.period == "morning" else build_evening_report()

    # 默认只输出 stdout，不推群（防止公众号聚合原始格式误推到飞书群）
    print_report(report, push=args.push)


if __name__ == "__main__":
    main()
