#!/usr/bin/env python3
"""
综合日报生成器 — 整合行情监控 + 模拟交易 + 止损止盈 + 专家分析（自动调用）
由 cron_monitor.py 在收盘后调用，生成完整的 Markdown 日报
"""

import importlib.util
import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_DIR / "scripts"
REPORTS_DIR = PROJECT_DIR / "reports"
DATA_DIR = PROJECT_DIR / "data" / "simulation"

INITIAL_CAPITAL = 30000.0
STOP_LOSS = -0.08
TAKE_PROFIT = 0.30


def today_str():
    return date.today().isoformat()


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run_script(script_name: str, *args) -> dict:
    """运行模拟交易脚本并返回 JSON 结果"""
    script_path = SCRIPTS_DIR / script_name
    cmd = ["python3", str(script_path)] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=str(PROJECT_DIR))
    if result.returncode != 0:
        return {"error": result.stderr.strip() or "脚本执行失败"}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"error": f"JSON 解析失败: {result.stdout[:200]}"}


def generate_comprehensive_report():
    """生成综合日报"""
    d = today_str()
    lines = []

    # ═════════════════════════════════════════════
    # 标题
    # ═════════════════════════════════════════════
    lines.append(f"# 📈【投顾操盘】模拟炒股日报 — {d}")
    lines.append(f"生成时间：{now_str()}")
    lines.append("")

    # ═════════════════════════════════════════════
    # 一、账户总览
    # ═════════════════════════════════════════════
    lines.append("## 一、账户总览")
    lines.append("")

    portfolio_data = run_script("sim_trade.py", "portfolio")
    if "error" not in portfolio_data:
        perf = portfolio_data.get("performance", {})
        total_asset = perf.get("total_asset", 0)
        total_pnl = perf.get("total_pnl", 0)
        total_pnl_pct = perf.get("total_pnl_pct", 0)
        cash = perf.get("cash", 0)
        win_rate = perf.get("win_rate", 0)
        total_trades = perf.get("total_trades", 0)
        realized_pnl = perf.get("realized_pnl", 0)

        emoji = "🔴" if total_pnl >= 0 else "🟢"
        lines.append("| 指标 | 数值 |")
        lines.append("|------|------|")
        lines.append(f"| 总资产 | ¥{total_asset:,.0f} |")
        lines.append(f"| 可用现金 | ¥{cash:,.0f} |")
        lines.append(f"| 累计盈亏 | {emoji} {total_pnl:+,.0f} ({total_pnl_pct:+.2f}%) |")
        lines.append(f"| 已实现盈亏 | ¥{realized_pnl:+,.0f} |")
        lines.append(f"| 胜率 | {win_rate:.1f}% ({total_trades}笔交易) |")
        lines.append("")

        # 仓位分布
        lines.append("### 📂 仓位分布")
        lines.append("")
        positions = portfolio_data.get("positions", [])
        if positions:
            for pos in positions:
                name = pos.get("name", "?")
                code = pos.get("code", "?")
                weight = pos.get("weight_pct", 0)
                pnl = pos.get("unrealized_pnl", 0)
                pnl_pct = pos.get("unrealized_pnl_pct", 0)
                emoji = "🔴" if pnl >= 0 else "🟢"
                bar = "█" * int(weight / 2)
                lines.append(
                    f"- **{name}**({code}): {bar} {weight:.1f}% | {emoji} {pnl:+,.0f} ({pnl_pct:+.2f}%)"
                )
        lines.append("")

    # ═════════════════════════════════════════════
    # 二、持仓明细（含止损止盈状态）
    # ═════════════════════════════════════════════
    lines.append("## 二、持仓明细")
    lines.append("")

    if positions:
        lines.append("| 代码 | 名称 | 持仓 | 成本 | 现价 | 市值 | 盈亏 | 止损状态 | 止盈状态 |")
        lines.append("|------|------|------|------|------|------|------|----------|----------|")

        for pos in positions:
            code = pos.get("code", "?")
            name = pos.get("name", "?")
            shares = pos.get("shares", 0)
            avg_cost = pos.get("avg_cost", 0)
            price = pos.get("current_price", 0)
            mv = pos.get("market_value", 0)
            pnl = pos.get("unrealized_pnl", 0)
            pnl_pct = pos.get("unrealized_pnl_pct", 0)
            sl_status = pos.get("stop_loss_status", "N/A")
            tp_status = pos.get("take_profit_status", "N/A")
            tp_level = pos.get("take_profit_level", 1)

            emoji = "🔴" if pnl >= 0 else "🟢"
            lines.append(
                f"| {code} | {name} | {shares}股 | ¥{avg_cost:.2f} | ¥{price:.2f} | "
                f"¥{mv:,.0f} | {emoji} {pnl:+,.0f}({pnl_pct:+.2f}%) | "
                f"{sl_status} | Lv{tp_level}:{tp_status} |"
            )
        lines.append("")

    # ═════════════════════════════════════════════
    # 三、止损止盈检查
    # ═════════════════════════════════════════════
    lines.append("## 三、智能止损止盈检测")
    lines.append("")

    auto_check = run_script("sim_trade.py", "auto-check")
    if auto_check.get("has_suggestions"):
        lines.append(f"🚨 **发现 {auto_check.get('count', 0)} 条止损止盈建议：**")
        lines.append("")
        for sug in auto_check.get("suggestions", []):
            priority = (
                "🔴"
                if sug.get("priority") == "high"
                else "🟡"
                if sug.get("priority") == "medium"
                else "🔵"
            )
            lines.append(
                f"- {priority} **{sug.get('name')}**({sug.get('code')}): {sug.get('reason')} | 建议卖出{sug.get('shares')}股"
            )
        lines.append("")
        lines.append("> ⚠️ 以上建议仅供参考，不构成投资建议。实盘交易请自行判断。")
    else:
        lines.append("✅ 当前所有持仓均未触发止损止盈条件。")
        lines.append("")

    # ═════════════════════════════════════════════
    # 四、资金流向分析（东方财富数据）
    # ═════════════════════════════════════════════
    lines.append("## 四、资金流向分析")
    lines.append("")
    lines.append("> 数据来源：东方财富资金流向 API")
    lines.append("")
    lines.append("| 代码 | 名称 | 主力净流入(万) | 主力占比 | 信号 |")
    lines.append("|------|------|----------------|----------|------|")

    for pos in positions:
        code = pos.get("code", "")
        name = pos.get("name", "")

        try:
            # 调用 cron_monitor 中的 fetch_money_flow
            sys.path.insert(0, str(SCRIPTS_DIR))
            from cron_monitor import fetch_money_flow

            mf = fetch_money_flow(code)

            if "error" not in mf:
                main_net = mf["main_net"]
                main_pct = mf["main_pct"]
                signal = "💰主力流入" if main_net > 0 else "💸主力流出" if main_net < 0 else "⚖️平衡"
                lines.append(f"| {code} | {name} | {main_net:+.0f} | {main_pct:.1f}% | {signal} |")
            else:
                lines.append(f"| {code} | {name} | 暂无数据 | - | - |")
        except Exception:
            lines.append(f"| {code} | {name} | 获取失败 | - | - |")

    lines.append("")

    # ═════════════════════════════════════════════
    # 五、投资大师专家团分析（自动调用）
    # ═════════════════════════════════════════════
    lines.append("## 五、投资大师专家团分析")
    lines.append("")
    lines.append("> 🤖 已自动调用5位专家对持仓股票进行多维度分析")
    lines.append("> 专家团组成：价值投资专家、技术分析专家、宏观经济专家、资金面专家、市场情绪专家")
    lines.append("")

    # 动态导入 expert_team_analyst 模块
    expert_team_script = SCRIPTS_DIR / "expert_team_analyst.py"
    if expert_team_script.exists():
        try:
            # 动态加载模块
            spec = importlib.util.spec_from_file_location("expert_team_analyst", expert_team_script)
            expert_team = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(expert_team)

            # 对每个持仓股票调用专家团分析
            if positions:
                for pos in positions:
                    code = pos.get("code", "")
                    name = pos.get("name", "")

                    lines.append(f"### {name}({code}) — 专家团分析")
                    lines.append("")

                    try:
                        # 调用专家团分析
                        context = expert_team.analyze_stock(code)
                        summary = context.get("final_summary", {})

                        # 添加各专家结论
                        for conc in summary.get("expert_conclusions", []):
                            lines.append(
                                f"**{conc['expert_name']}**：{conc['conclusion']}（{conc['score']}分）"
                            )
                            lines.append(f"> {conc['reason']}")
                            lines.append("")

                        # 添加综合结论
                        lines.append(f"**综合结论**：{summary.get('final_conclusion', '持有')}")
                        lines.append(
                            f"- 买入信号：{summary.get('buy_signals', 0)}个 | 卖出信号：{summary.get('sell_signals', 0)}个"
                        )
                        lines.append(f"- 综合评分：{summary.get('avg_score', 5)}分（满分10分）")

                        buy_price = summary.get("suggest_buy_price", 0)
                        sell_price = summary.get("suggest_sell_price", 0)
                        stop_loss = summary.get("suggest_stop_loss", 0)

                        if buy_price > 0:
                            lines.append(f"- 建议买入价：{buy_price}元")
                            lines.append(f"- 建议卖出价：{sell_price}元")
                            lines.append(f"- 建议止损价：{stop_loss}元")

                        lines.append("")
                    except Exception as e:
                        lines.append(f"⚠️ 专家团分析失败：{str(e)}")
                        lines.append("")
            else:
                lines.append("当前无持仓，跳过专家团分析。")
                lines.append("")
        except Exception as e:
            lines.append(f"⚠️ 加载专家团分析模块失败：{str(e)}")
            lines.append("")
    else:
        lines.append("⚠️ 专家团分析脚本不存在，跳过自动分析。")
        lines.append("> 如需使用此功能，请确保 `expert_team_analyst.py` 已创建。")
        lines.append("")

    # ═════════════════════════════════════════════
    # 六、量化策略信号（star_signal 引擎）
    # ═════════════════════════════════════════════
    lines.append("## 六、量化策略信号")
    lines.append("")

    try:
        from star_signal_adapter import get_star_signal

        # 为持仓股票获取 star_signal 评分
        signal_lines = []
        signal_lines.append("| 策略 | 股票 | 评分 | 信号等级 | RSI | 量比 | 趋势 |")
        signal_lines.append("|------|------|------|----------|-----|------|------|")

        for pos in positions[:5]:  # Top 5
            code = pos.get("code", "")
            name = pos.get("name", "")
            try:
                sig = get_star_signal(code)
                if "error" not in sig:
                    trend = sig.get("trend", "—")
                    signal_lines.append(
                        f"| ⭐ 五角星信号 | {name} | {sig['score']:.0f}/100 | "
                        f"{sig['strength_name']} | {sig['rsi']:.1f} | {sig['vol_ratio']:.1f}x | {trend} |"
                    )
            except Exception:
                import sys

                print(f"[report] 信号行生成跳过 {code}", file=sys.stderr)

        if len(signal_lines) > 2:
            lines.extend(signal_lines)
            lines.append("> 数据引擎: star_signal.py v2.1 · 5维加权评分 · ATR动态止损")
        else:
            lines.append("> ⚠️ 无持仓数据或信号引擎不可用")
    except ImportError:
        lines.append("> 💡 信号引擎 star_signal.py 未安装 · 运行 `pip install pandas` 后可用")

    lines.append("")

    # ═════════════════════════════════════════════
    # 七、风险提示
    # ═════════════════════════════════════════════
    lines.append("## 七、风险提示")
    lines.append("")

    # 计算风险指标
    if positions:
        max_weight = max((p.get("weight_pct", 0) for p in positions), default=0)
        max_dd_pct = 0  # 需要从历史数据获取

        lines.append("### 仓位风险")
        lines.append(
            f"- 最大单只仓位：{max_weight:.1f}%（{'⚠️ 超过50%警戒线' if max_weight > 50 else '✅ 安全'}）"
        )
        lines.append(
            f"- 持仓数量：{len(positions)}只（{'✅ 符合≤3只约束' if len(positions) <= 3 else '⚠️ 超过3只约束'}）"
        )

        # 最近的止损预警
        sl_risk = [
            p
            for p in positions
            if "触发" in p.get("stop_loss_status", "")
            and "未触发" not in p.get("stop_loss_status", "")
        ]
        if sl_risk:
            lines.append(f"- 止损预警：{len(sl_risk)}只股票触发止损条件")
        else:
            lines.append("- 止损预警：无")

        lines.append("")

    lines.append("### 市场风险")
    lines.append("- 本报告数据源：腾讯财经（行情）+ 东方财富（资金流向）")
    lines.append("- 数据延迟：约3-5秒（盘中），收盘后为静态数据")
    lines.append("- 模拟系统不接入实盘交易，仅供学习和策略测试")
    lines.append("")

    # ═════════════════════════════════════════════
    # 八、复盘笔记
    # ═════════════════════════════════════════════
    lines.append("## 八、复盘笔记")
    lines.append("")
    lines.append("> 📝 记录本日交易决策理由、经验教训和改进点")
    lines.append("")
    lines.append("### 今日操作")
    lines.append("- 买入：___")
    lines.append("- 卖出：___")
    lines.append("- 理由：___")
    lines.append("")
    lines.append("### 持仓评估")
    lines.append("- 表现好的：___")
    lines.append("- 需要关注：___")
    lines.append("")
    lines.append("### 明日计划")
    lines.append("- 关注标的：___")
    lines.append("- 操作预案：___")
    lines.append("")

    # ═════════════════════════════════════════════
    # 尾部
    # ═════════════════════════════════════════════
    lines.append("---")
    lines.append(
        f"📅 报告日期：{d} | 🤖 生成工具：WorkBuddy 模拟炒股系统 | 📊 数据源：腾讯财经 + 东方财富"
    )
    lines.append("")
    lines.append("> ⚠️ 免责声明：本报告仅供学习参考，不构成任何投资建议。股市有风险，投资需谨慎。")

    # 保存报告
    report_text = "\n".join(lines)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_file = REPORTS_DIR / f"daily_comprehensive_{d}.md"
    report_file.write_text(report_text, encoding="utf-8")

    return {
        "ok": True,
        "report_file": str(report_file),
        "date": d,
    }


if __name__ == "__main__":
    result = generate_comprehensive_report()
    if result.get("ok"):
        print(f"✅ 综合日报已生成: {result['report_file']}")
    else:
        print(f"❌ 生成失败: {result}")
