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
from pathlib import Path  # P1-12：位置无关化后需要


def _claw_root() -> Path:
    """位置无关地定位仓库根（向上找含 .workbuddy 的目录）。2026-10-12 P1-14 单源化时加入。"""
    p = Path(__file__).resolve().parent
    for cand in (p, *p.parents):
        if (cand / ".workbuddy").is_dir():
            return cand
    raise RuntimeError("找不到含 .workbuddy 的仓库根（脚本被放在异常位置？）")


from pathlib import Path  # 2026-10-12 P1-14：位置无关化的 _claw_root() 需要

# 确保能找到 claw 包


sys.path.insert(0, os.path.join(str(_claw_root()), "src"))


from claw.monitoring.wind_monitor import main

if __name__ == "__main__":
    main()
