"""test_py39_compat.py — 防「在新解释器上正常、在 3.9 上崩溃」的注解兼容性门禁。

## 为什么需要它（2026-10-11 实踩）
本仓库**解释器不统一**（AGENTS.md §2：默认 `python3` 是 3.13，`/usr/bin/python3` 是 3.9，
且知识库/向量检索必须用后者）。而脚本里写 `def f() -> bool | None:` 这种 **PEP 604** 注解时：
  - 3.10+ 正常；
  - **3.9 下在模块导入/函数定义时就抛 `TypeError: unsupported operand type(s) for |`** ——
    脚本连 `--help` 都跑不起来。
实战后果不是"某个功能不可用"，而是**伪装成业务告警**：
`.workbuddy/scripts/qts_pmf_health_guard.py` 被巡检中枢以 `sys.executable` 调起，
在有 3.9 的链路上直接崩 → 中枢把它读成「qts_guard 退出码 1」→ **报出假的「GitHub CI 红灯」**；
tests/ 下两个文件同因导致 **pytest 直接采集失败**（整个文件不跑）。

## 判据（两类，2026-10-12 补第二类）
① **注解**：没有 `from __future__ import annotations` 的 .py，注解里**不得**出现 `X | Y`
   （加了 future import 后注解变惰性字符串、不求值 → 允许）。
② **运行时**：`isinstance(x, A | B)` / `issubclass(...)` / `TypeVar(bound=A | B)` 这类
   **运行时会真的求值**的位置，一律**不得**用 `A | B` —— **future import 救不了它们**，
   3.9 下会抛 `TypeError: unsupported operand type(s) for |`，写成 `(A, B)` 即可。
   实战（2026-10-12）：`.workbuddy/tests/test_portfolio.py:68` 的
   `isinstance(pf["cash"], int | float)` 让 `make test`（走 `/usr/bin/python3`）单点失败；
   `.workbuddy/scripts/fetch_wx_rss.py:259` 的 `isinstance(v, dict | list)` 是同类隐患。

这是一个**结构性**门禁：它不测某个功能，而是测"这套代码在仓库声明的两种解释器下都能被导入并运行到"。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# 只扫仓库自己的代码；skip 掉第三方/归档/虚拟环境
ROOTS = [".workbuddy/scripts", ".workbuddy/lib", ".workbuddy/tests", "scripts", "src/claw", "tests"]
SKIP_DIR_PARTS = {
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    ".git",
    ".ruff_cache",
    ".mypy_cache",
    "_archive",
    "archive",
    "experiments",
    "dist",
    "build",
    ".egg-info",
}
FUTURE_IMPORT = "from __future__ import annotations"


def _pep604_usages(tree: ast.AST) -> list[tuple[int, str]]:
    """返回 [(行号, 所属名字), ...]，列出注解里使用 PEP 604 `X | Y` 的位置。"""

    def has_bitor(ann: ast.AST | None) -> bool:
        if ann is None:
            return False
        return any(isinstance(n, ast.BinOp) and isinstance(n.op, ast.BitOr) for n in ast.walk(ann))

    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs)
            anns = [a.annotation for a in args]
            if node.args.vararg:
                anns.append(node.args.vararg.annotation)
            if node.args.kwarg:
                anns.append(node.args.kwarg.annotation)
            anns.append(node.returns)
            if any(has_bitor(a) for a in anns):
                hits.append((node.lineno, node.name))
        elif isinstance(node, ast.AnnAssign):
            if has_bitor(node.annotation):
                hits.append((node.lineno, "变量注解"))
    return hits


def _iter_py_files():
    for root in ROOTS:
        d = REPO / root
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*.py")):
            if SKIP_DIR_PARTS & set(p.parts):
                continue
            yield p


def test_no_pep604_annotations_without_future_import():
    """没有 future import 的文件里不得出现 PEP 604 注解（否则 3.9 下导入即崩）。

    修法：在模块 docstring 之后加一行 `from __future__ import annotations`（语义为空操作）。
    """
    offenders: list[str] = []
    for p in _iter_py_files():
        try:
            src = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if FUTURE_IMPORT in src:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue  # 语法问题由别的门禁管，这里只盯注解兼容
        hits = _pep604_usages(tree)
        if hits:
            detail = ", ".join(f"L{ln}:{name}" for ln, name in hits[:4])
            offenders.append(f"{p.relative_to(REPO)}（{detail}）")
    assert not offenders, (
        "以下文件在注解里用了 PEP 604 `X | Y` 却没加 `from __future__ import annotations`，"
        "在 /usr/bin/python3（3.9）下导入即抛 TypeError：\n  - " + "\n  - ".join(offenders)
    )


@pytest.mark.parametrize(
    "path",
    [
        ".workbuddy/scripts/qts_pmf_health_guard.py",
        "tests/test_market_sentiment.py",
        "tests/test_alpha_factors.py",
    ],
)
def test_known_offenders_now_have_future_import(path):
    """2026-10-11 实修过的三个文件必须保持有 future import（防回退）。"""
    p = REPO / path
    assert p.is_file(), f"文件不存在：{path}"
    assert FUTURE_IMPORT in p.read_text(encoding="utf-8"), f"{path} 缺少 {FUTURE_IMPORT}"


# ─────────────────────────────────────────────────────────────
# 第二类：**运行时** PEP 604（future import 救不了）
# ─────────────────────────────────────────────────────────────
_RUNTIME_CALLS = {"isinstance", "issubclass"}
_RUNTIME_KWARGS = {"bound", "constraints"}  # TypeVar(...) 会在运行时求值


def _runtime_pep604_usages(tree: ast.AST) -> list[str]:
    """列出**运行时会求值**的 `A | B` 位置（这些位置加 future import 也没用）。"""

    def has_bitor(n: ast.AST | None) -> bool:
        return n is not None and any(
            isinstance(x, ast.BinOp) and isinstance(x.op, ast.BitOr) for x in ast.walk(n)
        )

    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if callee in _RUNTIME_CALLS and any(has_bitor(a) for a in node.args[1:]):
            hits.append(f"L{node.lineno}: {callee}(..., A | B) → 写成 (A, B)")
        for kw in node.keywords:
            if kw.arg in _RUNTIME_KWARGS and has_bitor(kw.value):
                hits.append(f"L{node.lineno}: {callee}({kw.arg}=A | B) → 写成 (A, B)")
    return hits


def test_no_runtime_pep604_type_unions():
    """运行时求值位置的 `A | B`（isinstance/issubclass/TypeVar）：**与 future import 无关**，一律拦。

    为什么单列一条：① 它不受 future import 保护，混在注解那条里会被"已有 future import"漏掉；
    ② 症状是运行到那一行才崩，比导入期崩更难定位。
    """
    offenders: list[str] = []
    for p in _iter_py_files():
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, OSError, SyntaxError):
            continue
        hits = _runtime_pep604_usages(tree)
        if hits:
            offenders.append(f"{p.relative_to(REPO)}（{'; '.join(hits[:3])}）")
    assert not offenders, (
        "以下文件在**运行时求值**位置用了 PEP 604 `A | B`（3.9 下必抛 TypeError，"
        "且 `from __future__ import annotations` 对它无效）：\n  - " + "\n  - ".join(offenders)
    )


def test_runtime_detector_catches_planted_code():
    """证伪：判据必须抓得住违规写法、且不误伤已修写法（防它退化成恒真空检查）。"""
    bad = ast.parse("def f(v):\n    return isinstance(v, dict | list)\n")
    assert _runtime_pep604_usages(bad), "抓不到 `isinstance(v, dict | list)`"
    bad2 = ast.parse("from typing import TypeVar\nT = TypeVar('T', bound=int | str)\n")
    assert _runtime_pep604_usages(bad2), "抓不到 `TypeVar(bound=A | B)`"
    good = ast.parse("def f(v):\n    return isinstance(v, (dict, list))\n")
    assert _runtime_pep604_usages(good) == [], "误伤已修写法"
