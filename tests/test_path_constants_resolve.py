"""结构性门禁：脚本的路径常量必须指向**存在**的文件（2026-10-11 审计配套）。

事故形态（一次扫出 5 文件 10 处）：
    `.workbuddy/scripts/` 下的脚本写 `PROJECT_ROOT = Path(__file__).resolve().parent.parent`
    （此时 PROJECT_ROOT 已经等于 `.workbuddy`），却继续拼 `PROJECT_ROOT / ".workbuddy" / "data" / ...`
    → 常量恒指向 `.workbuddy/.workbuddy/...`（**永远不存在**）→ 依赖它的功能静默失效：
      · advisor_rules 行业集中度 → 恒「无有效持仓数据」
      · market_sentiment 行业缓存 → 读不到
      · cross_portfolio_monitor / t0_strategy / sector_strength_layer → 同上

本门禁用「常量指向的文件是否存在」直接判定，比读代码更快也更可靠。
新增此类常量时把它登记到 CASES 即可。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / ".workbuddy" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# (模块名, 常量名)：这些常量都必须指向真实存在的文件
CASES = [
    ("advisor_rules", "USER_PORTFOLIO"),
    ("cross_portfolio_monitor", "USER_PORTFOLIO"),
    ("cross_portfolio_monitor", "SIM_PORTFOLIO"),
    ("market_sentiment", "SECTOR_CACHE"),
    ("t0_strategy", "USER_PORTFOLIO"),
    ("sector_strength_layer", "OUTPUT"),
    ("refresh_portfolio", "PORTFOLIO_PATH"),
    ("check_holdings_freshness", "LIVE_PORTFOLIO"),
    ("check_holdings_freshness", "SIM_PORTFOLIO"),
]


@pytest.mark.parametrize(("module", "const"), CASES)
def test_path_constant_points_to_existing_file(module, const):
    mod = importlib.import_module(module)
    value = getattr(mod, const)
    path = Path(value)
    assert path.exists(), f"{module}.{const} 指向不存在的路径：{path}"
    # 双保险：绝不出现 `.workbuddy/.workbuddy`
    assert ".workbuddy/.workbuddy" not in str(path)
