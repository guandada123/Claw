#!/usr/bin/env python3
"""
cost_monitor.py — 成本监控报告生成器（供 WorkBuddy 自动化调用）

功能：
  - 生成每日/每月成本报告
  - 输出结构化内容（直接可推送飞书/微信）
  - 带预算警告

用法：
  python3 cost_monitor.py daily    → 今日成本报告
  python3 cost_monitor.py monthly  → 本月成本报告
  python3 cost_monitor.py summary  → 快速预算摘要（推荐用于每日监控）

版本: v1.0 | 2026-06-14
"""

import sys
import subprocess
from datetime import datetime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent.resolve()


def run_cost_tracker(cmd: str) -> str:
    """执行 cost_tracker.py 并捕获输出"""
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "cost_tracker.py")] + cmd.split(),
        capture_output=True, text=True, timeout=30
    )
    return result.stdout


def run_budget_guard(cmd: str) -> str:
    """执行 budget_guard.py 并捕获输出"""
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "budget_guard.py"), cmd],
        capture_output=True, text=True, timeout=30
    )
    return result.stdout


def daily_report() -> str:
    """今日成本报告 + 预算状态 + 估算成本"""
    cost_output = run_cost_tracker("daily")
    budget_output = run_budget_guard("status")
    estimate_output = run_cost_tracker("estimate_today Claw")

    return f"""💰 AI成本监控日报 ({datetime.now().strftime('%m/%d')})
━━━━━━━━━━━━━
📊 实际调用记录
{cost_output.strip()}

📊 今日估算自动化成本
{estimate_output.strip()}

📊 预算状态
{budget_output.strip()}
━━━━━━━━━━━━━
提示：¥350触发Flash锁定 | ¥400月硬上限"""


def monthly_report() -> str:
    """本月成本汇总"""
    output = run_cost_tracker("monthly")
    return f"""📅 AI成本月度汇总 ({(__import__('datetime').datetime.now().strftime('%Y-%m'))})
━━━━━━━━━━━━━
{output.strip()}
━━━━━━━━━━━━━
预算：¥400/月"""


def generate_dashboard(output: str = None) -> str:
    """生成可视化仪表盘 HTML"""
    args = [sys.executable, str(SCRIPTS_DIR / "cost_dashboard.py")]
    if output:
        args.append(output)
    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    if result.returncode == 0:
        return f"✅ 仪表盘已生成\n{result.stdout.strip()}"
    return f"❌ 生成失败\n{result.stderr.strip()}"


def quick_summary() -> str:
    """快速预算摘要（一行）"""
    budget = run_budget_guard("status")
    # 提取关键行
    lines = budget.strip().split("\n")
    key_lines = [l for l in lines if "本月预算" in l or "已用比例" in l or "剩余预算" in l or "当前层级" in l]
    return "💰 预算速报\n" + "\n".join(key_lines)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "daily"

    if cmd == "daily":
        print(daily_report())
    elif cmd == "monthly":
        print(monthly_report())
    elif cmd == "summary":
        print(quick_summary())
    elif cmd == "dashboard":
        output = sys.argv[2] if len(sys.argv) > 2 else None
        print(generate_dashboard(output))
    else:
        print(f"用法: python3 cost_monitor.py [daily|monthly|summary|dashboard [输出路径]]")
