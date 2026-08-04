#!/usr/bin/env python3
"""
缓存文章归档 + 信号提取 + 溯源统计 一键脚本
"""

import contextlib
import email.utils
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

CACHE_DIR = Path.home() / ".workbuddy" / "cache" / "wx_articles"
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ARCHIVE_DIR = PROJECT_ROOT / "archive" / "articles"
SIGNALS_FILE = PROJECT_ROOT / ".workbuddy" / "data" / "article_signals.json"
SCRIPT_DIR = Path(__file__).resolve().parent
ARCHIVE_STATS_FILE = PROJECT_ROOT / ".workbuddy" / "data" / "archive_stats.json"

# 已知的A股股票名称 → 代码映射（常用推荐股）
STOCK_MAP = {
    "金诚信": "603979",
    "鼎通科技": "688668",
    "路维光电": "688401",
    "华电国际": "600027",
    "紫光国微": "002049",
    "士兰微": "600460",
    "天娱数科": "002354",
    "有研新材": "600206",
    "烽火通信": "600498",
    "奥士康": "002913",
    "光迅科技": "002281",
    "沪电股份": "002463",
    "深南电路": "002916",
    "中兴通讯": "000063",
    "中际旭创": "300308",
    "新易盛": "300502",
    "天孚通信": "300394",
    "东山精密": "002384",
    "鹏鼎控股": "002938",
    "立讯精密": "002475",
    "工业富联": "601138",
    "浪潮信息": "000977",
    "中科曙光": "603019",
    "寒武纪": "688256",
    "海光信息": "688041",
    "中芯国际": "688981",
    "北方华创": "002371",
    "中微公司": "688012",
    "韦尔股份": "603501",
    "卓胜微": "300782",
}


def extract_stocks(content: str, title: str) -> list:
    """从文章内容中提取可能的股票推荐"""
    # 方法1：直接匹配已知股票名称
    found = []
    for name, code in STOCK_MAP.items():
        if name in content:
            found.append({"name": name, "code": code})

    # 方法2：匹配股票代码模式 (6位数字)
    codes = set(re.findall(r"\b(60[0-9]{4}|00[0-9]{4}|30[0-9]{4})\b", content))
    for code in codes:
        # 反向查找名称
        name = next((n for n, c in STOCK_MAP.items() if c == code), f"股票{code}")
        if not any(f["code"] == code for f in found):
            found.append({"name": name, "code": code})

    return found


# 08-03 修复：统一日期解析，避免 pub_time[:10] 把 RFC822 截断成 "Sat, 27 Ju"
_WEEKDAY = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
_MONTH_NAMES = [
    (1, "Jan"),
    (2, "Feb"),
    (3, "Mar"),
    (4, "Apr"),
    (5, "May"),
    (6, "Jun"),
    (7, "Jul"),
    (8, "Aug"),
    (9, "Sep"),
    (10, "Oct"),
    (11, "Nov"),
    (12, "Dec"),
]


def parse_pub_date(s: str, fallback_src: str = "") -> str:
    """把发布时间解析为 ISO 日期 YYYY-MM-DD；失败回退文件名前缀日期，再失败才返回今天。

    🔴 08-04 修复：原实现失败一律返回"今天"，导致批量回填(pub_time缺失)时把入库日误写成
    recorded_at，65条07-09/07-10文章被记成07-31/08-01，污染30日滚动胜率窗口。
    现在优先回退 source_file 文件名前缀日期(如 20260709_...json → 2026-07-09)。
    """
    if not s:
        return _fallback_date(fallback_src)
    s = str(s).strip()
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日?", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.match(r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun), (\d{1,2}) (\w+)", s)
    if m:
        wd, day, mon_p = m.group(1), int(m.group(2)), m.group(3)
        cands = []
        for y in (date.today().year - 1, date.today().year):
            for month, monname in _MONTH_NAMES:
                if monname.startswith(mon_p):
                    try:
                        d = date(y, month, day)
                        if _WEEKDAY[wd] == d.weekday():
                            cands.append(d)
                    except ValueError:
                        pass
        if cands:
            return min(cands, key=lambda d: abs((d - date.today()).days)).isoformat()
    try:
        t = email.utils.parsedate_to_datetime(s)
        if t:
            return t.date().isoformat()
    except Exception:
        pass
    return _fallback_date(fallback_src)


