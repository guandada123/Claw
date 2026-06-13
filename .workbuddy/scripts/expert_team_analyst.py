#!/usr/bin/env python3
"""
多专家投研协作分析脚本 v2
不依赖WorkBuddy对话专家，直接使用腾讯财经API + 现有数据源
按顺序分析5个维度，汇总输出综合投资建议
"""

import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

# 加载 Claw 公共库
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))
from error_handler import atomic_write_json

PROJECT_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_DIR / "scripts"
DATA_DIR = PROJECT_DIR / "data"

EXPERT_NAMES_ZH = [
    "价值投资专家",
    "技术分析专家",
    "宏观经济专家",
    "资金面专家",
    "市场情绪专家",
]


def fetch_tencent_quote(symbol: str) -> dict | None:
    """从腾讯财经API获取实时行情（3-5秒延迟）"""
    # 判断交易所：600xxx/601xxx → sh, 000xxx/002xxx → sz
    if symbol.startswith(("6", "5")):
        code = f"sh{symbol}"
    elif symbol.startswith(("0", "3")):
        code = f"sz{symbol}"
    else:
        code = f"sh{symbol}"

    url = f"https://qt.gtimg.cn/q={code}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("gbk", errors="replace")

        # 解析：v_sh600519="name~code~price~..."
        match = re.search(r'="([^"]*)"', raw)
        if not match:
            return None

        fields = match.group(1).split("~")
        if len(fields) < 45:
            return None

        return {
            "name": fields[1].strip(),
            "code": fields[2].strip(),
            "price": float(fields[3]) if fields[3] else 0,
            "prev_close": float(fields[4]) if fields[4] else 0,
            "open": float(fields[5]) if fields[5] else 0,
            "volume": int(fields[6]) if fields[6] else 0,  # 手
            "high": float(fields[33]) if fields[33] else 0,
            "low": float(fields[34]) if fields[34] else 0,
            "change_pct": float(fields[32]) if fields[32] else 0,  # 涨跌幅%
            "pe": float(fields[39]) if fields[39] and fields[39] != "" else 0,
            "total_value": float(fields[45])
            if len(fields) > 45 and fields[45]
            else 0,  # 总市值(亿)
        }
    except Exception as e:
        print(f"  ⚠️ 腾讯财经API请求失败: {e}")
        return None


# ═════════════════════════════════════════
# 专家分析函数
# ═════════════════════════════════════════


def analyze_value_investing(quote: dict) -> dict:
    """价值投资专家：PE估值 + 市值判断"""
    price = quote.get("price", 0)
    pe = quote.get("pe", 0)
    total_value = quote.get("total_value", 0)
    name = quote.get("name", "")

    score = 5
    conclusion = "持有"
    key_points = []
    reason = ""

    # PE分析
    if pe > 0:
        if pe < 15:
            score = min(10, score + 3)
            conclusion = "买入"
            key_points.append(f"PE={pe:.1f}，处于低估区间（<15）")
        elif pe > 30:
            score = max(1, score - 3)
            conclusion = "卖出"
            key_points.append(f"PE={pe:.1f}，处于高估区间（>30）")
        else:
            key_points.append(f"PE={pe:.1f}，估值合理")
    else:
        key_points.append("PE数据缺失，需进一步研究")

    # 市值分析
    if total_value > 0:
        if total_value > 5000:
            key_points.append(f"总市值{total_value:.0f}亿，大盘蓝筹，流动性好")
        elif total_value > 500:
            key_points.append(f"总市值{total_value:.0f}亿，中盘股")
        else:
            key_points.append(f"总市值{total_value:.0f}亿，小盘股，波动较大")

    # 当前价格参考
    if price > 0:
        key_points.append(f"当前价{price:.2f}元")

    reason = (
        f"PE={pe:.1f}，{'低估' if pe < 15 else '高估' if pe > 30 else '估值合理'}，建议{conclusion}"
    )

    return {
        "expert_name": "价值投资专家",
        "conclusion": conclusion,
        "score": score,
        "reason": reason,
        "key_points": key_points,
    }


