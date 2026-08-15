#!/usr/bin/env python3
"""
ops_center_liveness_watchdog.py — 统一巡检中枢存活看门狗（独立调度）

设计意图：
  统一巡检中枢(unified_ops_center.py)每小时运行时会写回 cross_project_state.json 的
  monitoring.global.unified_ops_center.self_health.last_ok_ts（last_run 心跳）。
  若中枢自身挂死/静默失败，该时间戳不再更新。本看门狗独立调度（每2h），
  读取 last_ok_ts 算间隔，超过阈值(默认180min=3倍调度周期)则飞书告警"中枢可能失联"。

  为什么独立：中枢无法检测自身失联（它不跑就不写锚）。必须由外部独立调度兜底，
  与中枢形成"互相 watchdog"的韧性结构（对齐 long-running-agent 状态锚恢复规范）。

用法：
  python3 ops_center_liveness_watchdog.py            # 真实运行（超阈值→飞书告警）
  python3 ops_center_liveness_watchdog.py --dry-run # 只检测不推送
  python3 ops_center_liveness_watchdog.py --threshold-min 180
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

CROSS_STATE_PATH = Path.home() / ".workbuddy" / "cross_project_state.json"
PUSH = Path(__file__).resolve().parent / "push_feishu.sh"
CHAT_ID = "oc_9ee5303497f5e0e71666b610d6bdc346"
DEFAULT_THRESHOLD_MIN = 180  # 3倍中枢调度周期(60min)


def _push_card(title: str, content: str) -> bool:
    env = dict(os.environ)
    env.setdefault("FEISHU_CHAT_ID", CHAT_ID)
    env["PUSH_LEVEL"] = "alert"
    try:
        r = subprocess.run(
            ["bash", str(PUSH), title, content], capture_output=True, text=True, timeout=60, env=env
        )
        return r.returncode == 0
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--threshold-min", type=int, default=DEFAULT_THRESHOLD_MIN)
    args = ap.parse_args()

    try:
        data = (
            json.loads(CROSS_STATE_PATH.read_text(encoding="utf-8"))
            if CROSS_STATE_PATH.exists()
            else {}
        )
    except Exception as e:
        print(f"[liveness-watchdog] 状态锚读失败: {e}")
        return 1

    sh = (
        data.get("monitoring", {})
        .get("global", {})
        .get("unified_ops_center", {})
        .get("self_health", {})
    )
    last_ok = sh.get("last_ok_ts")
    if not last_ok:
        print("[liveness-watchdog] 状态锚无 self_health.last_ok_ts（中枢从未成功运行过）→ 告警")
        if not args.dry_run:
            _push_card(
                "🛡️ 巡检中枢存活看门狗",
                "⚠️ 统一巡检中枢状态锚无 last_ok_ts（可能从未成功运行），请检查中枢调度。",
            )
        return 0

    try:
        last_dt = datetime.datetime.fromisoformat(last_ok)
    except Exception:
        print(f"[liveness-watchdog] last_ok_ts 解析失败: {last_ok}")
        return 1

    gap_min = round((datetime.datetime.now() - last_dt).total_seconds() / 60, 1)
    print(
        f"[liveness-watchdog] 中枢上次成功: {last_ok} | 间隔 {gap_min}min | 阈值 {args.threshold_min}min"
    )

    if gap_min > args.threshold_min:
        msg = (
            f"🚨 **统一巡检中枢可能失联**\n\n"
            f"• 上次成功运行：{last_ok}（{gap_min} 分钟前）\n"
            f"• 阈值：{args.threshold_min} 分钟\n"
            f"• 可能原因：中枢自动化静默失败 / 宿主进程挂死 / 调度系统异常\n"
            f"• 建议：检查 automation-1785982929477（统一巡检中枢，真正写 last_ok_ts 的中枢）运行记录，必要时手动重跑"
        )
        print("[liveness-watchdog] 超阈值 → 飞书告警")
        if not args.dry_run:
            _push_card("🛡️ 巡检中枢存活看门狗", msg)
    else:
        print("[liveness-watchdog] 中枢存活正常 → SILENT")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
