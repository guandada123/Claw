#!/usr/bin/env python3
"""
AI 成本监控脚本 — 每日预算检查

用法:
    python scripts/cost_monitor.py daily
    python scripts/cost_monitor.py check_budget_status

输出: 结构化的成本报告 JSON/文本
"""

import json
import os
import subprocess
import sys
from datetime import UTC, date, datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data"
REPORTS_DIR = PROJECT_DIR / "reports"

# 预算常量
BUDGET_WARNING = 280  # ¥ 警戒线
BUDGET_LOCK = 350     # ¥ 锁定线
MONTHLY_BUDGET = 500  # ¥ 月度预算

# 模型参考单价（每百万 Token，¥）
PRICE_PER_M_TOKENS = {
    "deepseek-v4-flash": {"input": 0.5, "output": 2.0, "cache_hit": 0.05},
    "deepseek-v4-pro": {"input": 2.0, "output": 8.0, "cache_hit": 0.1},
}

# 各自动化平均每次运行估计消耗（¥）
EST_COST_PER_RUN = {
    "flash": 0.02,   # Flash 模型每次约 ¥0.02（短任务）
    "pro": 0.10,     # Pro 模型每次约 ¥0.10（深度分析）
}


def load_json(path: Path):
    """安全加载JSON文件"""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def count_today_runs() -> tuple:
    """统计今天各模型运行次数"""
    # 读取自动化健康数据
    health = load_json(DATA_DIR / "automation_health.json")
    if not health:
        return 0, 0, "N/A"

    today_str = date.today().isoformat()
    flash_count = 0
    pro_count = 0
    total_estimated_cost = 0.0

    for category, items in health.get("by_category", {}).items():
        for item in items:
            if item.get("status") != "ACTIVE":
                continue

            run_count = item.get("run_count_24h", 0)
            name = item.get("name", "")

            # 根据名称判断模型类型
            if "Pro" in name or "深度" in name or "深研" in name:
                pro_count += run_count
                total_estimated_cost += run_count * EST_COST_PER_RUN["pro"]
            else:
                flash_count += run_count
                total_estimated_cost += run_count * EST_COST_PER_RUN["flash"]

    return flash_count, pro_count, total_estimated_cost


def estimate_monthly_cost() -> dict:
    """基于当前数据估算月成本"""
    health = load_json(DATA_DIR / "automation_health.json")
    if not health:
        return {"error": "无自动化数据", "estimated_monthly": "N/A"}

    total_runs_24h = 0
    for category, items in health.get("by_category", {}).items():
        for item in items:
            if item.get("status") == "ACTIVE":
                total_runs_24h += item.get("run_count_24h", 0)

    daily_cost_est = total_runs_24h * 0.02  # 平均每次 ¥0.02
    monthly_est = daily_cost_est * 30
    monthly_savings = 0.0

    # 读取 Prompt Cache 报告
    cache_report = load_json(DATA_DIR / "prompt_cache_report.json")
    if cache_report and isinstance(cache_report, list) and len(cache_report) > 0:
        estimated_savings = cache_report[0].get("estimated_monthly_savings", 0)
        monthly_savings = estimated_savings

    return {
        "daily_runs_24h": total_runs_24h,
        "daily_cost_est": round(daily_cost_est, 2),
        "monthly_cost_est": round(monthly_est, 2),
        "monthly_savings_cache": round(monthly_savings, 2),
        "net_monthly_cost": round(monthly_est - monthly_savings, 2),
    }