def analyze_technical(quote: dict) -> dict:
    """技术分析专家：价格趋势 + 日内走势 + 涨跌幅 (v2: 集成 star_signal)"""
    price = quote.get("price", 0)
    prev_close = quote.get("prev_close", 0)
    open_price = quote.get("open", 0)
    high = quote.get("high", 0)
    low = quote.get("low", 0)
    change_pct = quote.get("change_pct", 0)
    code = quote.get("code", "")

    # 尝试使用 star_signal 获取更精确的技术评分
    star_score = None
    try:
        from star_signal_adapter import get_technical_score

        ts = get_technical_score(code)
        if "error" not in ts:
            star_score = ts
    except ImportError:
        print("  ⚠️ star_signal 模块未安装，跳过信号分析")

    score = 5
    conclusion = "持有"
    key_points = []

    # star_signal 优先
    if star_score:
        score = star_score["score"]
        conclusion = star_score["conclusion"]
        key_points.append(
            f"⭐ 五角星信号: {star_score['strength_name']}({star_score['signal_score']:.0f}/100)"
        )
        key_points.append(
            f"   均线排列={star_score['ma_alignment']:.2f} | 动量={star_score['momentum']:.2f}"
        )
        key_points.append(
            f"   量能={star_score['volume_signal']:.2f} | 突破={star_score['breakout_signal']:.2f}"
        )
        key_points.append(f"   RSI={star_score['rsi']:.1f} | MACD柱={star_score['macd_hist']:.4f}")
    # 降级到原有简单逻辑
    elif change_pct > 3:
        score = min(10, score + 2)
        conclusion = "买入"
        key_points.append(f"涨幅{change_pct:.2f}%，强势上涨")
    elif change_pct > 1:
        score = min(10, score + 1)
        key_points.append(f"涨幅{change_pct:.2f}%，温和上涨")
    elif change_pct > -1:
        key_points.append(f"涨跌{change_pct:+.2f}%，横盘震荡")
    elif change_pct > -3:
        score = max(1, score - 1)
        key_points.append(f"跌幅{change_pct:.2f}%，弱势调整")
    else:
        score = max(1, score - 2)
        conclusion = "卖出"
        key_points.append(f"跌幅{change_pct:.2f}%，大幅下跌")

    # 日内走势分析（保留原有逻辑作为补充）
    if price > open_price > 0:
        key_points.append(f"日内上涨（开{open_price:.2f}→收{price:.2f}），多头占优")
    elif price < open_price and open_price > 0:
        key_points.append(f"日内下跌（开{open_price:.2f}→收{price:.2f}），空头占优")

    if high > 0 and low > 0:
        amplitude = (high - low) / low * 100
        key_points.append(f"振幅{amplitude:.2f}%，最高{high:.2f}，最低{low:.2f}")

    reason = (
        f"涨跌{change_pct:+.2f}%，{'⭐信号' + conclusion if star_score else '建议' + conclusion}"
    )

    return {
        "expert_name": "技术分析专家",
        "conclusion": conclusion,
        "score": round(score, 1),
        "reason": reason,
        "key_points": key_points,
        "support_price": round(min(price, prev_close) * 0.95, 2) if price > 0 else 0,
        "resistance_price": round(max(price, prev_close) * 1.05, 2) if price > 0 else 0,
        "star_signal": star_score is not None,  # 标记使用了增强信号
    }


def analyze_macro(quote: dict) -> dict:
    """
    宏观经济专家：行业趋势 + 政策环境
    注意：实时宏观数据需要通过 macro-monitor skill 获取
    此处给出中性判断，提示用户参考宏观监控
    """
    name = quote.get("name", "")
    pe = quote.get("pe", 0)

    score = 5
    conclusion = "持有"
    key_points = [
        "宏观经济环境需结合最新GDP/CPI/PMI数据判断",
        "政策面：关注央行货币政策和行业监管动态",
        f"行业估值：PE={pe:.1f}，需横向对比同行业均值",
        "国际市场：关注美联储利率政策和地缘风险",
    ]

    reason = "宏观数据需通过 macro-monitor 获取，当前基于行业估值给出中性判断，建议持有"

    return {
        "expert_name": "宏观经济专家",
        "conclusion": conclusion,
        "score": score,
        "reason": reason,
        "key_points": key_points,
    }


def analyze_capital_flow(quote: dict) -> dict:
    """资金面专家：成交量分析 + 市值分析"""
    volume = quote.get("volume", 0)
    price = quote.get("price", 0)
    change_pct = quote.get("change_pct", 0)
    total_value = quote.get("total_value", 0)

    score = 5
    conclusion = "持有"
    key_points = []

    # 成交量分析
    amount = volume * 100 * price if price > 0 else 0
    if amount > 0:
        amt_desc = f"成交额{amount / 1e8:.2f}亿"
        if amount > 50e8:
            score = min(10, score + 2)
            key_points.append(f"{amt_desc}，交投活跃，资金关注度高")
        elif amount > 10e8:
            score = min(10, score + 1)
            key_points.append(f"{amt_desc}，交投正常")
        else:
            key_points.append(f"{amt_desc}，交投清淡")

    # 量价关系
    if volume > 0 and change_pct != 0:
        if change_pct > 0:
            conclusion = "买入"
            score = min(10, score + 1)
            key_points.append("量价配合，放量上涨，主力资金流入迹象")
        elif change_pct < -2:
            conclusion = "卖出"
            score = max(1, score - 1)
            key_points.append("放量下跌，主力资金流出迹象")

    # 市值参考
    if total_value > 0:
        key_points.append(
            f"总市值{total_value:.0f}亿，{'流动性充裕' if total_value > 1000 else '中等流动性'}"
        )

    reason = f"成交额{amount / 1e8:.2f}亿，{'交投活跃' if amount > 10e8 else '交投清淡'}，{'主力流入' if change_pct > 0 else '主力流出' if change_pct < 0 else '资金平衡'}，建议{conclusion}"

    return {
        "expert_name": "资金面专家",
        "conclusion": conclusion,
        "score": score,
        "reason": reason,
        "key_points": key_points,
    }


