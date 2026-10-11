"""结构性门禁：脚本的路径常量必须指向**存在**的文件（2026-10-11 审计配套）。

事故形态（一次扫出 5 文件 10 处）：
    `.workbuddy/scripts/` 下的脚本写 `PROJECT_ROOT = Path(__file__).resolve().parent.parent`
    （此时 PROJECT_ROOT 已经等于 `.workbuddy`），却继续拼 `PROJECT_ROOT / ".workbuddy" / "data" / ...`
    → 常量恒指向 `.workbuddy/.workbuddy/...`（**永远不存在**）→ 依赖它的功能静默失效：
      · advisor_rules 行业集中度 → 恒「无有效持仓数据」
      · market_sentiment 行业缓存 → 读不到
      · cross_portfolio_monitor / t0_strategy / sector_strength_layer → 同上

本门禁用「常量指向的文件是否存在」直接判定，比读代码更快也更可靠。

⚠️ 2026-10-11 修正一处**必然误红**：原实现把「运行时产物」也当输入查存在性
—— `sector_strength_layer.OUTPUT` 指向 `.workbuddy/data/sector_strength.json`，
它是**生成物、未入库（untracked/ignored）**，干净检出里根本不存在
→ CI 判红（`/home/runner/work/Claw/Claw/.workbuddy/data/sector_strength.json` 不存在），
而本机因为跑过自动化所以存在 = 本地永远绿。
→ 故分两桶：**INPUT 必须存在**（入库的输入/账本），**OUTPUT 只查父目录存在 + 无 `.workbuddy/.workbuddy` 双嵌套**
（生成物在干净检出里不存在是正常的，门禁要抓的是**路径拼错**，不是"文件还没生成"）。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / ".workbuddy" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# ── 输入常量：必须是**入库的真实文件**（跑测试时不存在 = 拼错或被删）──
INPUT_CASES = [
    ("advisor_rules", "USER_PORTFOLIO"),
    ("cross_portfolio_monitor", "USER_PORTFOLIO"),
    ("cross_portfolio_monitor", "SIM_PORTFOLIO"),
    ("market_sentiment", "SECTOR_CACHE"),
    ("t0_strategy", "USER_PORTFOLIO"),
    ("refresh_portfolio", "PORTFOLIO_PATH"),
    ("check_holdings_freshness", "LIVE_PORTFOLIO"),
    ("check_holdings_freshness", "SIM_PORTFOLIO"),
]

# ── 输出常量：**运行时生成、不入库**（干净检出里不存在是正常的）──
#    门禁只查「父目录存在 + 无双嵌套」：抓路径拼错，不抓"还没生成"。
#    ⚠️ 往这里加条目之前，先确认目标是生成物（`git ls-files --error-unmatch <path>` 应报未跟踪）。
OUTPUT_CASES = [
    ("sector_strength_layer", "OUTPUT"),
]


def _resolve(module: str, const: str) -> Path:
    mod = importlib.import_module(module)
    return Path(str(getattr(mod, const)))


@pytest.mark.parametrize(("module", "const"), INPUT_CASES)
def test_input_constant_points_to_existing_file(module, const):
    path = _resolve(module, const)
    assert path.exists(), f"{module}.{const} 指向不存在的路径：{path}（输入常量必须入库）"
    # 双保险：绝不出现 `.workbuddy/.workbuddy`
    assert ".workbuddy/.workbuddy" not in str(path)


@pytest.mark.parametrize(("module", "const"), OUTPUT_CASES)
def test_output_constant_parent_dir_exists_and_no_double_nesting(module, const):
    path = _resolve(module, const)
    assert ".workbuddy/.workbuddy" not in str(path), f"{module}.{const} 出现双嵌套：{path}"
    assert path.parent.is_dir(), (
        f"{module}.{const} 的父目录不存在：{path.parent}"
        "（输出路径的目录应在仓库里存在；不存在说明路径拼错）"
    )
