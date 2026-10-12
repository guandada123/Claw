#!/usr/bin/env python3
""".workbuddy/scripts/check_tracked_tree.py — 在「只有 tracked 文件」的树里预跑门禁，防「本地绿、CI 红」

━━ 为什么（2026-10-11 一天内踩了三次同类）━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ① 新门禁把**运行时产物**当输入查存在性 → 本机有（跑过自动化）、干净检出没有 → **本地永远绿、CI 永远红**；
  ② 我写的说明文字里引用了 runner 的绝对路径 → 被封闭性门禁判红（本地不报，因为本地那个路径存在）；
  ③ 测试里写死 `/Users/guan/...` → CI collection FileNotFoundError（这条早有铁律，仍复发过）。
规则层**早已写明**「推送前在 `git clone --local` 的『只有 tracked 文件』树里跑一遍」，
但一直**没有对应工具** —— 本条把那条规则变成可执行命令。

━━ 判据 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
把临时 clone 当「CI 替身」：clone 只含 tracked 文件（等价于 CI 的 checkout），在里面跑门禁。
  全部通过 → rc=0｜任一失败 → rc=1｜**克隆或执行不了 → rc=2**（判不了就说判不了，不静默放过）

━━ 用法 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  python3 .workbuddy/scripts/check_tracked_tree.py                 # 快档：结构门禁 + ruff
  python3 .workbuddy/scripts/check_tracked_tree.py --full          # 全档：另跑 tests/ 与 .workbuddy/tests/
  python3 .workbuddy/scripts/check_tracked_tree.py --keep          # 保留克隆目录以便排查
  推荐接入：`make ci-tracked`（推前跑）/ `make ci-tracked-full`（改动大时）

━━ 注意 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- 克隆默认落在**仓库同卷的临时目录**（同卷才能用 `git clone --local` 的硬链接提速）；
  跨卷时自动退化为 `--no-hardlinks`。清理用 Python 的 `shutil.rmtree`（**不要在 shell 里 `rm -rf`**：
  本仓 safe-delete 护栏会拦「一次删 >50 个文件」，而克隆里必然远超 50 个）。
- 本工具**不做覆盖率门禁**（阈值权威在 CI 的 per-job `--cov-fail-under`）；它只回答
  「这棵树在干净检出下还能不能跑通」。
"""

from __future__ import annotations

import argparse
import os  # P1-12：env-first 的工具路径需要
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent

# 结构门禁（快档）：只挑「读文件/读路径」的判据 —— 正是本地绿/CI 红的高发区
GUARD_TESTS = [
    "tests/test_ci_hermeticity.py",
    "tests/test_path_constants_resolve.py",
    "tests/test_doc_volatile_claims.py",
    "tests/test_py39_compat.py",
    "tests/test_secret_scan_selfcheck.py",
    "tests/test_memory_headroom_check.py",
]
RUFF_TARGETS = [".workbuddy/scripts", ".workbuddy/lib", "tests", "src/claw"]


def pick_python() -> str | None:
    """挑一个**真的装了 pytest** 的解释器。

    本机实测（2026-10-11）：默认 `python3` 是 3.13 但**没有 pytest** → 直接用它跑门禁会得到
    「假失败」（不是代码有问题，是解释器选错）；而 `.venv` 与托管默认都有。
    优先级：仓库 `.venv`（与 CI 最接近）→ 托管默认 → 系统 `/usr/bin/python3`（3.9，本仓亦需支持）。
    """
    import importlib.util as _ilu

    candidates = [
        Path(__file__).resolve().parent.parent.parent / ".venv" / "bin" / "python3",
        Path(
            os.environ.get("PYTHON")
            or str(
                Path.home() / ".workbuddy" / "binaries" / "python",
                "envs",
                "default",
                "bin",
                "python3",
            )
        ),
        Path("/usr/bin/python3"),
    ]
    for c in candidates:
        if not c.is_file():
            continue
        r = subprocess.run([str(c), "-c", "import pytest"], capture_output=True, text=True)
        if r.returncode == 0:
            return str(c)
    _ = _ilu  # 保留导入以便将来按需扩展
    return None


