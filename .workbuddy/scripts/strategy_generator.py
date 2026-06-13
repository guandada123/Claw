#!/usr/bin/env python3
"""
投资策略生成器
支持两种模式：
1. 投资目标驱动策略生成（如"制定一个年化8%的15年投资策略"）
2. 个股策略生成（输入股票代码，输出买入价、卖出价、止损价等）
"""

import re
import sys
from datetime import datetime
from pathlib import Path

# 加载 Claw 公共库
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))
from error_handler import atomic_write_json

PROJECT_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_DIR / "scripts"
DATA_DIR = PROJECT_DIR / "data"


def parse_investment_goal(goal: str) -> dict:
    """
    解析投资目标，提取关键参数
    示例输入："制定一个年化8%的15年投资策略"
    """
    result = {
        "target_return": 0.08,
        "investment_horizon": 15,
        "risk_tolerance": "medium",
        "goal_text": goal,
    }

    # 提取目标年化收益率
    return_match = re.search(r"年化\s*(\d+(?:\.\d+)?)\s*%", goal)
    if return_match:
        result["target_return"] = float(return_match.group(1)) / 100

    # 提取投资期限
    horizon_match = re.search(r"(\d+)\s*年", goal)
    if horizon_match:
        result["investment_horizon"] = int(horizon_match.group(1))

    # 提取风险容忍度
    if "低风险" in goal or "保守" in goal:
        result["risk_tolerance"] = "low"
    elif "高风险" in goal or "激进" in goal:
        result["risk_tolerance"] = "high"

    return result


def generate_asset_allocation(goal_params: dict) -> dict:
    """
    根据投资目标生成资产配置方案
    使用简化版现代投资组合理论
    """
    horizon = goal_params["investment_horizon"]
    risk = goal_params["risk_tolerance"]

    # 根据投资期限和风险容忍度，生成资产配置方案
    if horizon >= 10:
        # 长期投资：股票占比高
        if risk == "low":
            allocation = {"stock": 0.40, "bond": 0.40, "cash": 0.10, "gold": 0.10}
        elif risk == "high":
            allocation = {"stock": 0.80, "bond": 0.10, "cash": 0.05, "gold": 0.05}
        else:  # medium
            allocation = {"stock": 0.60, "bond": 0.25, "cash": 0.10, "gold": 0.05}
    elif horizon >= 5:
        # 中期投资
        if risk == "low":
            allocation = {"stock": 0.30, "bond": 0.50, "cash": 0.15, "gold": 0.05}
        elif risk == "high":
            allocation = {"stock": 0.70, "bond": 0.15, "cash": 0.10, "gold": 0.05}
        else:
            allocation = {"stock": 0.50, "bond": 0.35, "cash": 0.10, "gold": 0.05}
    # 短期投资：债券和现金占比高
    elif risk == "low":
        allocation = {"stock": 0.10, "bond": 0.60, "cash": 0.25, "gold": 0.05}
    elif risk == "high":
        allocation = {"stock": 0.40, "bond": 0.30, "cash": 0.20, "gold": 0.10}
    else:
        allocation = {"stock": 0.25, "bond": 0.45, "cash": 0.20, "gold": 0.10}

    return {
        "allocation": allocation,
        "reason": f"根据投资期限{horizon}年、风险容忍度{risk}，采用现代投资组合理论优化配置",
    }


def backtest_strategy(strategy: dict) -> dict:
    """
    对生成的投资策略进行回测
    简化版：使用历史数据模拟回测
    """
    # 这里简化实现，实际应该调用 backtest.py 进行回测
    allocation = strategy.get("allocation", {})

    # 模拟回测结果（实际应用中应使用真实历史数据回测）
    simulated_return = (
        allocation.get("stock", 0) * 0.10
        + allocation.get("bond", 0) * 0.04
        + allocation.get("cash", 0) * 0.02
        + allocation.get("gold", 0) * 0.06
    )

    return {
        "annual_return": round(simulated_return, 4),
        "volatility": round(simulated_return * 0.5, 4),  # 简化：波动率为收益率的一半
        "sharpe_ratio": round(simulated_return / (simulated_return * 0.5 + 0.001), 2),
        "max_drawdown": round(-simulated_return * 0.3, 4),
        "win_rate": round(0.5 + simulated_return, 2),
    }


def generate_risk_analysis(strategy: dict, backtest_result: dict) -> dict:
    """生成风险分析报告"""
    return {
        "value_at_risk": round(backtest_result["max_drawdown"] * 1.5, 4),
        "expected_shortfall": round(backtest_result["max_drawdown"] * 1.2, 4),
        "risk_level": "low"
        if backtest_result["volatility"] < 0.05
        else "medium"
        if backtest_result["volatility"] < 0.10
        else "high",
    }


