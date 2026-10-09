#!/usr/bin/env python3
"""automation_dispatch_audit.py — 自动化「派发对账」：抓出「活跃却从不派发」的自动化

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ 2026-10-09 重大更正（改本脚本前必读）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
首版只按 `status='ACTIVE'` 取数，**漏了 `deleted_at`**，于是把 20 行**早已软删除**的记录
误判成「平台侧不存在的幽灵行」，还为此加了一整套「已知幽灵行登记抑制」机制去绕开自己的 bug，
并把一个不存在的问题上报给用户。

真相：本机 automations 表用**软删除**语义 ——
    · **`deleted_at` 非空 = 已删除**（权威口径）
    · `status` 列在软删除后**不会被同步改写**，于是留下 `status='ACTIVE' AND deleted_at≠NULL` 的组合
    · 实测分布：ACTIVE 未删 71 / **ACTIVE 已删 20** / DELETED 已删 36 / PAUSED 未删 1 / PAUSED 已删 54
    · 口径与 `hub_reconcile.py` 一致：`live = rows if not deleted_at`（它报 observed_live=72✅）

**教训：判「一条自动化是否活着」，必须 `status` 与 `deleted_at` 一起看，只看其一必错。**
（同族铁律：『同一语义的值必须单一真源；「已配置」≠「被读取」』）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
为何仍需要本脚本（它要补的那个盲区是真的）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
`🛡️ 自动化失败扫表 watchdog` 只扫 `automation_runs.result_success=0`。
一个**从不派发**的自动化产生 **0 条运行记录** → 在失败扫表里结构性隐形。
这是本机最高频事故「上游定时任务失败 → 下游静默产出旧结论」的新变体：
**不是产出旧结论，而是根本没有上游。**

（当前实测发现 0 条 —— 这是正常结果，不是脚本没用。守护类检查长期为 0 才是常态。）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
判据
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
取数：`status='ACTIVE' AND deleted_at IS NULL`   ← 两个条件缺一不可

分类（顺序即优先级）：
  1) `next_run_at` 在未来  或  窗口内确有派发  → 🟢 健康（两信号互为兜底）
  2) once 且无 next_run_at（跑完清空）        → 🧹 已完成未清理
  3) 新建未满一个周期                          → 🕐 宽限（避免误报"刚建好还没首跑"）
  4) 其余                                      → 🔴 活跃却从不派发（**真问题**）

gap 阈值**按 FREQ 分级**（本机铁律：固定阈值会把"周跑"误判成"静默"）。
主判据是 **id** 在 `automation.log` 的 `run start` 事件 —— **禁用 name 比对**：
库里存在"旧行 + 新行"**同名并存**（如「Wind 深度研报（周日）」vs「…（周日 dry-run）」，
id 相邻、创建差 41 秒），按 name 比对会张冠李戴。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
用法
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  python3 automation_dispatch_audit.py            # 人读报告
  python3 automation_dispatch_audit.py --json     # 供其他脚本聚合
  python3 automation_dispatch_audit.py --days 14  # 覆盖最小窗口
  python3 automation_dispatch_audit.py --include-ok

退出码：0=无问题  1=有发现  2=环境异常
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sqlite3
import sys
from pathlib import Path

DB = Path.home() / ".workbuddy" / "workbuddy.db"
LOG_CANDIDATES = [
    Path("/Volumes/ZHITAI/workbuddy_data/logs/automation.log"),
    Path.home() / "WorkBuddy" / "Claw" / "logs" / "automation.log",
]

# ⚠️ 日志实际形态是 `...Scheduler] run start: id=`，`]` 与 `run` 之间是「] 」不是「: 」。
#    首版用 `": run start:" in line` 判类型 → **恒为 false** → 83/91 误判成僵尸。
#    必须由正则捕获组判类型。
EVENT_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})T[\d:.]+Z\s+\[INFO\]\s+\[LocalAutomationScheduler\]\s+"
    r"run (start|finished): id=([^,]+)"
)

# gap 阈值按频率分级（本机铁律）
FREQ_THRESHOLD_DAYS = {"HOURLY": 1, "DAILY": 3, "WEEKLY": 10, "MONTHLY": 40, "YEARLY": 400}
DEFAULT_THRESHOLD_DAYS = 3


def rrule_threshold(rrule: str | None, stype: str) -> int:
    if stype == "once":
        return 0
    m = re.search(r"FREQ=(\w+)", rrule or "")
    return FREQ_THRESHOLD_DAYS.get(m.group(1).upper() if m else "", DEFAULT_THRESHOLD_DAYS)


def pick_log(explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit)
        return p if p.is_file() else None
    for p in LOG_CANDIDATES:
        if p.is_file():
            return p
    return None


def scan_log(log: Path) -> dict:
    """一遍扫日志，产出 {id: {'starts': n, 'last': 'YYYY-MM-DD'}}"""
    out: dict = {}
    with open(log, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = EVENT_RE.match(line)
            if not m:
                continue
            day, kind, aid = m.group(1), m.group(2), m.group(3)
            rec = out.setdefault(aid, {"starts": 0, "last": None})
            if kind == "start":
                rec["starts"] += 1
            if rec["last"] is None or day > rec["last"]:
                rec["last"] = day
    return out


def audit(days: int, log: Path, include_ok: bool = False) -> dict:
    today = dt.date.today()

    events = scan_log(log)
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    # ★ 关键：status 与 deleted_at 必须一起判（软删除语义，口径同 hub_reconcile）
    rows = conn.execute(
        "SELECT id, name, schedule_type, next_run_at, created_at, cwds, rrule "
        "FROM automations WHERE status='ACTIVE' AND deleted_at IS NULL ORDER BY name"
    ).fetchall()
    n_all_active = conn.execute(
        "SELECT COUNT(*) FROM automations WHERE status='ACTIVE'").fetchone()[0]
    n_soft_deleted = conn.execute(
        "SELECT COUNT(*) FROM automations WHERE status='ACTIVE' AND deleted_at IS NOT NULL"
    ).fetchone()[0]
    conn.close()

    healthy, new_pending, done_uncleaned, zombies = [], [], [], []
    for aid, name, stype, nr, created, cwds, rrule in rows:
        ev = events.get(aid, {"starts": 0, "last": None})
        try:
            created_d = dt.datetime.fromtimestamp(created / 1000).date()
        except (TypeError, ValueError):
            created_d = None
        try:
            nr_d = dt.datetime.fromtimestamp(nr / 1000).date() if nr else None
        except (TypeError, ValueError):
            nr_d = None
        nr_future = bool(nr_d and nr_d >= today)
        thr = rrule_threshold(rrule, stype)
        fresh = bool(ev["starts"] > 0 and ev["last"]
                     and ev["last"] >= (today - dt.timedelta(days=thr)).isoformat())

        item = {
            "id": aid, "name": name, "schedule_type": stype, "rrule": rrule,
            "threshold_days": thr, "dispatches": ev["starts"], "last_dispatch": ev["last"],
            "next_run_at": nr_d.isoformat() if nr_d else None,
            "next_run_at_in_past": bool(nr_d and nr_d < today),
            "created_at": created_d.isoformat() if created_d else None, "cwds": cwds,
        }

        if nr_future or fresh:
            healthy.append(item)
            continue
        if stype == "once":
            item["reason"] = ("已执行完成，未清理" if ev["starts"] > 0 else "从未执行且已过期，未清理")
            done_uncleaned.append(item)
            continue
        if created_d and (today - created_d).days < thr:
            item["reason"] = f"新建于 {created_d}，未满 {thr} 天周期"
            new_pending.append(item)
            continue
        item["reason"] = (f"next_run_at={item['next_run_at']} 非未来，且 {thr} 天内无派发"
                          if item["next_run_at"] else f"next_run_at 缺失，且 {thr} 天内无派发")
        zombies.append(item)

    return {
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "window_days": days, "log": str(log),
        "counts": {
            "active_undeleted": len(rows),
            "zombie": len(zombies), "new_pending": len(new_pending),
            "once_uncleaned": len(done_uncleaned), "healthy": len(healthy),
            # 单列以免后人重犯"把已删行当活跃"的错
            "excluded_soft_deleted": n_soft_deleted,
            "raw_status_active": n_all_active,
        },
        "zombies": zombies, "once_uncleaned": done_uncleaned,
        "new_pending": new_pending, "healthy": healthy if include_ok else [],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14, help="最小窗口（实际取 max(14, 传入)）")
    ap.add_argument("--log", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--include-ok", action="store_true")
    a = ap.parse_args()

    log = pick_log(a.log)
    if log is None:
        print(f"🔴 找不到 automation.log（候选：{[str(p) for p in LOG_CANDIDATES]}）", file=sys.stderr)
        return 2

    r = audit(max(14, a.days), log, a.include_ok)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        c = r["counts"]
        print(f"🔍 自动化派发对账（窗口 {r['window_days']} 天 · 日志 {log.name}）")
        print(f"   活跃（status=ACTIVE 且未软删）{c['active_undeleted']} | 🟢 健康 {c['healthy']} | "
              f"🔴 活跃却从不派发 {c['zombie']} | 🕐 新建待首跑 {c['new_pending']} | "
              f"🧹 once 未清理 {c['once_uncleaned']}")
        print(f"   ℹ️ 另有 {c['excluded_soft_deleted']} 行 status=ACTIVE 但 deleted_at 非空"
              f"（**已软删除，非活跃**，已正确排除；裸 status 计数会得 {c['raw_status_active']}，虚高）")
        if r["zombies"]:
            print("\n🔴 活跃却从不派发 — 需停用或修复：")
            for z in sorted(r["zombies"], key=lambda x: x["last_dispatch"] or ""):
                print(f"   · 最近派发 {z['last_dispatch'] or '从未'} | next_run {z['next_run_at']}  "
                      f"{z['name'][:50]}\n       id={z['id']}")
        if r["once_uncleaned"]:
            print("\n🧹 once 已完成/已过期但仍是 ACTIVE（本机铁律：僵尸一次性须定期清理）：")
            for z in r["once_uncleaned"]:
                print(f"   · {z['name'][:50]}  (id={z['id']})")
        if r["new_pending"]:
            print("\n🕐 新建待首跑（正常）：")
            for z in r["new_pending"]:
                print(f"   · {z['name'][:50]}")
        if r["healthy"]:
            print(f"\n🟢 健康 {len(r['healthy'])} 条")
        if not (r["zombies"] or r["once_uncleaned"]):
            print("\n✅ 无异常")

    return 1 if (r["zombies"] or r["once_uncleaned"]) else 0


if __name__ == "__main__":
    sys.exit(main())
