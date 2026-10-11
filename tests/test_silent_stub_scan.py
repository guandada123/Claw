"""`silent_stub_scan.py` 的回归测试（2026-10-11 建立）。

动机：该扫描器要把「看起来已实现、实则空转」做成**门禁**，而门禁本身必须先被证明
「真的在查」—— 本文件钉住三类：
  A. **正检**：真的能抓出已知缺陷形态（拿 QTS 那例的形态做 fixture）
  B. **两个已修的自身缺陷**（都是拿已知缺陷回归时当场发现的）
     · `self._xxx(` 形式的调用**没被算作 call site** → 高危级被降级
     · 函数块取到 `end_lineno` 为止 → **尾随注释没进块** → `bare_comment` 误判成 `empty`
  C. **豁免与降噪**：显式 `# stub-scan: ok` 可豁免；abstractmethod / Protocol 不报
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

SRC = (
    pathlib.Path(__file__).resolve().parent.parent / ".workbuddy" / "scripts" / "silent_stub_scan.py"
)
_spec = importlib.util.spec_from_file_location("stub_scan", SRC)
ss = importlib.util.module_from_spec(_spec)
sys.modules["stub_scan"] = ss
_spec.loader.exec_module(ss)


def _scan(tmp_path, files: dict[str, str]) -> list[dict]:
    for name, text in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return ss.scan_repo(tmp_path)


# ── A. 正检：已知缺陷形态 ────────────────────────────────────────


def test_detects_called_stub_with_bare_todo(tmp_path):
    """QTS 那例的形态：docstring + 一行裸注释，且**调用方就在同文件**（`self._xxx(`）。"""
    got = _scan(
        tmp_path,
        {
            "svc.py": (
                "class C:\n"
                "    def _check_new_high_after_sell(self, sym, last):\n"
                '        """检查是否创新高"""\n'
                "        # TODO: 接入行情数据后实现\n"
                "\n"
                "    def caller(self):\n"
                "        self._check_new_high_after_sell('x', {})\n"
            )
        },
    )
    assert len(got) == 1, got
    r = got[0]
    assert r["name"] == "_check_new_high_after_sell"
    assert r["kind"] == "bare_comment"
    assert r["calls"] >= 1, "同文件里的 self._xxx( 调用必须被算作 call site"
    assert r["severity"] == "🔴"


def test_clean_function_is_not_reported(tmp_path):
    got = _scan(tmp_path, {"a.py": "def f(x):\n    return x + 1\n"})
    assert got == []


def test_not_implemented_raise_is_reported(tmp_path):
    got = _scan(tmp_path, {"a.py": "def f():\n    raise NotImplementedError\n"})
    assert len(got) == 1 and got[0]["kind"] == "not_implemented"


# ── B. 两个已修的自身缺陷 ───────────────────────────────────────


def test_self_prefixed_call_counts_as_call_site(tmp_path):
    """回归：`(?<![\\w.])` 会把 `.` 排除 → `self.foo(` 全部漏计（高危级降级）。"""
    got = _scan(
        tmp_path,
        {
            "a.py": "def f():\n    pass\n",
            "b.py": "def g(o):\n    o.f()\n    o.f()\n",
        },
    )
    assert got and got[0]["calls"] >= 2, got


def test_trailing_comment_enters_block(tmp_path):
    """回归：注释不是 AST 节点，`end_lineno` 不含尾随注释 → 会把 bare_comment 判成 empty。"""
    got = _scan(tmp_path, {"a.py": 'def f():\n    """doc"""\n    # TODO: 以后做\n'})
    assert len(got) == 1 and got[0]["kind"] == "bare_comment", got


def test_definition_line_does_not_inflate_call_count(tmp_path):
    """`def f(` 也会命中放宽后的正则 → 必须减掉定义处，否则"没人调用"会被算成"被调用"。"""
    got = _scan(tmp_path, {"a.py": "def f():\n    pass\n"})
    assert got and got[0]["calls"] == 0, got


# ── C. 豁免与降噪 ───────────────────────────────────────────────


def test_escape_hatch_skips(tmp_path):
    got = _scan(tmp_path, {"a.py": "def f():\n    # TODO: x  # stub-scan: ok\n"})
    assert got == []


def test_abstractmethod_is_exempt(tmp_path):
    got = _scan(
        tmp_path,
        {
            "a.py": (
                "import abc\n"
                "class B(abc.ABC):\n"
                "    @abc.abstractmethod\n"
                "    def f(self):\n"
                "        ...\n"
            )
        },
    )
    assert got == []


def test_overload_is_exempt(tmp_path):
    got = _scan(tmp_path, {"a.py": "import typing\n@typing.overload\ndef f(x: int): ...\n"})
    assert got == []


def test_protocol_ellipsis_is_exempt(tmp_path):
    got = _scan(
        tmp_path,
        {"a.py": "from typing import Protocol\nclass P(Protocol):\n    def f(self): ...\n"},
    )
    assert got == []


def test_repo_roots_missing_yields_rc2(tmp_path):
    rc = ss.main(["x", "--root", str(tmp_path / "nope")])
    assert rc == 2, "路径不可读必须 rc=2（**不许当成'扫完没事'**）"
