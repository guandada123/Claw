"""test_doc_volatile_claims.py — 入口文档不得写死「会随记忆层变化的计数/行号」。

## 为什么（2026-10-11 实测）
`AGENTS.md` §1 曾写「完整 **62** 条 + 逐条证据 … 规则层在**行 24–88**」，而当天实际已是 **65 条**、
规则层行号也随每次新增规则而变 —— 入口文档是**最先被读到**的文件，它写死的数字腐烂后，
读者（人或 AI）会拿错口径去核对，比"没有这句话"更糟。

## 判据
`AGENTS.md` 里不得出现形如「N 条」「行 A–B」「止于 NB」这类**从 MEMORY.md 结构算出来的**数字；
需要时改为**指路 + 取用命令**（`grep -n ...`），让数字只有一个权威来源（MEMORY.md 速查索引，
且由 `check_memory_visibility.py --fix` 回写）。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
AGENTS = REPO / "AGENTS.md"

# 易腐声明形态（限定在与记忆层结构相关的表述上，避免误伤无关数字）
VOLATILE = [
    (re.compile(r"完整\s*\d+\s*条"), "「完整 N 条」"),
    (re.compile(r"\d+\s*条一览"), "「N 条一览」"),
    (re.compile(r"规则层在行\s*\d+\s*[–—\-]\s*\d+"), "「规则层在行 A–B」"),
    (re.compile(r"规则层行\s*\d+\s*[–—\-]\s*\d+"), "「规则层行 A–B」"),
    (re.compile(r"共\s*\d+\s*条"), "「共 N 条」"),
    (re.compile(r"止于\s*\*{0,2}[\d,]+B"), "「止于 NB」"),
]


def find_volatile(text: str) -> list[str]:
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        for rx, label in VOLATILE:
            if rx.search(line):
                out.append(f"L{i}: {label} → {line.strip()[:90]}")
    return out


def test_agents_md_has_no_volatile_memory_counts():
    hits = find_volatile(AGENTS.read_text(encoding="utf-8"))
    assert not hits, (
        "AGENTS.md 里出现会随记忆层变化的硬编码计数/行号（改一次规则层就腐烂，且本文件最先被读）：\n  - "
        + "\n  - ".join(hits)
        + "\n改为「指路 + 取用命令」，数字只在 MEMORY.md 速查索引里（由 check_memory_visibility.py --fix 回写）。"
    )


def test_detector_can_actually_catch_a_violation():
    """证伪：判据必须抓得住违规写法，否则它是一条恒真空检查。"""
    planted = (
        "> 以上是规则层摘要。完整 999 条 + 逐条证据，规则层在行 1–2，共 999 条，止于 **9,999B**。"
    )
    assert find_volatile(planted), "判据抓不到违规写法"
