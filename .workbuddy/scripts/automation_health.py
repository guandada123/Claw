#!/usr/bin/env python3
"""
自动化健康检查脚本
扫描所有自动化任务，检查运行状态、产出时间、异常情况
输出: JSON 摘要
"""

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DB_PATH = Path.home() / ".workbuddy" / "workbuddy.db"
DATA_DIR = Path(__file__).parent.parent / "data"


def get_automations() -> list:
    """从 SQLite 读取所有活跃自动化"""
    if not DB_PATH.exists():
        print(f"DB not found: {DB_PATH}", file=sys.stderr)
        return []

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # 获取所有自动化
    cur.execute("""
        SELECT a.id, a.name, a.status, a.schedule_type, a.rrule,
               a.last_run_at, a.next_run_at, a.model_id, a.model_is_thinking
        FROM automations a
        WHERE a.deleted_at IS NULL
        ORDER BY a.name
    """)
    automations = [dict(r) for r in cur.fetchall()]

    for auto in automations:
        # 最近运行记录
        cur.execute(
            """
            SELECT thread_id, status, created_at
            FROM automation_runs
            WHERE automation_id = ?
            ORDER BY created_at DESC LIMIT 5
        """,
            (auto["id"],),
        )
        auto["recent_runs"] = [dict(r) for r in cur.fetchall()]

        # 运行时状态
        cur.execute(
            """
            SELECT last_error, running, running_started_at
            FROM automation_runtime_state
            WHERE automation_id = ?
        """,
            (auto["id"],),
        )
        state = cur.fetchone()
        if state:
            auto["last_error"] = state["last_error"]
            auto["running"] = bool(state["running"])

    conn.close()
    return automations


def categorize(automations: list) -> dict:
    """按业务分类"""
    cats = {
        "投顾操盘": [],
        "炒股助理": [],
        "美股监控": [],
        "系统维护": [],
        "桥接监控": [],
        "其他": [],
    }

    for a in automations:
        name = a.get("name", "")
        if any(k in name for k in ["投顾", "模拟", "选股"]):
            cats["投顾操盘"].append(a)
        elif any(
            k in name for k in ["助理", "盘前", "收盘", "持仓", "财报", "早报", "公众号", "投资"]
        ):
            cats["炒股助理"].append(a)
        elif "美股" in name:
            cats["美股监控"].append(a)
        elif any(k in name for k in ["健康", "清理", "索引", "审计", "记忆"]):
            cats["系统维护"].append(a)
        elif any(k in name.lower() for k in ["marvis", "bridge", "桥接"]):
            cats["桥接监控"].append(a)
        else:
            cats["其他"].append(a)

    return cats


def _parse_unix(ts) -> datetime | None:
    """解析 Unix 时间戳（毫秒）"""
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts) / 1000)
    except Exception:
        return None


def check_health(auto: dict) -> dict:
    """检查单个自动化健康状态"""
    name = auto.get("name", "Unknown")
    status = auto.get("status", "UNKNOWN")
    runs = auto.get("recent_runs", [])
    last_run_ts = auto.get("last_run_at", 0)
    last_error = auto.get("last_error", "")
    running = auto.get("running", False)

    health = "🟢"
    issues = []

    # 状态检查
    if status == "PAUSED":
        health = "🟡"
        issues.append("已暂停")
    elif status != "ACTIVE":
        health = "🔴"
        issues.append(f"状态异常: {status}")

    # 运行记录检查
    if runs:
        last_status = runs[0].get("status", "")
        if last_status == "failed":
            health = "🔴"
            issues.append("最后一次运行失败")
        elif last_status == "timeout":
            health = "🟡"
            issues.append("超时")

    # 静默失败检测（根据调度频率调整阈值）
    last_run = _parse_unix(last_run_ts)
    if last_run:
        hours_ago = (datetime.now() - last_run).total_seconds() / 3600
        schedule = auto.get("schedule_type", "recurring")
        rrule = auto.get("rrule", "") or ""

        # 根据调度频率设定不同阈值
        if "WEEKLY" in rrule or "每周" in name:
            threshold = 192  # 8天
        elif schedule == "once":
            threshold = 99999  # 一次性任务不报
        else:
            threshold = 48  # 默认48h

        if hours_ago > threshold:
            health = "🔴" if health == "🟢" else health
            issues.append(f"{int(hours_ago)}h未运行")
        elif hours_ago > threshold * 0.6:
            health = "🟡" if health == "🟢" else health
            issues.append(f"{int(hours_ago)}h未运行")

    # 运行中
    if running:
        health = "🔵" if health == "🟢" else health
        issues.append("运行中")

    # 错误信息
    if last_error:
        health = "🟡" if health == "🟢" else health
        issues.append(f"历史错误: {last_error[:40]}")

    # 计算24h运行次数
    run_24h = 0
    for r in runs:
        rt = _parse_unix(r.get("created_at"))
        if rt and (datetime.now() - rt).total_seconds() < 86400:
            run_24h += 1

    return {
        "name": name,
        "status": status,
        "health": health,
        "last_run": last_run.isoformat() if last_run else None,
        "issues": issues,
        "run_count_24h": run_24h,
        "running": running,
    }


def generate_report(summary: dict) -> str:
    """生成汇总报告文本"""
    lines = []
    lines.append(f"⚙️ 自动化健康 | {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("━━━━━━━━━━━━━━━━━━━━")

    total = summary["total_count"]
    h = summary["healthy_count"]
    w = summary["warning_count"]
    c = summary["critical_count"]

    lines.append(f"总计 {total} | 🟢{h} 🟡{w} 🔴{c}")
    lines.append("")

    for cat, items in summary["by_category"].items():
        if not items:
            continue
        cat_issues = [i for i in items if i["health"] in ("🟡", "🔴")]
        lines.append(f"📂 {cat} ({len(items)}个, {len(cat_issues)}异常)")
        for item in items:
            if item["health"] in ("🟡", "🔴"):
                lines.append(f"  {item['health']} {item['name']}: {', '.join(item['issues'])}")
        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def main():
    print(f"[{datetime.now()}] 自动化健康扫描...")

    automations = get_automations()
    if not automations:
        print("无法读取自动化数据", file=sys.stderr)
        sys.exit(1)

    by_cat = categorize(automations)

    healthy = warning = critical = 0
    cat_results = {}

    for cat, items in by_cat.items():
        checked = []
        for auto in items:
            h = check_health(auto)
            checked.append(h)
            if h["health"] == "🟢":
                healthy += 1
            elif h["health"] == "🟡":
                warning += 1
            elif h["health"] == "🔴":
                critical += 1
            else:
                healthy += 1  # 🔵 running = OK
        cat_results[cat] = checked

    summary = {
        "total_count": len(automations),
        "healthy_count": healthy,
        "warning_count": warning,
        "critical_count": critical,
        "by_category": cat_results,
        "generated_at": datetime.now().isoformat(),
    }

    # 写入 JSON
    output_path = DATA_DIR / "automation_health.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

    # 生成报告
    report = generate_report(summary)
    print(report)
    print(f"\n详细数据 → {output_path}")

    return 0 if critical == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
