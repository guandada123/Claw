#!/usr/bin/env python3
"""
缓存文章归档 + 信号提取 + 溯源统计 一键脚本
"""
import json, os, re, hashlib
from pathlib import Path
from datetime import datetime, timezone

CACHE_DIR = Path.home() / ".workbuddy" / "cache" / "wx_articles"
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ARCHIVE_DIR = PROJECT_ROOT / "archive" / "articles"
SIGNALS_FILE = PROJECT_ROOT / ".workbuddy" / "data" / "article_signals.json"
SCRIPT_DIR = Path(__file__).resolve().parent
ARCHIVE_STATS_FILE = PROJECT_ROOT / ".workbuddy" / "data" / "archive_stats.json"

# 已知的A股股票名称 → 代码映射（常用推荐股）
STOCK_MAP = {
    "金诚信": "603979", "鼎通科技": "688668", "路维光电": "688401",
    "华电国际": "600027", "紫光国微": "002049", "士兰微": "600460",
    "天娱数科": "002354", "有研新材": "600206", "烽火通信": "600498",
    "奥士康": "002913", "光迅科技": "002281", "沪电股份": "002463",
    "深南电路": "002916", "中兴通讯": "000063", "中际旭创": "300308",
    "新易盛": "300502", "天孚通信": "300394", "东山精密": "002384",
    "鹏鼎控股": "002938", "立讯精密": "002475", "工业富联": "601138",
    "浪潮信息": "000977", "中科曙光": "603019", "寒武纪": "688256",
    "海光信息": "688041", "中芯国际": "688981", "北方华创": "002371",
    "中微公司": "688012", "韦尔股份": "603501", "卓胜微": "300782",
}

def extract_stocks(content: str, title: str) -> list:
    """从文章内容中提取可能的股票推荐"""
    # 方法1：直接匹配已知股票名称
    found = []
    for name, code in STOCK_MAP.items():
        if name in content:
            found.append({"name": name, "code": code})
    
    # 方法2：匹配股票代码模式 (6位数字)
    codes = set(re.findall(r'\b(60[0-9]{4}|00[0-9]{4}|30[0-9]{4})\b', content))
    for code in codes:
        # 反向查找名称
        name = next((n for n, c in STOCK_MAP.items() if c == code), f"股票{code}")
        if not any(f['code'] == code for f in found):
            found.append({"name": name, "code": code})
    
    return found

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
        safe_title = re.sub(r'[\\/:*?"<>|]', '_', title)[:40]
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
        except:
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
        bullish_keywords = ["买入", "推荐", "看好", "加仓", "爆发", "上涨", "突破", "反弹", "龙头", "机会"]
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
            
            signals.append({
                "article_id": hashlib.md5(f.name.encode()).hexdigest()[:12],
                "account": account,
                "title": title,
                "stock_code": stock["code"],
                "stock_name": stock["name"],
                "signal": signal,
                "target_price": None,
                "confidence": 5,
                "recorded_at": pub_time[:10] if pub_time else datetime.now().strftime("%Y-%m-%d"),
                "verified": False,
                "hit_target": None,
                "hit_stop": None,
                "final_return_pct": None,
                "source_file": f.name,
            })
    
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
    import sys
    sys.path.insert(0, str(SCRIPT_DIR.parent / "lib"))
    
    # 手动调用索引
    os.chdir(str(SCRIPT_DIR.parent))
    ret = os.system(f"/usr/bin/python3 {SCRIPT_DIR}/knowledge_base.py index 2>&1")
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
        
        print(f"\n📊 公众号信号概览:")
        print(f"  {'公众号':<12} {'文章':<6} {'看多':<6} {'涉及股票':<30}")
        print(f"  {'-'*54}")
        for acc, info in sorted(accounts.items(), key=lambda x: -x[1]["count"]):
            stocks_str = ", ".join(list(info["stocks"])[:3])
            if len(info["stocks"]) > 3:
                stocks_str += f"...(+{len(info['stocks'])-3})"
            print(f"  {acc:<12} {info['count']:<6} {info['bullish']:<6} {stocks_str:<30}")
        
        # 保存信号到文件
        # 合并现有信号
        existing = []
        if SIGNALS_FILE.exists():
            try:
                existing = json.loads(SIGNALS_FILE.read_text())
            except:
                pass
        
        # 去重合并
        existing_ids = {s.get("article_id", "") for s in existing}
        new_signals = [s for s in signals if s["article_id"] not in existing_ids]
        
        all_signals = existing + new_signals
        SIGNALS_FILE.write_text(json.dumps(all_signals, ensure_ascii=False, indent=2))
        print(f"\n  ✅ 新增 {len(new_signals)} 条信号（累计 {len(all_signals)} 条）")
        
        # 步骤4：溯源统计
        print("\n📈 步骤4：信号溯源分析...")
        ret = os.system(f"/usr/bin/python3 {SCRIPT_DIR}/knowledge_base.py trace --days 60 2>&1")
    else:
        print("\n  📭 未能提取到有效信号")
    
    print(f"\n{'='*50}")
    print("✅ 维护完成")
    print(f"  归档: {ARCHIVE_DIR}/{datetime.now().strftime('%Y-%m-%d')}/")
    print(f"  信号: {SIGNALS_FILE}")
    print(f"{'='*50}")

if __name__ == "__main__":
    main()
