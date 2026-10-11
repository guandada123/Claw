#!/usr/bin/env python3
"""把 automation-backups 与线上 DB 对齐，消除「陈旧备份被误用 → 静默回滚」的风险。

背景（2026-10-11 实测）：
  `~/.workbuddy/automation-backups/` 的备份**只在通过 UI 编辑自动化时生成**。
  凡经 `automation_update` API 改过 prompt 的任务，其备份会永远停在旧版本。
  证据：备份 mtime 按月分布 6月1 / 7月11 / 8月48 / **9月0** / 10月17；
  全量比对发现 16 个备份与线上不一致，最大差异 2277 字符
  （🛡️ 统一巡检中枢：备份 683 vs 线上 2960 → 误用直接丢掉四分之三逻辑）。

  这属于本系统最高频的事故类型：**上游状态陈旧 + 下游静默使用**。而按备份
  改写 prompt 正是会被建议的动作，所以必须让备份永远等于线上。

本脚本做的三件事：
  1. 比对每个备份的 prompt 与线上 DB
  2. 不一致 → 旧版归档为 `<原名>.stale-<YYYYMMDD>`（**不丢数据**）
  3. 原名用线上内容覆盖（保持文件名与键结构，避免破坏平台可读性）

用法：
  python3 sync_automation_backups.py              # 执行对齐
  python3 sync_automation_backups.py --dry-run    # 只报告不写盘
  python3 sync_automation_backups.py --selftest   # 不触网，纯逻辑断言

退出码：0=正常（含"已自动对齐"）；1=异常（DB/目录不可读）
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

HOME = Path.home()
DEFAULT_DB = HOME / ".workbuddy" / "workbuddy.db"
DEFAULT_BACKUP_DIR = HOME / ".workbuddy" / "automation-backups"

# 备份文件用 camelCase，DB 用 snake_case：camelCase -> DB 列名
FIELD_MAP = {
    "prompt": "prompt",
    "name": "name",
    "status": "status",
    "rrule": "rrule",
    "nextRunAt": "next_run_at",
    "lastRunAt": "last_run_at",
    "updatedAt": "updated_at",
}


def plan_sync(backup_prompts: dict, live_prompts: dict) -> dict:
    """纯函数：算出待对齐/孤立/已一致的集合。不读盘、不触网，便于单测。

    Returns:
        {"to_fix": [(id, 备份长度, 线上长度)], "orphans": [id], "same": [id]}
    """
    to_fix, orphans, same = [], [], []
    for aid, bp in backup_prompts.items():
        lp = live_prompts.get(aid)
        if lp is None:
            orphans.append(aid)
        elif bp == lp:
            same.append(aid)
        else:
            to_fix.append((aid, len(bp or ""), len(lp or "")))
    return {"to_fix": to_fix, "orphans": orphans, "same": same}


def load_live(db_path: Path) -> dict:
    """只读打开 DB，返回 {id: {camelField: value}}。"""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(automations)")]
        out = {}
        for row in con.execute("SELECT * FROM automations"):
            d = dict(zip(cols, row))
            if not d.get("id"):
                continue
            out[d["id"]] = {c: d.get(s) for c, s in FIELD_MAP.items()}
        return out
    finally:
        con.close()


def load_backups(backup_dir: Path) -> dict:
    """返回 {(path, id): {camelField: value}}；跳过 .stale-* 归档与不可解析文件。"""
    out = {}
    for f in sorted(backup_dir.glob("automation-*.json")):
        if ".stale-" in f.name:
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"  ⚠️ 跳过无法解析的备份 {f.name}: {e}", file=sys.stderr)
            continue
        out[(f, d.get("id") or f.name.replace(".json", ""))] = d
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只报告不写盘")
    ap.add_argument("--selftest", action="store_true", help="不触网，跑纯逻辑断言")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--backups-dir", default=str(DEFAULT_BACKUP_DIR))
    args = ap.parse_args()

    if args.selftest:
        return _selftest()

    db_path = Path(args.db).expanduser()
    bdir = Path(args.backups_dir).expanduser()
    if not db_path.exists():
        print(f"🔴 DB 不存在: {db_path}", file=sys.stderr)
        return 1
    if not bdir.exists():
        print(f"🔴 备份目录不存在: {bdir}", file=sys.stderr)
        return 1

    live = load_live(db_path)
    backups = load_backups(bdir)
    plan = plan_sync(
        {aid: d.get("prompt", "") for (_, aid), d in backups.items()},
        {aid: v.get("prompt", "") for aid, v in live.items()},
    )
    fix_ids = {t[0] for t in plan["to_fix"]}

    if not args.dry_run:
        for (f, aid), d in backups.items():
            if aid not in fix_ids:
                continue
            # 1) 先归档旧版（不丢数据）
            stamp = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y%m%d")
            arc = f.with_name(f.name + f".stale-{stamp}")
            if not arc.exists():
                arc.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
            # 2) 原名覆盖为线上内容（保持键结构不变）
            for camel in FIELD_MAP:
                d[camel] = live[aid].get(camel)
            d["_stale_note"] = (
                f"此备份此前未被自动刷新，已于 {datetime.now():%Y-%m-%d} 由 "
                "sync_automation_backups.py 对齐线上。原始旧版见同名 .stale-* 文件。"
            )
            f.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

    tag = "  (dry-run)" if args.dry_run else ""
    print(
        f"[backup-sync] 检查 {len(backups)} 个备份 | 一致 {len(plan['same'])} | "
        f"已对齐 {len(plan['to_fix'])} | 孤立 {len(plan['orphans'])}{tag}"
    )
    for aid, old, new in plan["to_fix"]:
        nm = next((d.get("name", "?") for (_, a), d in backups.items() if a == aid), "?")
        print(f"  {'[dry] ' if args.dry_run else ''}对齐 {nm[:30]:<32}{old:>6} → {new:>6}")
    if plan["orphans"]:
        print(
            f"  ⚠️ 线上已不存在的任务（备份孤立，未处理）: {len(plan['orphans'])} 个",
            file=sys.stderr,
        )
    return 0


def _selftest() -> int:
    """不触网的纯逻辑断言。"""
    fails: list[str] = []

    def check(name: str, got, want) -> None:
        if got != want:
            fails.append(f"{name}: 期望 {want!r} 实得 {got!r}")

    p = plan_sync({"a": "x", "b": "old", "c": "gone"}, {"a": "x", "b": "newer", "d": "z"})
    check("一致集合", p["same"], ["a"])
    check("待对齐集合", [t[0] for t in p["to_fix"]], ["b"])
    check("长度对 (备份, 线上)", (p["to_fix"][0][1], p["to_fix"][0][2]), (3, 5))
    check("孤立集合", p["orphans"], ["c"])

    # 反例（真实形态）
    check("空备份视为不一致", len(plan_sync({"a": ""}, {"a": "x"})["to_fix"]), 1)
    check("空线上视为孤立", plan_sync({"a": "x"}, {"a": ""})["orphans"], [])
    check(
        "无备份时全空",
        plan_sync({}, {"a": "x"}),
        {"to_fix": [], "orphans": [], "same": []},
    )

    if fails:
        print("❌ selftest 失败:")
        for f in fails:
            print(f"   - {f}")
        return 1
    print("✅ selftest 全绿（4 例 + 3 反例，不触网）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
