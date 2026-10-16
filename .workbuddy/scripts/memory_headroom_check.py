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
    # 🔴 2026-10-14 新增：**实际被注入**的用户级记忆（平台 Layer 2）。旧路径
    #   `~/.workbuddy/MEMORY.md` 自 10-11 起已冻结、不再被任何注入消费 ——
    #   只查旧路径会给出「余量 599B 🟢」的**假绿灯**（实测被注入那份余量≈0B）。
    #   上限取 check_memory_visibility.DEFAULT_LIMITS 里 2026-10-14 实测的 7,913B。
    Path.home() / ".workbuddy" / "user-24688a34-03ff-4299-8736-08d5a6585c6b-personal" / "MEMORY.md",
]

# 日日志只存在于真正的 memory 目录。**不要从 TARGETS 父目录派生**：三份 MEMORY.md 的
# 父目录是 ~/.workbuddy、~/.workbuddy/user-...-personal、Claw/.workbuddy/memory，前两处
# 永远没有日日志 → 派生会多出两行「日日志 0 个」恒零噪音，很容易被读成「日志丢了」。
LOG_DIRS = [
    Path.home() / "WorkBuddy" / "Claw" / ".workbuddy" / "memory",
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


DAILY_LOG_MIN_SKEW_DAYS = 7
DAILY_LOG_KNOWN_FILE = ".daily_log_reflux_known.json"
"""已核实的回灌清单（同目录）。登记过的只报一行计数，避免恒真噪音。"""
"""日日志「被复制/恢复进来」的 ctime−mtime 最小间隔（天）。

判据由来（2026-10-11 实况）：10-10 20:50 有 11 个 2026-06-21~07-01 的日日志被静默放回
`memory/`，**同期没有任何自动化运行**，也没有任何日志记录该动作 → 只能靠时间戳识别：
「复制/恢复且保留 mtime」会让 **mtime 停在原值、ctime 变成操作时刻**，两者相差数月；
正常写入时 ctime≈mtime。这是「被动过」的机器可判证据。
"""


def _load_known_reflux(mem_dir: Path) -> set[str]:
    """已知（已核实）的回灌文件名集合；文件缺失/坏 JSON = 空集（不当成豁免）。"""
    f = mem_dir / DAILY_LOG_KNOWN_FILE
    if not f.is_file():
        return set()
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    return {
        str(e.get("file"))
        for e in data.get("entries", [])
        if e.get("file") and e.get("recorded_at")
    }


def check_daily_log_anomalies(
    mem_dirs: list[Path], min_skew_days: int = DAILY_LOG_MIN_SKEW_DAYS
) -> list[dict]:
    """日日志卫生：个数/最早/最新 + 「疑似被复制或恢复进来」的清单。

    ⚠️ 本判据**只报不拦**（不进 rc）：被回灌的内容经核实是**独有历史**（不是重复），
    正确处置是「知道它来了」；rc 仍由可见余量决定。若哪天变成真缺陷，再升级为拦。
    """
    import datetime as _dt
    import re as _re

    out: list[dict] = []
    for d in mem_dirs:
        if not d.is_dir():
            continue
        files = sorted(
            p for p in d.glob("2026-*-*.md") if _re.match(r"^\d{4}-\d\d-\d\d\.md$", p.name)
        )
        refluxed = []
        for p in files:
            st = p.stat()
            skew_days = (st.st_ctime - st.st_mtime) / 86400
            if skew_days >= min_skew_days:
                refluxed.append(
                    {
                        "file": p.name,
                        "skew_days": round(skew_days, 1),
                        "mtime": _dt.date.fromtimestamp(st.st_mtime).isoformat(),
                        "ctime": _dt.date.fromtimestamp(st.st_ctime).isoformat(),
                    }
                )
        known = _load_known_reflux(d)
        new_reflux = [r for r in refluxed if r["file"] not in known]
        out.append(
            {
                "dir": str(d),
                "count": len(files),
                "oldest": files[0].name if files else None,
                "newest": files[-1].name if files else None,
                "refluxed": new_reflux,
                "refluxed_known": len(refluxed) - len(new_reflux),
            }
        )
    return out


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
        # 物理口径（2026-10-17 新增，只作信息、不参与 rc）：
        #   余量的物理约束不是 500B 这个人为阈值，而是「最长的那一条规则有多长」——
        #   余量 ≥ 最长规则 ⇒ 任何**单条**新规则都不会把尾部挤出可见区。
        #   与阈值是两件事，故分别给出，避免用一个判据同时答两件事（Step 0.30）。
        longest_rule = info.get("longest_rule")
        rows.append(
            {
                "file": str(f),
                "limit": limit,
                "rule_off": rule_off,
                "headroom": headroom,
                "rules": info.get("rules"),
                "longest_rule": longest_rule,
                "physical_ok": (
                    None
                    if not isinstance(longest_rule, int) or longest_rule <= 0
                    else headroom >= longest_rule
                ),
                "status": "ok" if headroom >= threshold else "low",
                "existing_problems": list(problems),
            }
        )
    return rows, errors


def check_wired(
    needle: str = "memory_headroom_check.py", db: Path | None = None
) -> tuple[bool, str]:
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
    ap.add_argument(
        "--threshold", type=int, default=DEFAULT_THRESHOLD, help="可见余量告警阈值（字节）"
    )
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
    daily = check_daily_log_anomalies(LOG_DIRS)

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
                    "daily_logs": daily,
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
            phys = r.get("physical_ok")
            phys_txt = (
                ""
                if not isinstance(r.get("longest_rule"), int) or r["longest_rule"] <= 0
                else (
                    f" | 最长规则 {r['longest_rule']}B | 物理口径 "
                    + ("✅ 余量≥最长规则" if phys else "⚠️ 余量<最长规则（再写长规则会溢出）")
                )
            )
            print(
                f"{mark} {r['file']}\n"
                f"     上限 {r['limit']}B | 规则止 {r['rule_off']}B | "
                f"**可见余量 {r['headroom']}B** | 规则 {r['rules']} 条" + phys_txt
            )
        for d in daily:
            print(
                "📅 日日志 {n} 个（最早 {old} / 最新 {new}） ← {dir}".format(
                    n=d["count"], old=d["oldest"], new=d["newest"], dir=d["dir"]
                )
            )
            if d.get("refluxed_known"):
                print(
                    "   ℹ️ 另有 {} 个已知（已核实为独有历史）的回灌文件，不重复报（见 {}/{}）".format(
                        d["refluxed_known"], d["dir"], DAILY_LOG_KNOWN_FILE
                    )
                )
            if d["refluxed"]:
                items = ", ".join(
                    "{f}(+{s:.0f}天 {m}→{c})".format(
                        f=x["file"], s=x["skew_days"], m=x["mtime"], c=x["ctime"]
                    )
                    for x in d["refluxed"][:12]
                )
                print(
                    "   ⚠️ 新发现「疑似被复制/恢复进来」的 {n} 个（ctime 远晚于 mtime；正常写入时两者接近）：{items}".format(
                        n=len(d["refluxed"]), items=items
                    )
                )
                print(
                    "     处置：先核实内容是否为独有历史（对照 CHRONICLE.md 日期段），"
                    "**不要直接删**（删/移走「先复制→验证→再删源」）；再查备份/恢复链路。"
                )
        for e in errors:
            print(f"🔴 {e}")
        if low:
            phys_all_ok = all(r.get("physical_ok") is True for r in low)
            print(
                f"\n🔴 {len(low)} 个文件可见余量 < {args.threshold}B —— "
                "再加铁律就会把末尾规则挤出可见区（写进去=模型读不到=白写）。"
                "处置：蒸馏/合并规则，或上调该文件的实测上限口径。"
            )
            if phys_all_ok:
                print(
                    "   ℹ️ 物理口径复核：这些文件的余量 ≥ 各自最长单条规则 → "
                    "**再加 1 条规则不会溢出**；本条红是**阈值口径**问题"
                    "（500B vs 技能 Step 0.20 验收线 300B），非物理约束被突破。"
                )
        if args.wired:
            print(f"{'🟢' if wired_ok else '🔴'} 接线自检: {wired_detail}")

    if errors or not wired_ok:
        return 2
    return 1 if low else 0


if __name__ == "__main__":
    sys.exit(main())
