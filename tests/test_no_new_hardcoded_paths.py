"""P1-12 增量门禁：**禁止新增**本机绝对路径（/Users/guan）到 .workbuddy/scripts。

## 为什么是"增量"而不是"清零"
真码目录里存量 38 文件 / 64 处硬编码（审计 P1-12）。一次性全改会同时动到**交易链路脚本**
（盘中监控/账户同步等），在交易时段做等于自己制造故障。故采用审计建议的做法：
  **门禁防新增（本文件） + 分批替换（每次只动非关键路径的少数文件）**。

## 判据（三条，全部机器判定）
  ① **新文件**出现 ≥1 处 → ❌ 失败
  ② 已有文件的出现数 **超过基线** → ❌ 失败（新增即拦住）
  ③ 已有文件出现数**少于基线** → ✅ 通过，但**提示更新基线**（把数字调小，让门禁逐步收紧）
  ④ 已清零但仍在基线表里 → ❌ 失败（防止基线腐烂成"永远允许"）

## 为什么不用 pre-commit 钩子
pre-commit 只在本机装了钩子的仓库生效（本仓曾长期因未安装钩子而 CI 红），
而 **pytest 在 CI 与本机都会跑** → 门禁覆盖面更广。基线表是**入库文件**，随仓库走。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / ".workbuddy" / "scripts"
BASELINE = Path(__file__).resolve().parent / "hardcoded_paths_baseline.json"
# 拼装构造，避免本门禁文件自身被 test_ci_hermeticity 判为「测试里写死本机路径」
# （沿用该守卫自己的做法：守卫不该是它自己唯一的例外）
PATTERN = re.compile("/" + "Use" + "rs/guan")
SELF = Path(__file__).name  # 本测试自己会提到该模式，必须排除


def scan() -> dict[str, int]:
    """{文件名: 出现次数}，只统计 .py（与基线生成口径一致）。"""
    out = {}
    for p in sorted(SCRIPTS.glob("*.py")):
        n = len(PATTERN.findall(p.read_text(encoding="utf-8", errors="ignore")))
        if n:
            out[p.name] = n
    return out


def load_baseline() -> dict[str, int]:
    return json.loads(BASELINE.read_text(encoding="utf-8"))["files"]


def test_no_new_hardcoded_paths():
    current, baseline = scan(), load_baseline()
    new_files = sorted(set(current) - set(baseline))
    increased = sorted(
        (f, baseline[f], current[f])
        for f in set(current) & set(baseline)
        if current[f] > baseline[f]
    )
    assert not new_files, (
        "以下文件**新增**了本机绝对路径 /Users/guan（P1-12 禁新增）——"
        "请改用派生路径（Path(__file__).resolve().parents[N] / Path.home()）或环境变量：\n  - "
        + "\n  - ".join(new_files)
    )
    assert not increased, "以下文件的本机绝对路径**比基线更多**（禁新增）：\n  - " + "\n  - ".join(
        f"{f}: {b} → {c}" for f, b, c in increased
    )


def test_baseline_has_no_stale_zero_entries():
    """基线腐烂守卫：文件已清零却还留在表里 = 门禁对它是"永远允许"，必须删条目。"""
    current, baseline = scan(), load_baseline()
    stale = sorted(f for f in baseline if f not in current)
    assert not stale, (
        "以下文件已无 /Users/guan，但仍留在基线表（会让门禁对它永久放行）——"
        f"请从 {BASELINE.name} 删除这些条目：\n  - " + "\n  - ".join(stale)
    )


def test_report_progress_and_keep_baseline_tight(capsys):
    """把「已比基线减少」的文件报出来（不失败），提示下调基线让门禁越来越紧。"""
    current, baseline = scan(), load_baseline()
    improved = sorted(
        (f, baseline[f], current[f])
        for f in set(current) & set(baseline)
        if current[f] < baseline[f]
    )
    if improved:
        print(  # noqa: T201 —— 本用例的产出就是"给人看的提示"
            "📉 以下文件已比基线减少，请把基线数字调小（门禁逐步收紧）：\n  - "
            + "\n  - ".join(f"{f}: {b} → {c}" for f, b, c in improved)
        )
    print(  # noqa: T201 —— 同上一处：本用例产出即提示文本
        f"📊 当前硬编码总量：{sum(current.values())} 处 / {len(current)} 文件（基线 {sum(baseline.values())} 处）"
    )
