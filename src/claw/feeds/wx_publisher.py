#!/usr/bin/env python3
"""微信早报/晚报推送输出层（三层架构第三层）。

职责：
  - stdout 输出报告正文（默认行为）
  - push=True 时**委托**给专用推送脚本 push_morning_report.py / push_evening_report.py

设计约束（项目铁律，勿违反）：
  1. 报告推送必须走 push_*_report.py（自建 docx + 交互卡片 + 「📄完整报告」按钮）
  2. 禁止在本模块内联拼卡片、禁止把 stdout 原文直推群
  3. 本模块只负责"打印 + 转交"，不承载任何报告格式逻辑

历史：2026-07-31 重建。此前该模块缺失导致
`wx_morning_report.py --collect-only` 之外的路径 ImportError。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_PUSH_SCRIPTS = {
    "morning": _PROJECT_ROOT / ".workbuddy" / "scripts" / "push_morning_report.py",
    "evening": _PROJECT_ROOT / ".workbuddy" / "scripts" / "push_evening_report.py",
}


def print_report(report: str, push: bool = False, period: str = "morning") -> None:
    """输出报告；push=True 时委托专用推送脚本。

    Args:
        report: 报告正文（字符串）
        push:   是否推送飞书群。默认 False，只打印
        period: "morning" | "evening"，决定委托哪个推送脚本
    """
    print(report)

    if not push:
        return

    script = _PUSH_SCRIPTS.get(period)
    if script is None or not script.exists():
        print(
            f"\n[wx_publisher] ⚠️ 未找到推送脚本 {script}，跳过推送。"
            f"\n[wx_publisher] 请手动执行: python3 .workbuddy/scripts/push_{period}_report.py",
            file=sys.stderr,
        )
        return

    print(f"\n[wx_publisher] → 委托 {script.name} 推送…", file=sys.stderr)
    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(_PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=300,
            env={**os.environ},
        )
        if result.returncode == 0:
            print("[wx_publisher] ✅ 推送完成", file=sys.stderr)
        else:
            print(
                f"[wx_publisher] ❌ 推送失败 (exit {result.returncode})"
                f"\n{result.stderr[-2000:]}",
                file=sys.stderr,
            )
    except subprocess.TimeoutExpired:
        print("[wx_publisher] ❌ 推送超时 (300s)", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"[wx_publisher] ❌ 推送异常: {e}", file=sys.stderr)


__all__ = ["print_report"]
