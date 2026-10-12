#!/usr/bin/env python3
"""migrate_only_stale.py — 「仅旧侧存在」文件的单源化（迁权威侧 + 旧侧留位置无关转发壳）。

## 为什么需要它（与既有两件工具的分工，勿重造）
本仓单源化共三种形态，此前只有前两种有工具：
  1. `safe_dedup.py`        —— **双侧都存在**（碰撞）：收敛为壳并跑双闸门
  2. `restore_forwarders.py`—— **权威侧有、旧侧缺**（漏壳）：补回转发壳
  3. **本工具**              —— **仅旧侧存在**：把真身迁到权威侧，旧侧留壳   ← 此前缺这一环

（2026-10-12 P1-14 收口时，第 3 类是我用临时脚本 + 手工差分门禁做的；本工具把它固化，
 避免下次再写一遍，也避免"临时脚本不带闸门"。）

## 安全设计（沿用 safe_dedup 的同一套铁律）
 ① **默认 dry-run**，`--apply` 才落盘；
 ② 迁前必须 `cp` 备份到 `--backup-dir`（默认 `/tmp/migrate_only_stale_<date>/`）；
 ③ 旧侧改写为**位置无关转发壳**（复用 `safe_dedup.FORWARDER_TPL`，**绝不用软链**）；
 ④ **差分门禁**：对每个文件，比较"迁移前 / 迁移后"经旧侧路径 import 的结果 ——
    只有这样才是**不会冤枉既有问题**的判据（实测：`bt_deep_analysis.py`、`wind_monitor.py`
    迁移前就 `ModuleNotFoundError`，那是既有条件，不该阻断本次迁移）；
    无 `__main__` 守卫的文件**不做 import 冒烟**（顶层代码会执行，可能有副作用），改走语法检查；
 ⑤ 任一回归 → **全量回滚**（旧侧还原 + 权威侧删除）并 exit 1；
 ⑥ `--only a.py,b.py` 可限定范围；`--list` 只列候选。

## 用法
```bash
$PY .workbuddy/scripts/migrate_only_stale.py                 # dry-run：列出候选
$PY .workbuddy/scripts/migrate_only_stale.py --apply         # 执行（备份+壳+差分门禁+失败回滚）
$PY .workbuddy/scripts/migrate_only_stale.py --apply --only a.py,b.py
```
迁完请照例跑：`pytest tests/ -q`（含双嵌套守卫与硬编码门禁）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]  # <repo>/.workbuddy/scripts/x.py → <repo>
STALE = REPO / "scripts"
AUTH = REPO / ".workbuddy" / "scripts"

sys.path.insert(0, str(AUTH))
try:
    from safe_dedup import FORWARDER_TPL, SKILL_RULE  # 模板**单一真源**，勿在本文件复制一份
except Exception as exc:  # pragma: no cover - 仅在工具被搬离原位时发生
    raise SystemExit(f"✗ 无法从 safe_dedup 导入转发壳模板（模板须单源）：{exc}") from exc


def candidates(only: set[str] | None = None) -> list[Path]:
    """仅旧侧存在（权威侧无同名）且当前**还不是转发壳**的文件。"""
    have = {p.name for p in AUTH.glob("*.py")}
    out = []
    for p in sorted(STALE.glob("*.py")):
        if p.name in have:
            continue
        if only and p.name not in only:
            continue
        if "AUTO-GENERATED FORWARDER" in p.read_text(encoding="utf-8", errors="ignore")[:600]:
            continue  # 已是壳（其真身在别处，需人工核对）
        out.append(p)
    return out


def _guarded(text: str) -> bool:
    return "__name__" in text and "__main__" in text


def _smoke(mod: str) -> tuple[bool, str]:
    """经**旧侧路径** import 一次；返回 (是否成功, 错误尾行)。"""
    r = subprocess.run(
        [sys.executable, "-c", f"import sys; sys.path.insert(0, {str(STALE)!r}); import {mod}"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if r.returncode == 0:
        return True, ""
    tail = (r.stderr.strip().splitlines() or ["import failed"])[-1]
    return False, tail[:90]


def _syntax_ok(path: Path) -> bool:
    return (
        subprocess.run(
            [sys.executable, "-m", "py_compile", str(path)], capture_output=True
        ).returncode
        == 0
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="仅旧侧文件的单源化（迁权威侧 + 留转发壳）")
    ap.add_argument("--apply", action="store_true", help="真实执行（默认 dry-run）")
    ap.add_argument("--only", default="", help="逗号分隔的文件名，限定范围")
    ap.add_argument("--list", action="store_true", help="只列候选")
    ap.add_argument(
        "--backup-dir", default="", help="备份目录（默认 /tmp/migrate_only_stale_<date>）"
    )
    args = ap.parse_args()

    only = {x.strip() for x in args.only.split(",") if x.strip()} or None
    tg = candidates(only)
    print(f"候选（仅旧侧存在且非壳）：{len(tg)} 个")
    for p in tg[:20]:
        print("  ·", p.name)
    if len(tg) > 20:
        print(f"  … 另有 {len(tg) - 20} 个")
    if args.list or not tg:
        return 0
    if not args.apply:
        print("\n（dry-run：确认无误后加 --apply 执行；执行会自动备份 + 跑差分门禁 + 失败回滚）")
        return 0

    backup = (
        Path(args.backup_dir)
        if args.backup_dir
        else Path(f"/tmp/migrate_only_stale_{dt.date.today():%Y%m%d}")
    )
    backup.mkdir(parents=True, exist_ok=True)
    print(f"备份目录：{backup}")

    # ── 差分门禁的**基线**：迁移前先量一次（不通过者视为既有条件，不阻断） ──
    baseline: dict[str, bool] = {}
    for src in tg:
        txt = src.read_text(encoding="utf-8", errors="ignore")
        baseline[src.name] = _smoke(src.stem)[0] if _guarded(txt) else True
    pre_bad = sorted(n for n, ok in baseline.items() if not ok)
    if pre_bad:
        print(f"ℹ️ 迁移前即不可导入（记录为既有条件，不阻断）：{', '.join(pre_bad)}")

    done: list[Path] = []
    for src in tg:
        dst = AUTH / src.name
        if dst.exists():
            print(f"  ⚠️ 权威侧已存在，跳过：{src.name}")
            continue
        shutil.copy2(src, backup / src.name)  # ① 备份
        shutil.copy2(src, dst)  # ② 真身落权威侧
        src.write_text(
            FORWARDER_TPL.format(name=src.stem, rule=SKILL_RULE), encoding="utf-8"
        )  # ③ 旧侧留壳
        done.append(src)
    print(f"已迁移 {len(done)} 个，开始差分门禁…")

    fails: list[str] = []
    for src in done:
        if not baseline.get(src.name, True):
            print(f"    · {src.name}: 迁移前后均不可导入（既有条件，未计入失败）")
            continue
        txt = (backup / src.name).read_text(encoding="utf-8", errors="ignore")
        if not _guarded(txt):
            if not _syntax_ok(AUTH / src.name):
                fails.append(f"{src.name}: 语法检查失败（无 __main__ 守卫，未做导入冒烟）")
            else:
                print(f"    · {src.name}: 无守卫 → 语法检查通过（未 import，避免副作用）")
            continue
        ok, err = _smoke(src.stem)
        if not ok:
            fails.append(f"{src.name}: {err}")

    if fails:
        print("  🔴 差分门禁失败：")
        for f in fails:
            print("    ", f)
        for src in done:  # ⑤ 全量回滚
            shutil.copy2(backup / src.name, STALE / src.name)
            (AUTH / src.name).unlink(missing_ok=True)
        print("  ↩️ 已全量回滚，未留下半成品")
        return 1
    print(f"  ✅ 差分门禁通过（{len(done)} 个）")
    print("  下一步：pytest tests/ -q（含双嵌套守卫 + 硬编码门禁）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
