"""微信早报/晚报组装器 — 从采集数据构建结构化报告。

此模块仅负责报告组装逻辑，不直接调用外部 API。
所有数据通过 wx_collector 模块获取。
"""

import json
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
    STRATEGY_FILE,
    call_sim_trade_auto_check,
    extract_article_stocks,
    fetch_cls_telegraph,
    fetch_current_price,
    fetch_eastmoney_news,
    fetch_macro_calendar,
    fetch_sector_hot,
    fetch_today_kline,
    get_subscriptions,
    get_technical_signal,
    load_portfolios,
    load_today_articles,
)

if _HAS_SUMMARIZE:
    from summarize_batch import summarize_article_content  # noqa: E402

VERIFY_REPORT = _Path(_PROJECT_ROOT / ".workbuddy" / "data" / "signal_verify_report.json")


def load_signal_weights():
    """读取信号验证报告，返回按公众号的权重摘要。
    Returns: {"best": [{account, win_rate, avg_return, signals}], "ranking": [...]}
    """
    try:
        if not VERIFY_REPORT.exists():
            return {"best": [], "ranking": [], "updated": None}
        report = json.loads(VERIFY_REPORT.read_text(encoding="utf-8"))
        ranking = report.get("ranking", [])
        best = [r for r in ranking if r.get("win_rate") and r["win_rate"] >= 40
                and r.get("total", r.get("signals", 0)) >= 3]
        return {
            "best": best[:8],
            "ranking": ranking,
            "updated": report.get("generated_at", ""),
        }
    except Exception:
        return {"best": [], "ranking": [], "updated": None}

