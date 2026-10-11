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

## 判据
凡是**没有** `from __future__ import annotations` 的 .py 文件，其注解里**不得**出现 `X | Y`。
（加了 future import 之后注解变成惰性字符串，3.9 也不会求值 → 允许。）

这是一个**结构性**门禁：它不测某个功能，而是测"这套代码在仓库声明的两种解释器下都能被导入"。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# 只扫仓库自己的代码；skip 掉第三方/归档/虚拟环境
ROOTS = [".workbuddy/scripts", ".workbuddy/lib", "scripts", "src/claw", "tests"]
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
