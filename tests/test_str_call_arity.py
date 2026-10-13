"""`str()` 的参数个数门禁 —— 防「批量重构把 str() 当路径拼接用」。

## 为什么（2026-10-12 实踩，一次批量重构打坏 7 个脚本）
`refactor(p12) 硬编码治理` 那条线为了去掉写死的本机路径，把

```python
str(Path.home() / ".workbuddy" / "binaries" / "python" / "envs" / "default" / "bin" / "python3")
```

机械替换成了

```python
str(Path.home() / ".workbuddy" / "binaries" / "python", "envs", "default", "bin", "python3")
```

**`str()` 没有这种签名**（`str(object)` / `str(object, encoding, errors)`，最多 3 个位置参数）→
运行到该行即 `TypeError: str expected at most 3 arguments, got 5`。

后果与「为什么 CI 没拦住」：
  - 这些行多数在**模块级**（`PY = ...` / `LANG = ...` / `NPX = _resolve_npx()`），即**一跑就崩**；
  - 但 `ruff check` 是静态风格检查、**不做类型/arity 校验**；`py_compile` 只看语法；
    而这些脚本**没有单测覆盖** → 坏代码**合法提交、CI 全绿**；
  - 实测受害者含 `check_tracked_tree.py`（我自己的工具：`make ci-tracked` 直接 TypeError）、
    `earnings_calendar.py` 与 `run_yupen_primary.py`（**被两条 ACTIVE 自动化引用**）。

## 判据
仓库代码里**不得**出现「`str()` 传 4 个以上位置参数」的调用（3 个位置参数是合法的
`str(bytes, encoding, errors)`；**只拦 ≥4**，避免误伤）。
这是**窄而确定**的一条：只针对一个机械错误类别，不做通用类型检查（不做半成品检测器）。
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
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
    "site-packages",
}


def find_str_misuse(tree: ast.AST) -> list[tuple[int, int]]:
    """返回 [(行号, 位置参数个数), ...]，列出 `str()` 传了 >3 个位置参数的位置。"""
    hits: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "str"
            and len(node.args) > 3
        ):
            hits.append((node.lineno, len(node.args)))
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


def test_no_str_call_with_too_many_positional_args():
    offenders: list[str] = []
    for p in _iter_py_files():
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, OSError, SyntaxError):
            continue
        hits = find_str_misuse(tree)
        if hits:
            detail = ", ".join(f"L{ln}({n} args)" for ln, n in hits[:4])
            offenders.append(f"{p.relative_to(REPO)}（{detail}）")
    assert not offenders, (
        "以下文件把 `str()` 当路径拼接用了（`str()` 最多 3 个位置参数，运行到那行即 TypeError；"
        "2026-10-12 一条批量重构就这么打坏了 7 个脚本，且 CI 全绿）：\n  - "
        + "\n  - ".join(offenders)
        + "\n改法：把逗号换成 `/` 的 Path 拼接，即 `Path.home() / 'a' / 'b' / 'c'`。"
    )


def test_detector_catches_planted_misuse():
    """证伪：判据必须抓得住违规写法，且**不得误伤**合法写法（含 `str(bytes, enc, err)`）。"""
    bad = ast.parse(
        "from pathlib import Path\n"
        "P = str(Path.home() / '.workbuddy' / 'bin', 'versions', '3.13.12', 'bin', 'python3')\n"
    )
    assert find_str_misuse(bad), "抓不到 `str(..., 'b', 'c', 'd', 'e')`"
    good = ast.parse("x = str(b'abc', 'utf-8', 'replace')\n")
    assert find_str_misuse(good) == [], "误伤了合法的 str(bytes, encoding, errors)"
    good2 = ast.parse("from pathlib import Path\ny = str(Path.home() / 'a' / 'b')\n")
    assert find_str_misuse(good2) == [], "误伤了正确写法"