def build_morning_report():
    now = datetime.now()
    articles = load_today_articles()

    # ── 接入 summarize 技能：为每篇文章生成 200 字摘要 ────
    article_summaries = {}
    if _HAS_SUMMARIZE and articles:
        for art in articles:
            try:
                s = summarize_article_content(art)
                if s:
                    article_summaries[art.get("title", "")] = s
            except Exception as e:
                pass
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
            pass

    # 财经热讯 + 板块热度 + 宏观数据
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

    # 监控中的公众号列表
    accounts = []
    try:
        subs = get_subscriptions().get("subscriptions", [])
        accounts = [s["nickname"] for s in subs if s.get("nickname")]
        lines.append(f"\n📡 监控公众号（{len(accounts)}个）：{'、'.join(accounts)}")
    except Exception:
        lines.append("\n📡 监控公众号：获取失败（查看 wx_rss_auth 配置）")

    # 公众号信号权重（从 signal_verify_report.json 自动加载）
    sw = load_signal_weights()
    if sw["best"]:
        lines.append("\n**📊 公众号信号权重（历史命中率，自动统计）：**")
        for rank in sw["best"]:
            icon = "⭐" if rank["win_rate"] >= 60 else "✅"
            sigs = rank.get("total", rank.get("signals", 0))
            lines.append(
                f"  {icon} {rank['account']}："
                f"命中率 {rank['win_rate']}% / 均收益 {rank['avg_return']:+.1f}%"
                f"（{sigs}条信号，高权重重点采信）" if rank["win_rate"] >= 60 else
                f"  {icon} {rank['account']}："
                f"命中率 {rank['win_rate']}% / 均收益 {rank['avg_return']:+.1f}%"
                f"（{sigs}条信号，权重中等）"
            )
    else:
        lines.append("\n**📊 公众号信号权重：** 暂无历史命中率数据")

    # 信号有效性警示（双源 STALE 判断）
    if sw["updated"]:
        lines.append(f"\n⚠️ 信号权重最后更新：{sw['updated']}（>24h 为 STALE，需重新验证）")

    if not sw.get("best") or len(sw.get("best", [])) < 3:
        untracked = [a for a in accounts[:6] if all(a != r["account"] for r in sw.get("ranking", []))]
        if untracked:
            lines.append(f"⚪ 以下公众号尚无命中率数据，信号仅供参考：{'、'.join(untracked[:6])}")

    # 外部发现账号（红狐API搜索，待验证胜率）
    ranking_file = _Path(_PROJECT_ROOT / "data" / "signal_ranking.json")
    if ranking_file.exists():
        try:
            ranking_full = json.loads(ranking_file.read_text(encoding="utf-8"))
            discovered = [r for r in ranking_full.get("ranking", [])
                          if r.get("win_rate") is None and r.get("source") == "红狐发现"]
            if discovered:
                lines.append(f"\n📡 **外部发现（{len(discovered)}个候选，待验证胜率）：**")
                for d in discovered[:5]:
                    lines.append(f"  🔍 {d['name']}（{d.get('signals', 0)}篇相关文章）")
                lines.append("  __完整排名 → data/signal_ranking.json__")
        except Exception:
            pass

    # QTS×公众号 信号共识（双源交叉验证）
    consensus_file = _Path(_PROJECT_ROOT / "data" / "signal_consensus.json")
    if consensus_file.exists():
        try:
            consensus_data = json.loads(consensus_file.read_text(encoding="utf-8"))
            pairs = consensus_data.get("pairs", [])
            summary = consensus_data.get("summary", {})
            if pairs:
                strong = [p for p in pairs if p["consensus_score"] >= 2]
                conflict = [p for p in pairs if p["consensus_score"] < 0]
                lines.append("\n**🔗 双源信号共识（QTS回测 × 公众号，历史命中率加权）：**")
                if strong:
                    strong_names = ", ".join(
                        p["code"] + "(" + p.get("name", "") + ")"
                        for p in strong[:3]
                    )
                    lines.append(f"  🟢 强共识 {len(strong)}只：{strong_names}  权重×1.3")
                if summary.get("weak_signal", 0) > 0:
                    lines.append(f"  🟡 弱信号 {summary['weak_signal']}只：QTS与公众号各自独立覆盖")
                if conflict:
                    conflict_names = ", ".join(
                        p["code"] + "(" + p.get("name", "") + ")"
                        for p in conflict[:3]
                    )
                    lines.append(f"  🔴 分歧 {len(conflict)}只：{conflict_names}  建议观望")
                lines.append(f"  __完整对比 → data/signal_consensus.json__")
        except Exception:
            pass

    # QTS 市场状态（牛/熊/震荡/过渡 → 仓位建议）
    regime_file = _Path(_PROJECT_ROOT / "data" / "qts_regime.json")
    if regime_file.exists():
        try:
            regime_data = json.loads(regime_file.read_text(encoding="utf-8"))
            pos_mult = regime_data.get("position_multiplier", 0.5)
            regime_label = regime_data.get("regime_label", "未知")
            lines.append(f"\n**📈 QTS 市场状态：**{regime_label}")
            sizetip = "全仓" if pos_mult >= 1.0 else ("半仓" if pos_mult >= 0.5 else ("轻仓" if pos_mult >= 0.25 else "空仓"))
            lines.append(f"  💰 建议仓位系数：{pos_mult:.1f}x（{sizetip}）")
        except Exception:
            pass

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

    # 监控中的公众号列表
    accounts = []
    try:
        subs = get_subscriptions().get("subscriptions", [])
        accounts = [s["nickname"] for s in subs if s.get("nickname")]
        lines.append(f"\n📡 监控公众号（{len(accounts)}个）：{'、'.join(accounts)}")
    except Exception:
        lines.append("\n📡 监控公众号：获取失败（查看 wx_rss_auth 配置）")

    # 公众号信号权重（晚报完整版，对齐早报）
    sw = load_signal_weights()
    if sw["best"]:
        lines.append("\n**📊 公众号信号权重（历史命中率，自动统计）：**")
        for rank in sw["best"][:5]:
            icon = "⭐" if rank["win_rate"] >= 60 else "✅"
            sigs = rank.get("total", rank.get("signals", 0))
            lines.append(
                f"  {icon} {rank['account']}：命中率 {rank['win_rate']}% / "
                f"均收益 {rank['avg_return']:+.1f}%（{sigs}条信号）" if rank["win_rate"] >= 60 else
                f"  {icon} {rank['account']}：命中率 {rank['win_rate']}% / "
                f"均收益 {rank['avg_return']:+.1f}%（{sigs}条信号，权重中等）"
            )
    else:
        lines.append("\n**📊 公众号信号权重：** 暂无历史命中率数据")
    if sw["updated"]:
        lines.append(f"\n⚠️ 信号权重最后更新：{sw['updated']}（>24h 为 STALE，需重新验证）")

    # 外部发现账号（红狐API搜索，待验证胜率）
    ranking_file = _Path(_PROJECT_ROOT / "data" / "signal_ranking.json")
    if ranking_file.exists():
        try:
            ranking_full = json.loads(ranking_file.read_text(encoding="utf-8"))
            discovered = [r for r in ranking_full.get("ranking", [])
                          if r.get("win_rate") is None and r.get("source") == "红狐发现"]
            if discovered:
                lines.append(f"\n📡 **外部发现（{len(discovered)}个候选，待验证胜率）：**")
                for d in discovered[:5]:
                    lines.append(f"  🔍 {d['name']}（{d.get('signals', 0)}篇相关文章）")
                lines.append("  __完整排名 → data/signal_ranking.json__")
        except Exception:
            pass

    # QTS×公众号 信号共识（收盘验证）
    consensus_file = _Path(_PROJECT_ROOT / "data" / "signal_consensus.json")
    if consensus_file.exists():
        try:
            consensus_data = json.loads(consensus_file.read_text(encoding="utf-8"))
            pairs = consensus_data.get("pairs", [])
            summary = consensus_data.get("summary", {})
            if pairs:
                strong = [p for p in pairs if p["consensus_score"] >= 2]
                conflict = [p for p in pairs if p["consensus_score"] < 0]
                lines.append("\n**🔗 双源信号共识（QTS回测 × 公众号，收盘复核）：**")
                if strong:
                    lines.append(f"  🟢 强共识 {len(strong)}只 | 早盘双源同时推荐 → 复盘看实际方向是否应验")
                if conflict:
                    lines.append(f"  🔴 分歧 {len(conflict)}只 | QTS看多但公众号看空（或反之）→ 谁对？更新权重")
                if summary.get("dual_source", 0) == 0:
                    lines.append(f"  🟡 今日无双源重叠信号（QTS回测池 vs 公众号推荐池无交集）")
                lines.append(f"  __完整对比 → data/signal_consensus.json__")
        except Exception:
            pass

    # QTS 市场状态（收盘复核）
    regime_file = _Path(_PROJECT_ROOT / "data" / "qts_regime.json")
    if regime_file.exists():
        try:
            regime_data = json.loads(regime_file.read_text(encoding="utf-8"))
            pos_mult = regime_data.get("position_multiplier", 0.5)
            regime_label = regime_data.get("regime_label", "未知")
            lines.append(f"\n**📈 QTS 市场状态（收盘复核）：**{regime_label} | 仓位系数 {pos_mult:.1f}x")
        except Exception:
            pass

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

    # 四、早报推荐回溯（按公众号维度，早盘推荐 × 收盘实际）
    lines.append("\n五、早报推荐回溯（按公众号维度，早盘推荐 × 收盘实际）")
    articles = load_today_articles()
    articles_stocks = []
    for art in articles:
        content = art.get("content", "")
        title   = art.get("title", "")
        account = art.get("account", "")
        stocks = extract_article_stocks(title, content, account)
        if stocks:
            signals = [{"code": s["code"], "name": s["name"],
                        "signal": "neutral", "confidence": 0, "reason": "待Agent分析"}
                       for s in stocks]
            articles_stocks.append({"title": title, "account": account, "signals": signals})

    # 按股票聚合 + 按公众号聚合（双重维度）
    stock_stats = {}
    account_stats = {}  # {account: {correct, total, stocks: [(code, name, result)]}}
    for art in articles_stocks:
        account = art["account"]
        for sig in art["signals"]:
            code = sig["code"]
            name = sig["name"]
            if code not in stock_stats:
                stock_stats[code] = {"name": name, "bullish": 0, "bearish": 0,
                                     "accounts": set(), "signals": []}
            if sig["signal"] == "bullish":
                stock_stats[code]["bullish"] += 1
            elif sig["signal"] == "bearish":
                stock_stats[code]["bearish"] += 1
            stock_stats[code]["accounts"].add(account)
            stock_stats[code]["signals"].append((account, sig))
            if account not in account_stats:
                account_stats[account] = {"correct": 0, "total": 0, "stocks": []}

    correct = 0
    total_signal = 0
    matched_stocks = []  # [(account, code, name, is_correct, change_pct, signal_str)]

    if stock_stats:
        for code, stat in stock_stats.items():
            name     = stat["name"]
            bullish  = stat["bullish"]
            bearish  = stat["bearish"]
            kline    = fetch_today_kline(code)

            if kline and (bullish > 0 or bearish > 0):
                total_signal += 1
                actual_up = kline["close"] >= kline["open"]
                suggested_up = bullish > bearish
                is_correct = (suggested_up and actual_up) or (not suggested_up and not actual_up)
                if is_correct:
                    correct += 1
                status_icon = "✅" if is_correct else "❌"
                signal_str = "看多" if bullish > bearish else ("看空" if bearish > bullish else "中性")
                actual_str = "涨" if actual_up else "跌"

                # 记录到每个相关公众号
                for acc in stat["accounts"]:
                    if acc in account_stats:
                        account_stats[acc]["total"] += 1
                        if is_correct:
                            account_stats[acc]["correct"] += 1
                        account_stats[acc]["stocks"].append(
                            (code, name, is_correct, kline["change"], signal_str)
                        )
                matched_stocks.append(
                    (",".join(sorted(stat["accounts"])), code, name,
                     is_correct, kline["change"], signal_str, actual_str)
                )

        # 按公众号回溯（权重对比）
        if account_stats:
            lines.append("\n📊 按公众号统计（今日命中 vs 历史权重）：")
            # 按命中数排序
            sorted_accounts = sorted(account_stats.items(),
                                     key=lambda x: -(x[1]["correct"] / max(x[1]["total"], 1)))
            for acc, a_stat in sorted_accounts[:8]:
                if a_stat["total"] == 0:
                    continue
                acc_hr = a_stat["correct"] / a_stat["total"] * 100
                # 查历史权重
                hist_wr = None
                for r in sw.get("ranking", []):
                    if r.get("account") == acc:
                        hist_wr = r.get("win_rate")
                        break
                wr_note = ""
                if hist_wr is not None:
                    diff = acc_hr - hist_wr
                    wr_note = f"（历史 {hist_wr}%，{'↑超预期' if diff > 10 else '↓低于预期' if diff < -10 else '≈吻合'}）"
                icon = "⭐" if acc_hr >= 60 else ("✅" if acc_hr >= 40 else "⚠️")
                lines.append(
                    f"  {icon} {acc}：{a_stat['correct']}/{a_stat['total']} = {acc_hr:.0f}%{wr_note}"
                )
            lines.append("")

        # 逐票明细
        if matched_stocks:
            lines.append("📋 逐票明细：")
            for (accs, code, name, is_correct, change, signal_str, actual_str) in matched_stocks:
                icon = "✅" if is_correct else "❌"
                lines.append(f"  {icon} [{accs}] {name}({code}) → {signal_str} 实际{actual_str} {change:+.2f}%")

        if total_signal > 0:
            acc = correct / total_signal * 100
            lines.append(f"\n  今日综合准确率：{correct}/{total_signal} = {acc:.0f}%")
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

    # 六、策略优化建议 + AI复盘
    lines.append("\n七、策略优化建议")

    # QTS AI 每日复盘（引用 AI 对昨日操作的专业分析）
    ai_review_file = _Path(_PROJECT_ROOT / "data" / "qts_ai_review.json")
    ai_review_text = ""
    if ai_review_file.exists():
        try:
            ai_data = json.loads(ai_review_file.read_text(encoding="utf-8"))
            ai_content = ai_data.get("content", "")
            if ai_content:
                # 截取核心结论（取前500字）
                ai_review_text = ai_content[:500]
                lines.append(f"\n  🤖 **QTS AI 复盘结论：**")
                for review_line in ai_review_text.split("\n")[:8]:
                    review_line = review_line.strip()
                    if review_line and len(review_line) > 5:
                        lines.append(f"  {review_line}")
        except Exception:
            pass

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
            non_empty = [line_text.strip() for line_text in content.split("\n") if line_text.strip()][:3]
            for line_text in non_empty:
                lines.append(f"    {line_text}")

    lines.append("\n" + "=" * 40)
    lines.append("📝 明日操作计划：结合今日复盘结果，明日早报将更新建议")
    lines.append("💡 每周日晚报后将生成本周策略迭代总结")

    report = "\n".join(lines)

    # 保存晚报
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(os.path.join(REPORT_DIR, f"{date_str}_evening.txt"), "w", encoding="utf-8") as f:
        f.write(report)

    return report

