"""
wind_monitor.py — Wind 高级监控脚本（自动化入口）

用法:
    python scripts/wind_monitor.py              # 完整监控
    python scripts/wind_monitor.py --technical   # 仅技术
    python scripts/wind_monitor.py --news        # 仅新闻
    python scripts/wind_monitor.py --screening   # 选股
"""

import os
import sys

# 确保能找到 claw 包
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

from claw.monitoring.wind_monitor import main

if __name__ == "__main__":
    main()
