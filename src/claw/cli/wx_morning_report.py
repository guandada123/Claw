#!/usr/bin/env python3
"""微信读书早报/晚报生成器 v3.0 — 薄壳 CLI 入口。

三层架构：
  - claw.feeds.wx_collector  → 数据采集（API + 技术信号 + 缓存）
  - claw.feeds.wx_assembler  → 报告组装（早报/晚报模板）
  - claw.feeds.wx_publisher  → 推送输出（飞书群 + stdout）

用法:
  python3 wx_morning_report.py --period morning          # 早报
  python3 wx_morning_report.py --period evening          # 晚报
  python3 wx_morning_report.py --collect-only            # 仅采集JSON
  python3 wx_morning_report.py --period morning --push   # 早报并推群
"""

import argparse
import sys
from pathlib import Path

# 确保项目根在 sys.path 中
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent.parent.parent
_SRC_DIR = str(_PROJECT_ROOT / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from claw.feeds.wx_collector import collect_data  # noqa: E402
from claw.feeds.wx_assembler import build_evening_report, build_morning_report  # noqa: E402
from claw.feeds.wx_publisher import print_report  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="微信早报/晚报生成器 v3.0 (三层架构)"
    )
    parser.add_argument("--period", choices=["morning", "evening"],
                        help="生成早报或晚报")
    parser.add_argument("--collect-only", action="store_true",
                        help="仅采集数据输出JSON，不做LLM分析和推送")
    parser.add_argument("--push", action="store_true",
                        help="【仅自动化内部用】生成后推送飞书群。默认不推送")
    args = parser.parse_args()

    if args.collect_only:
        collect_data()
        return

    if not args.period:
        parser.error("请指定 --period morning 或 --period evening")

    if args.period == "morning":
        report = build_morning_report()
    else:
        report = build_evening_report()

    # 默认只输出 stdout，不推群（防止原始格式误推）
    print_report(report, push=args.push)


if __name__ == "__main__":
    main()