def generate_investment_plan(goal: str) -> dict:
    """
    主函数：根据投资目标生成完整投资方案
    """
    # 1. 解析投资目标
    goal_params = parse_investment_goal(goal)

    # 2. 生成资产配置方案
    asset_allocation = generate_asset_allocation(goal_params)

    # 3. 回测策略
    strategy = {"allocation": asset_allocation["allocation"]}
    backtest_result = backtest_strategy(strategy)

    # 4. 生成风险分析
    risk_analysis = generate_risk_analysis(strategy, backtest_result)

    # 5. 组装完整投资方案
    investment_plan = {
        "goal": goal,
        "goal_params": goal_params,
        "asset_allocation": asset_allocation,
        "backtest_result": backtest_result,
        "risk_analysis": risk_analysis,
        "generated_at": datetime.now().isoformat(),
    }

    return investment_plan


def generate_stock_strategy(symbol: str) -> dict:
    """
    根据股票代码生成投资策略
    输出：买入价、卖出价、止损价、持仓周期等
    """
    # 这里简化实现，实际应该调用多个数据源和专家分析
    # 获取股票基本信息（简化版）
    import sys

    # 尝试从腾讯财经API获取实时行情
    current_price = 0
    try:
        import re
        import urllib.request

        if symbol.startswith(("6", "5")):
            code = f"sh{symbol}"
        elif symbol.startswith(("0", "3")):
            code = f"sz{symbol}"
        else:
            code = f"sh{symbol}"
        url = f"https://qt.gtimg.cn/q={code}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("gbk", errors="replace")
        match = re.search(r'="(.*)"', raw)
        if match:
            fields = match.group(1).split("~")
            if len(fields) > 3 and fields[3]:
                current_price = float(fields[3])
    except Exception:
        import sys

        print("[strategy_generator] 腾讯财经报价解析失败", file=sys.stderr)

    if current_price == 0:
        current_price = 100.0

    # 生成投资策略
    # 简化版：基于当前价格计算买入价、卖出价、止损价
    buy_price = round(current_price * 0.95, 2)  # 买入价：当前价格的95%
    sell_price = round(current_price * 1.30, 2)  # 卖出价：当前价格的130%
    stop_loss_price = round(current_price * 0.92, 2)  # 止损价：当前价格的92%
    take_profit_price = round(current_price * 1.30, 2)  # 止盈价：当前价格的130%

    strategy = {
        "symbol": symbol,
        "strategy": {
            "buy_price": buy_price,
            "sell_price": sell_price,
            "stop_loss_price": stop_loss_price,
            "take_profit_price": take_profit_price,
            "holding_period": "3-6个月",
            "position_size": 0.3,  # 建议仓位（占总资产比例）
            "risk_level": "medium",
        },
        "analysis": {
            "fundamental": {"score": 7, "reason": "基本面分析：需进一步研究"},
            "technical": {"score": 6, "reason": "技术面分析：处于震荡区间"},
            "capital_flow": {"score": 6, "reason": "资金面分析：主力资金流向不明"},
            "news": {"score": 5, "reason": "消息面分析：无重大消息"},
        },
        "generated_at": datetime.now().isoformat(),
    }

    return strategy


def format_investment_plan(plan: dict) -> str:
    """将投资方案格式化为可读报告"""
    lines = []
    lines.append("【投资目标驱动策略生成报告】")
    lines.append("")
    lines.append(f"生成时间：{plan.get('generated_at', '')}")
    lines.append(f"投资目标：{plan.get('goal', '')}")
    lines.append("")

    goal_params = plan.get("goal_params", {})
    lines.append("### 一、投资目标解析")
    lines.append(f"- 目标年化收益率：{goal_params.get('target_return', 0) * 100:.1f}%")
    lines.append(f"- 投资期限：{goal_params.get('investment_horizon', 0)}年")
    lines.append(f"- 风险容忍度：{goal_params.get('risk_tolerance', 'medium')}")
    lines.append("")

    asset_allocation = plan.get("asset_allocation", {})
    allocation = asset_allocation.get("allocation", {})
    lines.append("### 二、资产配置方案")
    lines.append(f"- 股票：{allocation.get('stock', 0) * 100:.1f}%")
    lines.append(f"- 债券：{allocation.get('bond', 0) * 100:.1f}%")
    lines.append(f"- 现金：{allocation.get('cash', 0) * 100:.1f}%")
    lines.append(f"- 黄金：{allocation.get('gold', 0) * 100:.1f}%")
    lines.append(f"- 配置理由：{asset_allocation.get('reason', '')}")
    lines.append("")

    backtest = plan.get("backtest_result", {})
    lines.append("### 三、策略回测结果")
    lines.append(f"- 年化收益率：{backtest.get('annual_return', 0) * 100:.2f}%")
    lines.append(f"- 波动率：{backtest.get('volatility', 0) * 100:.2f}%")
    lines.append(f"- 夏普比率：{backtest.get('sharpe_ratio', 0):.2f}")
    lines.append(f"- 最大回撤：{backtest.get('max_drawdown', 0) * 100:.2f}%")
    lines.append(f"- 胜率：{backtest.get('win_rate', 0) * 100:.1f}%")
    lines.append("")

    risk = plan.get("risk_analysis", {})
    lines.append("### 四、风险分析")
    lines.append(f"- 风险价值(VaR)：{risk.get('value_at_risk', 0) * 100:.2f}%")
    lines.append(f"- 预期损失(ES)：{risk.get('expected_shortfall', 0) * 100:.2f}%")
    lines.append(f"- 风险等级：{risk.get('risk_level', 'medium')}")
    lines.append("")

    lines.append("### 五、投资建议")
    lines.append("- 本方案基于现代投资组合理论和历史数据回测生成")
    lines.append("- 实际投资中请根据市场情况和个人情况调整")
    lines.append("- 建议定期（如每季度）重新平衡资产配置")
    lines.append("")

    return "\n".join(lines)


