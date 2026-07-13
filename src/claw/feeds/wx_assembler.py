"""微信早报/晚报组装器 — 从采集数据构建结构化报告。

此模块仅负责报告组装逻辑，不直接调用外部 API。
所有数据通过 wx_collector 模块获取。
"""

import os
import sys
from datetime import datetime

# 确保项目根在 sys.path 中，以便导入同级模块
from pathlib import Path as _Path
_SELF = _Path(__file__).resolve()
_PROJECT_ROOT = _SELF.parent.parent.parent.parent
if str(_PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from claw.feeds.wx_collector import (  # noqa: E402
    _HAS_SUMMARIZE,
    REPORT_DIR,
    call_sim_trade_auto_check,
    extract_article_stocks,
    fetch_cls_telegraph,
    fetch_current_price,
    fetch_eastmoney_news,
    fetch_macro_calendar,
    fetch_sector_hot,
    fetch_today_kline,
    get_technical_signal,
    load_portfolios,
    load_today_articles,
)

if _HAS_SUMMARIZE:
    from summarize_batch import summarize_article_content  # noqa: E402

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