def check_deepseek_balance() -> dict:
    """尝试获取 DeepSeek 余额"""
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        return {"available": False, "balance": "N/A", "note": "未配置 DEEPSEEK_API_KEY"}

    try:
        import urllib.request
        req = urllib.request.Request(
            "https://api.deepseek.com/user/balance",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            balance = data.get("balance", {})
            total_balance = balance.get("total_balance", "N/A")
            return {
                "available": True,
                "balance": total_balance,
                "currency": balance.get("currency", "¥"),
            }
    except Exception as e:
        return {"available": False, "balance": "N/A", "note": f"查询失败: {e}"}


def check_budget_status(accumulated_cost: float) -> dict:
    """检查预算状态"""
    if accumulated_cost >= BUDGET_LOCK:
        return {
            "level": "⛔ LOCKED",
            "message": f"已触发 Flash 锁定！累计¥{accumulated_cost:.2f} ≥ ¥{BUDGET_LOCK}",
            "color": "red",
        }
    elif accumulated_cost >= BUDGET_WARNING:
        return {
            "level": "⚠️ WARNING",
            "message": f"接近锁定线！累计¥{accumulated_cost:.2f} ≥ ¥{BUDGET_WARNING}（警告线）",
            "color": "yellow",
        }
    else:
        remaining = BUDGET_WARNING - accumulated_cost
        return {
            "level": "🟢 NORMAL",
            "message": f"预算正常。距警告线还有 ¥{remaining:.2f}",
            "color": "green",
        }


def generate_daily_report() -> str:
    """生成日成本报告"""
    today = date.today().isoformat()
    flash_count, pro_count, daily_cost = count_today_runs()

    budget_used = 0.0
    # 尝试读取历史累计
    cost_history = load_json(DATA_DIR / "cost_history.json")
    if cost_history and isinstance(cost_history, dict):
        budget_used = cost_history.get("accumulated_cost", 0.0)

    # 如果没有历史数据，用当月以来估算天数
    if budget_used == 0.0:
        day_of_month = date.today().day
        budget_used = daily_cost * day_of_month

    monthly = estimate_monthly_cost()
    budget_status = check_budget_status(budget_used)
    deepseek = check_deepseek_balance()

    # 构建报告
    lines = []
    lines.append(f"📅 日期：{today}")
    lines.append(f"⏰ 时间：{datetime.now(UTC).astimezone().strftime('%H:%M:%S')}")
    lines.append("")

    lines.append("━━━ 运行统计 ━━━")
    lines.append(f"Flash 模型运行：{flash_count} 次")
    lines.append(f"Pro 模型运行：{pro_count} 次")
    lines.append(f"24h 总运行：{flash_count + pro_count} 次")
    lines.append(f"本日预估成本：¥{daily_cost:.2f}")
    lines.append("")

    lines.append("━━━ 月成本估算 ━━━")
    lines.append(f"日运行均值：{monthly['daily_runs_24h']} 次/天")
    lines.append(f"月预估成本：¥{monthly['monthly_cost_est']:.2f}")
    lines.append(f"Prompt Cache 月省：¥{monthly['monthly_savings_cache']:.2f}")
    lines.append(f"净月成本：¥{monthly['net_monthly_cost']:.2f}")
    lines.append(f"月预算：¥{MONTHLY_BUDGET}")
    lines.append("")

    lines.append("━━━ 预算状态 ━━━")
    lines.append(f"累计成本：¥{budget_used:.2f}")
    lines.append(f"预算状态：{budget_status['level']}")
    lines.append(f"{budget_status['message']}")
    lines.append("")

    lines.append("━━━ DeepSeek 余额 ━━━")
    if deepseek["available"]:
        lines.append(f"余额：¥{deepseek['balance']}")
    else:
        lines.append(f"状态：{deepseek.get('note', '未查')}")
    lines.append("")

    lines.append("━━━ 自动化健康 ━━━")
    health = load_json(DATA_DIR / "automation_health.json")
    if health:
        total = health.get("total_count", 0)
        healthy = health.get("healthy_count", 0)
        warning = health.get("warning_count", 0)
        critical = health.get("critical_count", 0)
        lines.append(f"总数：{total} | 🟢 {healthy} | 🟡 {warning} | 🔴 {critical}")
    lines.append("")

    # 保存报告
    report = {
        "date": today,
        "flash_runs": flash_count,
        "pro_runs": pro_count,
        "daily_cost": round(daily_cost, 2),
        "monthly_est": monthly,
        "budget_status": budget_status,
        "deepseek": deepseek,
    }
    report_file = REPORTS_DIR / f"cost_report_{today}.json"
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python cost_monitor.py <daily|check_budget_status>")
        sys.exit(0)

    command = sys.argv[1]

    if command == "daily":
        report = generate_daily_report()
        print(report)

        # 同时输出 JSON 供其他工具解析
        print("\n---JSON---")
        today = date.today().isoformat()
        report_file = REPORTS_DIR / f"cost_report_{today}.json"
        if report_file.exists():
            print(report_file.read_text(encoding="utf-8"))

    elif command == "check_budget_status":
        # 读取最新报告
        today = date.today().isoformat()
        report_file = REPORTS_DIR / f"cost_report_{today}.json"
        data = load_json(report_file)
        budget_used = data.get("budget_used", 0) if data else 0
        status = check_budget_status(budget_used)
        print(f"累计成本: ¥{budget_used:.2f}")
        print(f"状态: {status['level']}")
        print(f"消息: {status['message']}")
