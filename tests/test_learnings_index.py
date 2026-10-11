"""`learnings_index.py` 的回归测试（2026-10-11 建立）。

动机：两仓的**手工** `LEARNINGS.md` 索引分别停在 08-31 / 09-06，而 dated 文件都到 10-11
（落后 5~6 周）—— 根因是"同一份信息存两处、只维护一处"。
处置是把索引改成**派生物**（本脚本生成 `INDEX.md`）。既然是派生物，就必须钉住：
  · 抽不到字段时**留空，不许编造**
  · `--check` 能判出"落后"，否则它就是个"看着在跑其实没查"的守卫
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

SRC = (
    pathlib.Path(__file__).resolve().parent.parent
    / ".workbuddy"
    / "scripts"
    / "learnings_index.py"
)
_spec = importlib.util.spec_from_file_location("learnings_index", SRC)
li = importlib.util.module_from_spec(_spec)
sys.modules["learnings_index"] = li
_spec.loader.exec_module(li)

FULL = """# ★升级候选：示例标题

**日期**：2026-10-11（某场景）
**等级**：P1（影响面说明）
**类型**：silent-pass（子类）
**状态**：已修复（abc123）

---

## 1. 现象
…
"""

BARE = """# 只有标题的老条目

## 1. 现象
（老格式，没有字段块）
"""


def _mk(tmp_path, files: dict[str, str]) -> pathlib.Path:
    d = tmp_path / ".learnings"
    d.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (d / name).write_text(text, encoding="utf-8")
    return d


def test_extracts_head_fields(tmp_path):
    d = _mk(tmp_path, {"2026-10-11-demo.md": FULL})
    (r,) = li.collect(d)
    assert r["title"].startswith("★升级候选：示例标题")
    assert r["日期"] == "2026-10-11（某场景）"
    assert r["等级"].startswith("P1")
    assert r["类型"].startswith("silent-pass")
    assert r["状态"].startswith("已修复")


def test_missing_fields_stay_empty_not_invented(tmp_path):
    """抽不到就留空 —— **不许编造**（老格式条目本就该显示为未标注）。"""
    d = _mk(tmp_path, {"2026-08-01-old.md": BARE})
    (r,) = li.collect(d)
    assert r["title"] == "只有标题的老条目"
    assert r["等级"] == "" and r["类型"] == "" and r["状态"] == ""


def test_ignores_non_dated_files(tmp_path):
    """`LEARNINGS.md` / `INDEX.md` 这些非 dated 文件不得进表。"""
    d = _mk(
        tmp_path,
        {
            "2026-10-11-a.md": FULL,
            "LEARNINGS.md": "# 手工索引\n### 2026-01-01 老条目\n",
            "INDEX.md": "| 2026-01-01 | x |\n",
        },
    )
    assert len(li.collect(d)) == 1


def test_rows_sorted_newest_first(tmp_path):
    d = _mk(tmp_path, {"2026-08-01-a.md": BARE, "2026-10-11-b.md": FULL})
    dates = [r["date"] for r in li.collect(d)]
    assert dates == ["2026-10-11", "2026-08-01"]


def test_check_detects_missing_then_fresh(tmp_path, capsys):
    """`--check` 必须先判"落后"、生成后判"最新" —— 否则守卫本身没被验证。"""
    d = _mk(tmp_path, {"2026-10-11-a.md": FULL, "2026-10-10-b.md": BARE})
    assert li.main(["x", "--dir", str(d), "--check"]) == 1, "没有 INDEX.md 时必须判落后"

    assert li.main(["x", "--dir", str(d)]) == 0
    assert (d / "INDEX.md").exists()
    assert li.main(["x", "--dir", str(d), "--check"]) == 0, "刚生成完必须判最新"

    # 新增一篇 → 又该判落后
    (d / "2026-10-12-c.md").write_text(FULL, encoding="utf-8")
    assert li.main(["x", "--dir", str(d), "--check"]) == 1


def test_rc2_when_no_dated_files(tmp_path):
    d = tmp_path / ".learnings"
    d.mkdir()
    assert li.main(["x", "--dir", str(d)]) == 2, "判不了必须 rc=2，不许当成功"
    assert li.main(["x", "--dir", str(tmp_path / "nope")]) == 2
