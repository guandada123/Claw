#!/usr/bin/env python3
"""silent_stub_scan.py — 扫「看起来已实现、实则空转」的桩函数（跨仓库审计）。

━━ 为什么需要（真实教训，不是假想）━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
2026-10-10/11 两次在**不同项目**撞到同一类缺陷：

  · QTS `advanced_risk_rules._check_new_high_after_sell`：函数体只有一行
    `# TODO: 接入行情数据后实现`，且**调用方把返回值丢掉了** →
    `sell_cooldown_new_high_days` 这个配置项**从未生效**，但通读代码完全看不出
    （发现回路 disc-20261010-03 / disc-20261011-02 各报了一次 —— 连发现模块
    自己都把它报了两遍）。
  · 同类形态的通用危害：**被调用 + 什么都不做 + 返回值被忽略** ⇒
    「功能在跑」的假象。它比崩溃危险得多：崩溃会响，这个不会。

━━ 判据（刻意保守，只报"高置信度"的）━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
命中「trivial body」之一：
  ① 去掉 docstring 后**无语句**
  ② 只剩 `pass`
  ③ 只剩 `...`（Ellipsis，TypedDict/Protocol 的合法写法）
  ④ 只剩一行裸注释（`# TODO:` / `# FIXME:` / `# XXX:` 也算）—— 就是 QTS 那例

**排除**（避免噪音，宁可漏报）：
  · `@abstractmethod` / `@abc.abstractmethod` / `@overload` / `@property`+abstract
  · 类名为 Protocol/ABC 且方法体是 `...`（接口声明，合法）
  · 名字以 `_abc_` 开头、或文件是 `.pyi`
  · 测试文件（tests/ 下）里的 fixture 桩 —— 单列但**不计入主要风险**

**分级**（决定要不要人看）：
  🔴 **被调用且忽略返回值** —— 最高危：在跑、什么都不做、无人知（QTS 那例的形态）
  🟠 被调用（有 call site）
  🟡 无 call site（可能是预留接口 / 回调占位）

退出码：0=无 🔴 / 1=有 🔴 / 2=无法判定（路径不可读）。

用法：
  python3 silent_stub_scan.py --root <repo> [--root <repo> ...] [--json] [--only-red]
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from pathlib import Path

SKIP_DIRS = {".venv", "venv", "node_modules", "__pycache__", ".git", "dist", "build", ".mypy_cache"}
BARE_TODO_RE = re.compile(r"^\s*#\s*(TODO|FIXME|XXX)\b", re.IGNORECASE)
# 显式豁免（有意为之的桩：空对象 close、模板方法基类、迁移的空 downgrade …）。
# 门禁化时必须给逃生口，否则一次误报就会被整体关掉（noqa 一类的粗粒度开关拦不住"只关这一条"）。
IGNORE_RE = re.compile(r"#\s*(stub-scan\s*:\s*(ok|ignore)|noqa\s*:\s*stub)", re.IGNORECASE)
EXEMPT_DECOS = {"abstractmethod", "overload", "abstractproperty"}
# 「忽略返回值」的判定：函数名在调用点出现（含 `self.foo(` / `obj.foo(` 形态）。
# ⚠️ 2026-10-11 修：原用 `(?<![\w.])` 把 `.` 也排除了 → **`self._xxx(` 形式的调用全部漏计**，
#    于是"被调用"判不出来、高危级被降级成🟡（拿已知缺陷回归时当场发现）。
#    现在允许 `.` 在前，代价是定义处 `def foo(` 也会命中 → 计数时减掉。
CALL_RE = re.compile(r"(?<![\w]){name}\s*\(")
DEF_RE = re.compile(r"def\s+{name}\s*\(")


def _func_block(src_lines: list[str], node: ast.AST) -> list[str]:
    """取函数的**文本块**（含尾随注释）。

    ⚠️ 2026-10-11 修：原用 `src_lines[lineno-1:end_lineno]` —— 注释不是 AST 节点，
    `end_lineno` **不含**函数末尾的 `# TODO` 行 → 那类桩被判成 `empty` 而不是 `bare_comment`
    （拿已知缺陷回归时当场发现）。改为按缩进向后吃到缩进回落为止。
    """
    start = node.lineno - 1
    first = src_lines[start] if start < len(src_lines) else ""
    indent = len(first) - len(first.lstrip())
    out = [first]
    i = start + 1
    while i < len(src_lines):
        ln = src_lines[i]
        if not ln.strip():
            out.append(ln)
            i += 1
            continue
        cur = len(ln) - len(ln.lstrip())
        if cur <= indent:
            break
        out.append(ln)
        i += 1
    return out


def _deco_names(node: ast.AST) -> set[str]:
    out = set()
    for d in getattr(node, "decorator_list", []) or []:
        if isinstance(d, ast.Name):
            out.add(d.id)
        elif isinstance(d, ast.Attribute):
            out.add(d.attr)
        elif isinstance(d, ast.Call):
            f = d.func
            out.add(f.id if isinstance(f, ast.Name) else getattr(f, "attr", ""))
    return out


def _body_kind(node: ast.FunctionDef | ast.AsyncFunctionDef, src_lines: list[str]) -> str | None:
    """返回 trivial 类型（'empty'|'pass'|'ellipsis'|'bare_comment'|'not_implemented'），否则 None。"""
    block = _func_block(src_lines, node)
    body = list(node.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)  # docstring
    ):
        body = body[1:]
    if any(IGNORE_RE.search(ln) for ln in block):
        return None  # 显式豁免（见 IGNORE_RE 注释）
    has_bare_todo = any(BARE_TODO_RE.match(ln) for ln in block)
    if not body:
        return "bare_comment" if has_bare_todo else "empty"
    if len(body) == 1:
        only = body[0]
        # 只有 `raise NotImplementedError`（或其子类）—— 显式未实现，也是桩
        if isinstance(only, ast.Raise) and only.exc is not None:
            exc = only.exc
            name = exc.id if isinstance(exc, ast.Name) else getattr(exc, "attr", "")
            if "NotImplemented" in name:
                return "not_implemented"
        if isinstance(only, ast.Pass):
            return "bare_comment" if has_bare_todo else "pass"
        if (
            isinstance(only, ast.Expr)
            and isinstance(only.value, ast.Constant)
            and only.value.value is Ellipsis
        ):
            return "ellipsis"
    return None


def _iter_funcs(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def scan_file(path: Path, repo_text_cache: dict, is_test: bool) -> list[dict]:
    try:
        src = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    lines = src.splitlines()
    parent_protocol = set()
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        bases = {getattr(b, "id", "") or getattr(b, "attr", "") for b in cls.bases}
        if bases & {"Protocol", "ABC"}:
            parent_protocol.update(
                m.name for m in cls.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
            )
    out = []
    for fn in _iter_funcs(tree):
        kind = _body_kind(fn, lines)
        if kind is None:
            continue
        decos = _deco_names(fn)
        if decos & EXEMPT_DECOS:
            continue
        if kind == "ellipsis" and (fn.name in parent_protocol or path.suffix == ".pyi"):
            continue
        out.append(
            {
                "file": str(path),
                "line": fn.lineno,
                "name": fn.name,
                "kind": kind,
                "is_test": is_test,
                "decorated": sorted(decos),
            }
        )
    return out


def count_calls(name: str, repo_root: Path, cache: dict) -> int:
    """跨仓文本级 call site 计数（够用且便宜；AST 级跨文件解析成本不值得）。"""
    pat = CALL_RE.pattern.format(name=re.escape(name))
    dpat = DEF_RE.pattern.format(name=re.escape(name))
    rx = re.compile(pat)
    drx = re.compile(dpat)
    n = 0
    for txt in cache.values():
        n += len(rx.findall(txt)) - len(drx.findall(txt))  # 减掉定义处
    return max(n, 0)


def scan_repo(root: Path) -> list[dict]:
    cache: dict = {}
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn.endswith((".py", ".pyi")):
                files.append(Path(dirpath) / fn)
    for f in files:
        try:
            cache[f] = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
    found: list[dict] = []
    for f in files:
        is_test = "tests" in f.parts or f.name.startswith("test_")
        found.extend(scan_file(f, cache, is_test))
    for r in found:
        r["calls"] = count_calls(r["name"], root, cache)
        r["severity"] = (
            "🔴"
            if (r["calls"] > 0 and r["kind"] == "bare_comment")
            else ("🟠" if r["calls"] > 0 else "🟡")
        )
    return found


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", action="append", required=True, help="仓库根（可多次）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--only-red", action="store_true", help="只输出 🔴")
    args = ap.parse_args(argv[1:])

    report = {}
    for r in args.root:
        rp = Path(r).expanduser().resolve()
        if not rp.is_dir():
            print(f"[stub-scan] 无法判定：{rp} 不存在", file=sys.stderr)
            return 2
        report[rp.name] = scan_repo(rp)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        reds = 0
        for repo, items in report.items():
            keep = [
                i
                for i in items
                if not i["is_test"] and (i["severity"] == "🔴" if args.only_red else True)
            ]
            print(f"\n══ {repo}：{len(keep)} 处（非测试）'trivial body' 函数")
            for i in sorted(keep, key=lambda x: (x["severity"] != "🔴", -x["calls"])):
                rel = Path(i["file"]).name
                print(
                    f"  {i['severity']} {rel}:{i['line']}  {i['name']}()  kind={i['kind']}  calls={i['calls']}"
                )
                if i["severity"] == "🔴":
                    reds += 1
        print(f"\n🔴 = 被调用 + 体是裸注释（**在跑却什么都不做**，最高危）：{reds} 处")
    reds = sum(
        1 for items in report.values() for i in items if i["severity"] == "🔴" and not i["is_test"]
    )
    return 1 if reds else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
