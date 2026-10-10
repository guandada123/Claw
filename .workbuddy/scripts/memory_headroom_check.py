#!/usr/bin/env python3
"""memory_headroom_check.py — 记忆文件「可见余量」守卫

━━ 为什么需要（2026-10-10 取证）━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
`~/.workbuddy/scripts/check_memory_visibility.py` 会算出每个 MEMORY.md 的
`上限 / 索引止 / 规则止`，但**没有任何自动化调用它**（实测全平台 0 处引用）；
而每日 03:30 的记忆维护自动化只校验「**索引段**是否落在可见区内」，
**从不校验「规则层是否全部可见」**。

后果：用户级 `MEMORY.md` 实测 上限 7,353B / 规则止 6,994B →
**可见余量只剩 ~359B**，而两边都报「通过」。等哪天再加几条铁律，
末尾那些规则就会**静默滑出可见区**（写进去 = 模型读不到 = 白写），
且没有任何一处会因此报警。

本脚本把「余量」变成一个可机器判定的数，并给出阈值告警。

━━ 用法 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  python3 memory_headroom_check.py                 # 默认阈值 500B
  python3 memory_headroom_check.py --threshold 800
  python3 memory_headroom_check.py --json

退出码：0=余量充足 / 1=余量不足（需蒸馏或调整） / 2=检查器不可用或结构解析不出（**不静默放过**）
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

CHECKER = Path.home() / ".workbuddy" / "scripts" / "check_memory_visibility.py"

TARGETS = [
    Path.home() / ".workbuddy" / "MEMORY.md",
    Path.home() / "WorkBuddy" / "Claw" / ".workbuddy" / "memory" / "MEMORY.md",
]

DEFAULT_LIMIT = 7353
DEFAULT_THRESHOLD = 500


def load_checker(path: Path | None = None):
    """从绝对路径加载检查器；不可用则返回 None（调用方须据此报 rc=2，不得静默通过）。

    注意 `path` 默认值必须在**调用时**取模块级 CHECKER，不能写成 `path: Path = CHECKER`
    —— 后者在函数定义时就把值绑死了，改 `module.CHECKER` 不生效（默认参数绑定陷阱，
    与可变默认参数同族），会让「检查器路径可替换」这件事变成假的。
    """
    path = path or CHECKER
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("_mem_visibility", str(path))
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:  # noqa: BLE001 — 检查器自身坏掉时必须能被上层看见
        return None
    return mod if hasattr(mod, "check") else None


def evaluate(mod, targets: list[Path], threshold: int) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    errors: list[str] = []
    for f in targets:
        if not f.is_file():
            rows.append({"file": str(f), "skipped": "文件不存在"})
            continue
        limit = (getattr(mod, "DEFAULT_LIMITS", {}) or {}).get(str(f), DEFAULT_LIMIT)
        try:
            problems, info = mod.check(str(f), limit)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{f}: check() 抛错 {e.__class__.__name__}: {e}")
            continue
        rule_off = info.get("rule_off")
        if not isinstance(rule_off, int):
            # 结构没解析出「规则层结束偏移」→ 无法判定余量。**不当作通过**。
            errors.append(f"{f}: 解析不出 rule_off（结构可能变了），无法判定可见余量")
            continue
        headroom = limit - rule_off
        rows.append(
            {
                "file": str(f),
                "limit": limit,
                "rule_off": rule_off,
                "headroom": headroom,
                "rules": info.get("rules"),
                "status": "ok" if headroom >= threshold else "low",
                "existing_problems": list(problems),
            }
        )
    return rows, errors


def check_wired(needle: str = "memory_headroom_check.py", db: Path | None = None) -> tuple[bool, str]:
    """验证「本检查真的被某个 ACTIVE 自动化调用」——即「已配置 ≠ 被读取」那一条。

    只交付脚本而不接线，等于又造一个零调用的摆设（正是本脚本要修的病）。
    查库放在 Python 里做，避免把 SQL + 引号塞进 shell 判据里。
    """
    import sqlite3

    db = db or (Path.home() / ".workbuddy" / "workbuddy.db")
    if not db.is_file():
        return False, f"自动化库不存在: {db}"
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = con.execute(
            "SELECT name FROM automations "
            "WHERE deleted_at IS NULL AND status='ACTIVE' AND prompt LIKE ?",
            (f"%{needle}%",),
        ).fetchall()
        con.close()
    except Exception as e:  # noqa: BLE001 — 查不动要说出来，不能默认通过
        return False, f"查库失败 {e.__class__.__name__}: {e}"
    if not rows:
        return False, f"没有任何 ACTIVE 自动化引用 {needle}（脚本交付了但没接线）"
    return True, "已接线: " + ", ".join(r[0] for r in rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD, help="可见余量告警阈值（字节）")
    ap.add_argument("--wired", action="store_true", help="额外验证本检查已被 ACTIVE 自动化调用")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    mod = load_checker()
    if mod is None:
        print(
            f"[ERROR] 检查器不可用或无法加载: {CHECKER}\n"
            "        → 无法判定可见余量。按设计**不静默放过**（rc=2）。",
            file=sys.stderr,
        )
        return 2

    rows, errors = evaluate(mod, TARGETS, args.threshold)
    low = [r for r in rows if r.get("status") == "low"]

    wired_ok, wired_detail = (True, "未检查（未加 --wired）")
    if args.wired:
        wired_ok, wired_detail = check_wired()

    if args.json:
        print(
            json.dumps(
                {
                    "threshold": args.threshold,
                    "rows": rows,
                    "errors": errors,
                    "low_count": len(low),
                    "wired_ok": wired_ok,
                    "wired_detail": wired_detail,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for r in rows:
            if "skipped" in r:
                print(f"⚪ {r['file']} → {r['skipped']}")
                continue
            mark = "🟢" if r["status"] == "ok" else "🔴"
            print(
                f"{mark} {r['file']}\n"
                f"     上限 {r['limit']}B | 规则止 {r['rule_off']}B | "
                f"**可见余量 {r['headroom']}B** | 规则 {r['rules']} 条"
            )
        for e in errors:
            print(f"🔴 {e}")
        if low:
            print(
                f"\n🔴 {len(low)} 个文件可见余量 < {args.threshold}B —— "
                "再加铁律就会把末尾规则挤出可见区（写进去=模型读不到=白写）。"
                "处置：蒸馏/合并规则，或上调该文件的实测上限口径。"
            )
        if args.wired:
            print(f"{'🟢' if wired_ok else '🔴'} 接线自检: {wired_detail}")

    if errors or not wired_ok:
        return 2
    return 1 if low else 0


if __name__ == "__main__":
    sys.exit(main())