def analyze_sentiment(quote: dict) -> dict:
    """市场情绪专家：涨跌幅 + 市场情绪综合判断"""
    change_pct = quote.get("change_pct", 0)
    name = quote.get("name", "")
    price = quote.get("price", 0)

    score = 5
    conclusion = "持有"
    key_points = []

    # 涨跌幅情绪判断
    if change_pct > 5:
        score = min(10, score + 3)
        conclusion = "买入"
        key_points.append(f"日涨幅{change_pct:.2f}%，市场情绪极度乐观，追涨需谨慎")
    elif change_pct > 2:
        score = min(10, score + 2)
        conclusion = "买入"
        key_points.append(f"日涨幅{change_pct:.2f}%，市场情绪偏乐观")
    elif change_pct > 0:
        score = min(10, score + 1)
        key_points.append(f"日涨幅{change_pct:.2f}%，市场情绪温和")
    elif change_pct > -2:
        key_points.append(f"日跌幅{change_pct:.2f}%，市场情绪中性偏弱")
    elif change_pct > -5:
        score = max(1, score - 1)
        conclusion = "卖出"
        key_points.append(f"日跌幅{change_pct:.2f}%，市场情绪偏悲观")
    else:
        score = max(1, score - 2)
        conclusion = "卖出"
        key_points.append(f"日跌幅{change_pct:.2f}%，市场情绪极度悲观，恐慌抛售")

    # 炒作热度
    key_points.append(
        f"{name}当前价{price:.2f}元，{'热点炒作可能性高' if abs(change_pct) > 3 else '无明显炒作迹象'}"
    )
    key_points.append("建议关注同板块其他个股涨跌情况，判断是否为板块性行情")

    reason = f"涨跌{change_pct:+.2f}%，{'市场情绪乐观' if change_pct > 0 else '市场情绪悲观' if change_pct < 0 else '市场情绪中性'}，建议{conclusion}"

    return {
        "expert_name": "市场情绪专家",
        "conclusion": conclusion,
        "score": score,
        "reason": reason,
        "key_points": key_points,
    }


# ═════════════════════════════════════════
# 汇总
# ═════════════════════════════════════════


def summarize_analysis(results: list[dict], quote: dict) -> dict:
    """汇总所有专家的分析结果"""
    conclusions = []
    buy_signals = 0
    sell_signals = 0
    hold_signals = 0
    total_score = 0

    for r in results:
        conclusion = r.get("conclusion", "持有")
        score = r.get("score", 5)

        conclusions.append(
            {
                "expert_name": r["expert_name"],
                "conclusion": conclusion,
                "score": score,
                "reason": r["reason"],
            }
        )

        if conclusion == "买入":
            buy_signals += 1
        elif conclusion == "卖出":
            sell_signals += 1
        else:
            hold_signals += 1

        total_score += score

    # 综合结论
    if buy_signals >= 3:
        final_conclusion = "买入"
    elif sell_signals >= 3:
        final_conclusion = "卖出"
    else:
        final_conclusion = "持有"

    avg_score = round(total_score / len(results), 1) if results else 5

    # 建议价位
    price = quote.get("price", 0)
    if price > 0:
        suggest_buy_price = round(price * 0.95, 2)
        suggest_sell_price = round(price * 1.30, 2)
        suggest_stop_loss = round(price * 0.92, 2)
    else:
        suggest_buy_price = 0
        suggest_sell_price = 0
        suggest_stop_loss = 0

    return {
        "symbol": quote.get("code", ""),
        "name": quote.get("name", ""),
        "expert_conclusions": conclusions,
        "buy_signals": buy_signals,
        "sell_signals": sell_signals,
        "hold_signals": hold_signals,
        "final_conclusion": final_conclusion,
        "avg_score": avg_score,
        "suggest_buy_price": suggest_buy_price,
        "suggest_sell_price": suggest_sell_price,
        "suggest_stop_loss": suggest_stop_loss,
        "analyzed_at": datetime.now().isoformat(),
    }


