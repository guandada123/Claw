#!/usr/bin/env python3
"""
Wind 万得积分及查询用量报告

每天运行一次，汇总 Wind 今日查询统计和积分余额。
可集成到飞书推送或日志中。

用法:
    python3 wind_quota_report.py                      # 仅输出到 stdout
    python3 wind_quota_report.py --push               # 推送到飞书
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── 查询统计（来自 wind_utils 和 wind_quote） ──


def _get_query_stats() -> dict:
    """从两个计数器收集统计"""
    stats = {"wind_utils": None, "wind_quote": None}
    try:
        sys.path.insert(0, str(PROJECT_ROOT / "src"))
        from claw.feeds.wind_utils import get_query_stats as qs1

        stats["wind_utils"] = qs1()
    except Exception:
        pass
    try:
        sys.path.insert(0, str(PROJECT_ROOT / ".workbuddy" / "lib"))
        from wind_quote import get_query_stats as qs2

        stats["wind_quote"] = qs2()
    except Exception:
        stats["wind_quote"] = None
    return stats


def _check_wind_api_points() -> dict | None:
    """尝试从 Wind 官网获取剩余积分（暂不可靠，仅作预留）"""
    return None


def build_report() -> dict:
    """生成用量报告"""
    now = datetime.now()
    stats = _get_query_stats()

    # wind_utils 和 wind_quote 可能共享计数器
    if stats["wind_utils"] and stats.get("wind_quote"):
        # 共享计数器时只用 wind_utils 的值
        primary = stats["wind_utils"]
        total_used = primary["used"]
        total_limit = primary["limit"]
        source = "wind_utils (shared)"
    elif stats["wind_utils"]:
        primary = stats["wind_utils"]
        total_used = primary["used"]
        total_limit = primary["limit"]
        source = "wind_utils"
    elif stats["wind_quote"]:
        primary = stats["wind_quote"]
        total_used = primary.get("usage", primary.get("used", 0))
        total_limit = primary.get("limit", 100)
        source = "wind_quote"
    else:
        return {"error": "无法获取查询统计", "timestamp": now.isoformat()}

    remaining = total_limit - total_used
    return {
        "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
        "date": now.strftime("%Y-%m-%d"),
        "source": source,
        "quota": {
            "limit": total_limit,
            "used": total_used,
            "remaining": remaining,
            "usage_pct": round(total_used / total_limit * 100, 1) if total_limit else 0,
        },
        "status": "🟢" if remaining > 20 else ("🟡" if remaining > 0 else "🔴"),
    }


def push_to_feishu(report: dict):
    """推送报告到飞书"""
    if "error" in report:
        return
    status_emoji = report["status"]
    q = report["quota"]
    msg = (
        f"{status_emoji} Wind 查询用量日报\n"
        f"━━━━━━━━━━━━━\n"
        f"日期: {report['date']}\n"
        f"计数器: {report['source']}\n"
        f"已用: {q['used']} / {q['limit']} 次 ({q['usage_pct']}%)\n"
        f"剩余: {q['remaining']} 次\n"
        f"━━━━━━━━━━━━━\n"
        f"{'⚠️ 配额紧张，注意控制 Wind 调用频率' if q['remaining'] <= 20 else '✅ 配额充足'}"
    )
    try:
        import subprocess

        cmd = [
            "lark-cli",
            "im",
            "+messages-send",
            "--chat-id",
            "oc_9ee5303497f5e0e71666b610d6bdc346",
            "--as",
            "bot",
            "--markdown",
            msg,
        ]
        subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        print("[push] ✅ 已推送飞书")
    except Exception as e:
        print(f"[push] ❌ 推送失败: {e}", file=sys.stderr)


def main():
    report = build_report()
    if "--json" in sys.argv:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        if "error" in report:
            print(f"❌ {report['error']}")
            return
        q = report["quota"]
        print(f"{report['status']} Wind 查询用量 | {report['date']}")
        print("  ─────────────────────────────")
        print(f"  计数器: {report['source']}")
        print(f"  已用: {q['used']} / {q['limit']} 次 ({q['usage_pct']}%)")
        print(f"  剩余: {q['remaining']} 次")
        if q["remaining"] <= 20:
            print("  ⚠️ 配额紧张！")

    if "--push" in sys.argv:
        push_to_feishu(report)


if __name__ == "__main__":
    main()