def _run(cmd: list[str], cwd: Path) -> tuple[int, str]:
    r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def clone_tracked_tree(dest_parent: Path) -> tuple[Path | None, str]:
    """把一个只含 tracked 文件的 clone 建到 dest_parent 下。返回 (路径, 说明)。"""
    dest = Path(tempfile.mkdtemp(prefix=".claw-tracked-", dir=str(dest_parent)))
    target = dest / "repo"
    for extra, label in (("--local", "同卷硬链接"), ("--no-hardlinks", "跨卷复制")):
        rc, out = _run(["git", "clone", "--quiet", extra, str(REPO), str(target)], cwd=dest_parent)
        if rc == 0:
            return target, f"git clone {extra}（{label}）"
        if "--local" in extra:
            shutil.rmtree(target, ignore_errors=True)
    return None, f"克隆失败：{out[:200]}"


def main() -> int:
    ap = argparse.ArgumentParser(description="在 tracked-only 树里预跑门禁（防本地绿/CI 红）")
    ap.add_argument("--full", action="store_true", help="另跑 tests/ 与 .workbuddy/tests/")
    ap.add_argument("--keep", action="store_true", help="保留克隆目录")
    ap.add_argument(
        "--python", default=None, help="跑 pytest 的解释器（默认自动探测，见 pick_python）"
    )
    args = ap.parse_args()

    python = args.python or pick_python()
    if python is None:
        print(
            "[ERROR] 找不到可用 pytest 的解释器（试过 .venv / 托管默认 / 系统 python3）→ rc=2",
            file=sys.stderr,
        )
        return 2
    print(f"🐍 解释器：{python}")

    if not (REPO / ".git").exists():
        print(f"[ERROR] 找不到仓库：{REPO}", file=sys.stderr)
        return 2

    tree, how = clone_tracked_tree(REPO.parent)
    if tree is None:
        print(f"[ERROR] {how} → 无法判定（rc=2，不静默放过）", file=sys.stderr)
        return 2
    print(f"🌳 tracked-only 树：{tree}\n   {how}\n")

    failures: list[str] = []
    try:
        # ① 结构门禁
        present = [t for t in GUARD_TESTS if (tree / t).is_file()]
        missing = [t for t in GUARD_TESTS if t not in present]
        if present:
            rc, out = _run([python, "-m", "pytest", *present, "-q"], cwd=tree)
            tail = "\n".join(out.splitlines()[-12:])
            print(f"{'✅' if rc == 0 else '🔴'} 结构门禁 ({len(present)} 个文件)\n{tail}\n")
            if rc != 0:
                failures.append("结构门禁")
        if missing:
            # 门禁文件不在 clone 里 = 说明它本身没入库 → 必须说出来，否则「预跑」是假的
            failures.append(f"门禁文件未入库：{', '.join(missing)}")
            print(f"🔴 以下门禁文件不在 tracked 树里（未入库？）：{', '.join(missing)}\n")

        # ② ruff（与 CI 的 Lint 同判据源）
        rc, out = _run(["ruff", "check", *RUFF_TARGETS, "--config", "ruff.toml"], cwd=tree)
        print(f"{'✅' if rc == 0 else '🔴'} ruff check\n{chr(10).join(out.splitlines()[-8:])}\n")
        if rc != 0:
            failures.append("ruff check")

        # ③ 全档：跑两套测试
        if args.full:
            for sub in ("tests", ".workbuddy/tests"):
                if not (tree / sub).is_dir():
                    continue
                rc, out = _run([python, "-m", "pytest", sub, "-q"], cwd=tree)
                tail = "\n".join(out.splitlines()[-10:])
                print(f"{'✅' if rc == 0 else '🔴'} pytest {sub}\n{tail}\n")
                if rc != 0:
                    failures.append(f"pytest {sub}")
    finally:
        if args.keep:
            print(f"📌 --keep：克隆保留在 {tree}（排查完请自行清理）")
        else:
            shutil.rmtree(tree.parent, ignore_errors=True)

    if failures:
        print(
            "🔴 tracked-only 树里失败："
            + "、".join(failures)
            + "\n   → 这些正是「本地绿、CI 红」的典型：本机有而干净检出没有的东西（产物/绝对路径/未入库文件）。"
        )
        return 1
    print(
        "✅ tracked-only 树预跑全部通过 —— 推送后 CI 大概率同结论（本工具不替代 CI 的覆盖率门禁）"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