def _fallback_date(fallback_src: str) -> str:
    """从文件名前缀(YYYYMMDD_...)提取日期；无则返回今天。"""
    m = re.match(r"(\d{4})(\d{2})(\d{2})_", str(fallback_src))
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            pass
    return datetime.now().strftime("%Y-%m-%d")


def archive_articles():
    """归档缓存文章为 markdown 文件"""
    today = datetime.now().strftime("%Y-%m-%d")
    date_dir = ARCHIVE_DIR / today
    date_dir.mkdir(parents=True, exist_ok=True)

    archived = 0
    for f in sorted(CACHE_DIR.glob("*.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                article = json.load(fh)
        except Exception as e:
            print(f"  ⚠️ 跳过 {f.name}: {e}")
            continue

        title = article.get("title", f.stem)
        account = article.get("account", "未知")
        content = article.get("content", "")
        pub_time = article.get("publish_time", "")
        url = article.get("url", "")

        # 生成文件名
        safe_title = re.sub(r'[\\/:*?"<>|]', "_", title)[:40]
        md_path = date_dir / f"{account}_{safe_title}.md"

        md_content = f"""# {title}

- **公众号**: {account}
- **发布时间**: {pub_time}
- **原文链接**: {url}
- **归档时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

---

{content}
"""
        md_path.write_text(md_content, encoding="utf-8")
        archived += 1

    print(f"✅ 归档 {archived} 篇文章 → {date_dir}")
    return archived


def extract_signals():
    """从缓存文章中提取推荐信号"""
    signals = []

    for f in sorted(CACHE_DIR.glob("*.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                article = json.load(fh)
        except Exception:
            continue

        title = article.get("title", f.stem)
        account = article.get("account", "未知")
        content = article.get("content", "")
        pub_time = article.get("publish_time", "")

        # 提取股票
        stocks = extract_stocks(content, title)
        if not stocks:
            continue

        # 分析信号方向（看多/中性）
        bullish_keywords = [
            "买入",
            "推荐",
            "看好",
            "加仓",
            "爆发",
            "上涨",
            "突破",
            "反弹",
            "龙头",
            "机会",
        ]
        bearish_keywords = ["卖出", "减仓", "风险", "下跌", "回避", "止损", "利空"]

        for stock in stocks:
            # 判断信号方向
            signal = "neutral"
            # 先数 bullish/bearish 关键词出现次数
            bullish_count = sum(1 for kw in bullish_keywords if kw in content)
            bearish_count = sum(1 for kw in bearish_keywords if kw in content)
            if bullish_count > bearish_count:
                signal = "bullish"
            elif bearish_count > bullish_count:
                signal = "bearish"
            else:
                # 平局时默认为 bullish（推荐类公众号的常见情况）
                signal = "bullish" if bullish_count > 0 else "neutral"

            signals.append(
                {
                    # article_id 仅用于去重标识, 非密码学用途
                    "article_id": hashlib.md5(f.name.encode(), usedforsecurity=False).hexdigest()[
                        :12
                    ],
                    # 08-03 修复：内容哈希，同文多文件名(重复拉取)也能去重
                    "content_id": hashlib.md5(
                        f"{account}|{title}|{content}".encode(), usedforsecurity=False
                    ).hexdigest()[:12],
                    "account": account,
                    "title": title,
                    "stock_code": stock["code"],
                    "stock_name": stock["name"],
                    "signal": signal,
                    "target_price": None,
                    "confidence": 5,
                    "recorded_at": parse_pub_date(pub_time, f.name),
                    "verified": False,
                    "hit_target": None,
                    "hit_stop": None,
                    "final_return_pct": None,
                    "source_file": f.name,
                }
            )

    return signals


def main():
    print("=" * 50)
    print("📊 知识库维护：缓存文章归档 + 信号提取")
    print("=" * 50)

    # 步骤1：归档
    print("\n📦 步骤1：归档缓存文章...")
    archived = archive_articles()
    if archived == 0:
        print("  📭 无新文章需归档")

    # 步骤2：索引到知识库
    print("\n🔍 步骤2：索引到知识库...")
    sys.path.insert(0, str(SCRIPT_DIR.parent / "lib"))

    # 手动调用索引
    os.chdir(str(SCRIPT_DIR.parent))
    ret = subprocess.run(
        [sys.executable, str(SCRIPT_DIR / "knowledge_base.py"), "index"],
        capture_output=True,
        text=True,
        check=False,
    ).returncode
    if ret != 0:
        print("  ⚠️ 索引可能未完整执行，继续处理信号...")

    # 步骤3：提取并保存信号
    print("\n📝 步骤3：信号提取...")
    signals = extract_signals()
    print(f"  共提取 {len(signals)} 条信号")

    if signals:
        # 按公众号统计
        accounts = {}
        for s in signals:
            acc = s["account"]
            if acc not in accounts:
                accounts[acc] = {"count": 0, "bullish": 0, "stocks": set()}
            accounts[acc]["count"] += 1
            if s["signal"] == "bullish":
                accounts[acc]["bullish"] += 1
            accounts[acc]["stocks"].add(f"{s['stock_name']}({s['stock_code']})")

        print("\n📊 公众号信号概览:")
        print(f"  {'公众号':<12} {'文章':<6} {'看多':<6} {'涉及股票':<30}")
        print(f"  {'-' * 54}")
        for acc, info in sorted(accounts.items(), key=lambda x: -x[1]["count"]):
            stocks_str = ", ".join(list(info["stocks"])[:3])
            if len(info["stocks"]) > 3:
                stocks_str += f"...(+{len(info['stocks']) - 3})"
            print(f"  {acc:<12} {info['count']:<6} {info['bullish']:<6} {stocks_str:<30}")

        # 保存信号到文件
        # 合并现有信号
        existing = []
        if SIGNALS_FILE.exists():
            with contextlib.suppress(json.JSONDecodeError, OSError):
                existing = json.loads(SIGNALS_FILE.read_text())

        # 去重合并：article_id(文件名) + content_id(内容，08-03 修复同文多文件名重复入库)
        existing_ids = {s.get("article_id", "") for s in existing}
        existing_content = {s.get("content_id", "") for s in existing if s.get("content_id")}
        new_signals = []
        seen_content = set()
        for s in signals:
            cid = s.get("content_id", "")
            if cid and (cid in existing_content or cid in seen_content):
                continue  # 同一篇文章重复缓存（不同文件名），跳过
            if cid:
                seen_content.add(cid)
            if s["article_id"] not in existing_ids:
                new_signals.append(s)

        all_signals = existing + new_signals
        SIGNALS_FILE.write_text(json.dumps(all_signals, ensure_ascii=False, indent=2))
        print(f"\n  ✅ 新增 {len(new_signals)} 条信号（累计 {len(all_signals)} 条）")

        # 步骤4：溯源统计
        print("\n📈 步骤4：信号溯源分析...")
        ret = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "knowledge_base.py"), "trace", "--days", "60"],
            capture_output=True,
            text=True,
            check=False,
        )
    else:
        print("\n  📭 未能提取到有效信号")

    print(f"\n{'=' * 50}")
    print("✅ 维护完成")
    print(f"  归档: {ARCHIVE_DIR}/{datetime.now().strftime('%Y-%m-%d')}/")
    print(f"  信号: {SIGNALS_FILE}")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    main()
