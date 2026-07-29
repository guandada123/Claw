#!/usr/bin/env python3
"""check_no_double_import.py — CI 守卫：禁止 `from scripts.` 双导入反模式回归。

scripts/ 目录没有 __init__.py，从不以包形式导入，因此所有
`from scripts.X import ...` 都是死代码兜底分支。该反模式已在 F6 整改中清除，
此脚本作为 CI 门禁防止复发。

配置（pyproject.toml）:
    [tool.ci.double_import]
    ignore_paths = ["tests/conftest.py"]   # 忽略的相对路径列表

用法:
    python3 scripts/check_no_double_import.py [-v|--verbose]
退出码:
    0 = 无违规
    1 = 发现双导入反模式
  -v / --verbose  打印每个被检查的文件名
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ImportError:
    import tomli as tomllib  # type: ignore[import-not-found,no-redef]

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def _load_ignored_paths() -> set[str]:
    """从 pyproject.toml 读取 [tool.ci.double_import] ignore_paths"""
    pyproject = ROOT / "pyproject.toml"
    if not pyproject.exists():
        return set()
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        ci = data.get("tool", {}).get("ci", {}).get("double_import", {})
        return set(ci.get("ignore_paths", []))
    except Exception:
        return set()


def main(argv: list[str] | None = None) -> int:
    verbose = "-v" in (argv or sys.argv) or "--verbose" in (argv or sys.argv)

    if not SCRIPTS.exists():
        print(f"[OK] 未找到 {SCRIPTS}")
        return 0

    ignored = _load_ignored_paths()
    if verbose and ignored:
        print(f"  忽略路径: {', '.join(sorted(ignored))}")

    violations: list[str] = []
    checked = 0
    for path in sorted(SCRIPTS.rglob("*.py")):
        rel = str(path.relative_to(ROOT))
        if "archive" in path.parts or rel in ignored:
            continue
        checked += 1
        if verbose:
            print(f"  ✓ {rel}")
        for ln, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("from scripts.", "import scripts.")):
                violations.append(f"{rel}:{ln}: {stripped}")

    if verbose:
        print(f"\n  检查 {checked} 个文件")

    if violations:
        print("❌ 发现双导入反模式（scripts/ 无 __init__.py，不应以包形式导入）：")
        for v in violations:
            print("  " + v)
        return 1

    print("[OK] 无双导入反模式")
    return 0


if __name__ == "__main__":
    sys.exit(main())