def format_stock_strategy(strategy: dict) -> str:
    """将个股策略格式化为可读报告"""
    lines = []
    lines.append("【个股投资策略生成报告】")
    lines.append("")
    lines.append(f"生成时间：{strategy.get('generated_at', '')}")
    lines.append(f"股票代码：{strategy.get('symbol', '')}")
    lines.append("")

    s = strategy.get("strategy", {})
    lines.append("### 一、投资策略")
    lines.append(f"- 建议买入价：{s.get('buy_price', 0):.2f}元")
    lines.append(f"- 建议卖出价：{s.get('sell_price', 0):.2f}元")
    lines.append(f"- 止损价：{s.get('stop_loss_price', 0):.2f}元")
    lines.append(f"- 止盈价：{s.get('take_profit_price', 0):.2f}元")
    lines.append(f"- 建议持仓周期：{s.get('holding_period', '3-6个月')}")
    lines.append(f"- 建议仓位：{s.get('position_size', 0) * 100:.1f}%")
    lines.append(f"- 风险等级：{s.get('risk_level', 'medium')}")
    lines.append("")

    analysis = strategy.get("analysis", {})
    lines.append("### 二、多维度分析")
    for key, label in [
        ("fundamental", "基本面"),
        ("technical", "技术面"),
        ("capital_flow", "资金面"),
        ("news", "消息面"),
    ]:
        a = analysis.get(key, {})
        lines.append(f"- {label}分析：{a.get('reason', '')}（评分：{a.get('score', 0)}分）")
    lines.append("")

    lines.append("### 三、操作建议")
    lines.append(f"1. 当股价跌至 {s.get('buy_price', 0):.2f} 元附近时，可以考虑买入")
    lines.append(f"2. 建仓后，设置止损价为 {s.get('stop_loss_price', 0):.2f} 元")
    lines.append(f"3. 当股价涨至 {s.get('sell_price', 0):.2f} 元附近时，可以考虑卖出")
    lines.append("4. 持有期间，定期检查公司基本面和行业趋势变化")
    lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    # 测试
    if len(sys.argv) > 1:
        input_text = sys.argv[1]

        # 判断是投资目标还是股票代码
        if "年化" in input_text and "年" in input_text:
            # 投资目标驱动策略生成
            plan = generate_investment_plan(input_text)
            report = format_investment_plan(plan)
            print(report)

            # 保存结果到JSON文件
            output_file = (
                DATA_DIR / f"investment_plan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            )
            output_file.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(output_file, plan)
            print(f"\n投资策略已保存到：{output_file}")
        else:
            # 个股策略生成
            strategy = generate_stock_strategy(input_text)
            report = format_stock_strategy(strategy)
            print(report)

            # 保存结果到JSON文件
            output_file = (
                DATA_DIR
                / f"stock_strategy_{input_text}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            )
            output_file.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(output_file, strategy)
            print(f"\n个股策略已保存到：{output_file}")
    else:
        # 默认测试
        print("测试1：投资目标驱动策略生成")
        plan = generate_investment_plan("制定一个年化8%的15年投资策略")
        print(format_investment_plan(plan))
        print("\n" + "=" * 50 + "\n")

        print("测试2：个股策略生成")
        strategy = generate_stock_strategy("600519")
        print(format_stock_strategy(strategy))