def format_report(summary: dict, quote: dict) -> str:
    """格式化为可读报告"""
    lines = []
    lines.append(f"\n{'=' * 60}")
    lines.append("  📊 多专家投研分析报告")
    lines.append(f"{'=' * 60}")
    lines.append(f"  股票：{summary.get('name', '')}（{summary.get('symbol', '')}）")
    lines.append(
        f"  当前价：{quote.get('price', 0):.2f}元  |  涨跌：{quote.get('change_pct', 0):+.2f}%"
    )
    lines.append(f"  分析时间：{summary.get('analyzed_at', '')}")
    lines.append(f"{'─' * 60}")

    for i, conc in enumerate(summary.get("expert_conclusions", []), 1):
        emoji_map = {"买入": "🔴", "持有": "🟡", "卖出": "🟢"}
        emoji = emoji_map.get(conc["conclusion"], "⚪")
        lines.append(
            f"\n  {i}. {emoji} {conc['expert_name']}：{conc['conclusion']}（{conc['score']}分）"
        )
        lines.append(f"     {conc['reason']}")

    lines.append(f"\n{'─' * 60}")
    lines.append("\n  📋 综合结论")
    lines.append(f"  {'─' * 40}")
    lines.append(f"  买入信号：{summary.get('buy_signals', 0)}个")
    lines.append(f"  卖出信号：{summary.get('sell_signals', 0)}个")
    lines.append(f"  持有信号：{summary.get('hold_signals', 0)}个")
    lines.append(f"  综合评分：{summary.get('avg_score', 5)}分（满分10分）")

    final_emoji_map = {"买入": "🔴 买入", "持有": "🟡 持有", "卖出": "🟢 卖出"}
    lines.append(
        f"  综合结论：{final_emoji_map.get(summary.get('final_conclusion', '持有'), summary.get('final_conclusion', '持有'))}"
    )

    buy_price = summary.get("suggest_buy_price", 0)
    sell_price = summary.get("suggest_sell_price", 0)
    stop_loss = summary.get("suggest_stop_loss", 0)

    if buy_price > 0:
        lines.append("\n  💰 建议价位")
        lines.append(
            f"  买入价：{buy_price:.2f}元  |  卖出价：{sell_price:.2f}元  |  止损价：{stop_loss:.2f}元"
        )

    lines.append(f"\n{'=' * 60}\n")
    return "\n".join(lines)


# ═════════════════════════════════════════
# 主函数
# ═════════════════════════════════════════


def analyze_stock(symbol: str) -> dict:
    """对指定股票进行多维度分析"""
    print(f"\n🔍 开始对 {symbol} 进行多维度分析...")

    # 1. 获取实时行情
    print("[1/6] 获取实时行情...")
    quote = fetch_tencent_quote(symbol)

    if not quote:
        print(f"  ❌ 无法获取 {symbol} 的行情数据")
        return {
            "error": f"无法获取 {symbol} 的行情数据",
            "symbol": symbol,
        }

    print(f"  ✅ {quote['name']}，当前价 {quote['price']:.2f}，涨跌 {quote['change_pct']:+.2f}%")

    # 2. 价值投资分析
    print("[2/6] 价值投资分析...")
    value_result = analyze_value_investing(quote)
    print(f"  ✅ {value_result['conclusion']}（{value_result['score']}分）")

    # 3. 技术分析
    print("[3/6] 技术分析...")
    tech_result = analyze_technical(quote)
    print(f"  ✅ {tech_result['conclusion']}（{tech_result['score']}分）")

    # 4. 宏观分析
    print("[4/6] 宏观经济分析...")
    macro_result = analyze_macro(quote)
    print(f"  ✅ {macro_result['conclusion']}（{macro_result['score']}分）")

    # 5. 资金面分析
    print("[5/6] 资金面分析...")
    capital_result = analyze_capital_flow(quote)
    print(f"  ✅ {capital_result['conclusion']}（{capital_result['score']}分）")

    # 6. 市场情绪分析
    print("[6/6] 市场情绪分析...")
    sentiment_result = analyze_sentiment(quote)
    print(f"  ✅ {sentiment_result['conclusion']}（{sentiment_result['score']}分）")

    # 7. 汇总
    print("\n📊 汇总分析结果...")
    results = [value_result, tech_result, macro_result, capital_result, sentiment_result]
    summary = summarize_analysis(results, quote)

    # 8. 格式化报告
    report = format_report(summary, quote)
    print(report)

    # 9. 保存结果
    output_file = (
        SCRIPTS_DIR / f"expert_analysis_{symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    output = {
        "quote": quote,
        "experts": {r["expert_name"]: r for r in results},
        "final_summary": summary,
    }
    atomic_write_json(output_file, output)
    print(f"📁 分析结果已保存到：{output_file}")

    return output


if __name__ == "__main__":
    symbol = sys.argv[1] if len(sys.argv) > 1 else "600519"

    result = analyze_stock(symbol)

    if "error" not in result:
        print("✅ 分析完成")
    else:
        print(f"❌ 分析失败: {result['error']}")
