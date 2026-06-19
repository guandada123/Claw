#!/usr/bin/env python3
"""Claw 心跳/存活检测脚本
被监控系统（MarvisBridge / Cron）周期性调用，写入时间戳到心跳文件。
同时检查关键依赖是否可用。

用法:
    python3 heartbeat.py          # 写心跳 + 检查依赖
    python3 heartbeat.py --check  # 只检查，不写心跳（用于 HEALTHCHECK）
    python3 heartbeat.py --json   # JSON 格式输出
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
HEARTBEAT_FILE = PROJECT_DIR / "data" / "heartbeat.json"
DB_PATH = Path.home() / ".workbuddy" / "workbuddy.db"


def check_dependencies() -> dict:
    """检查关键依赖"""
    results = {"db": False, "data_dir": False, "scripts": False}

    # 检查 WorkBuddy DB
    try:
        if DB_PATH.exists():
            conn = sqlite3.connect(str(DB_PATH))
            conn.execute("SELECT 1 FROM automations LIMIT 1")
            conn.close()
            results["db"] = True
    except Exception:
        pass

    # 检查数据目录
    data_dir = PROJECT_DIR / "data"
    results["data_dir"] = data_dir.is_dir()

    # 检查核心脚本
    scripts_dir = PROJECT_DIR / "scripts"
    required = ["sim_trade.py", "market_data.py", "strategy_generator.py"]
    results["scripts"] = all((scripts_dir / s).exists() for s in required)

    return results


def main():
    parser = argparse.ArgumentParser(description="Claw 心跳检测")
    parser.add_argument("--check", action="store_true", help="仅检查，不写心跳")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    deps = check_dependencies()
    healthy = all(deps.values())
    now = datetime.now().isoformat()

    if not args.check:
        # 写心跳文件
        heartbeat_data = {
            "last_heartbeat": now,
            "healthy": healthy,
            "dependencies": deps,
        }
        HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(HEARTBEAT_FILE, "w", encoding="utf-8") as f:
            json.dump(heartbeat_data, f, ensure_ascii=False, indent=2)

    if args.json:
        print(
            json.dumps(
                {"healthy": healthy, "dependencies": deps, "checked_at": now}, ensure_ascii=False
            )
        )
    else:
        status = "✅" if healthy else "❌"
        print(f"{status} Claw heartbeat | {now}")
        for name, ok in deps.items():
            print(f"  {'✅' if ok else '❌'} {name}")

    sys.exit(0 if healthy else 1)


if __name__ == "__main__":
    main()
