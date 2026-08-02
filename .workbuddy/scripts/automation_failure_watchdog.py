#!/usr/bin/env python3
"""
automation_failure_watchdog.py — 自动化静默失败扫表告警

## 背景（2026-07-31 建）
Marvis 内存守护（可用内存 <1G 重启 WorkBuddy）会 **terminate 进行中的自动化**，
表现为「日报/监控没发」但没有任何告警。07-30 收盘晚报(1782817769722)、
14:00 助理实盘监控(1785123941786) 都是这么静默漏掉的，事后翻库才发现。

## 关键实证（勿凭记忆改，均已核表）
- `automation_runs.status` **恒为 'PENDING_REVIEW'**，库里根本没有 'interrupted' 这个值。
  按 status 过滤会 100% 漏检 → **必须用 `result_success = 0`**。
- `runs_json` 里**没有 runKind 字段**（旧记忆写的 runKind=interrupted 不存在）。
  中断特征体现在 `thread_title` 文案上。
- 近 7 天 result_success=0 仅 5 条 → 噪音极低，可直接推送不必先跑观察模式。

## 已知失败文案分类
| 文案特征 | 含义 |
|---|---|
| `Run interrupted because the automation orchestrator restarted` | 被 Marvis/WB 重启杀掉（真·静默中断） |
| `Run did not create a session within 60000ms` | 会话未拉起（排队/资源紧张） |
| `Generated results, but final wrap-up was interrupted` | 跑完了但收尾被打断（**产物多半已生成，危害小**） |

## 用法
    python3 automation_failure_watchdog.py                # 扫近24h，有关键失败才推送
    python3 automation_failure_watchdog.py --hours 48
    python3 automation_failure_watchdog.py --dry-run      # 只打印不推送
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

DB = Path.home() / ".workbuddy" / "workbuddy.db"
ROOT = Path(__file__).resolve().parent.parent.parent
PUSH = ROOT / ".workbuddy" / "scripts" / "push_feishu.sh"
# 去重状态：记录已推送告警的 (automation_id@运行时间戳)，避免同一条失败每小时重复轰炸
STATE = Path(__file__).resolve().parent / ".watchdog_alerted.json"

# 关键自动化（漏跑影响决策）→ 按名称前缀/关键词判定，避免硬编码 ID 导致新增自动化漏网
CRITICAL_KEYWORDS = (
    "早报", "晚报", "收盘", "盘中", "监控", "选股", "策略执行", "鱼盆", "账户",
)
# 收尾被打断：产物通常已生成，降级为提示不算关键
SOFT_FAIL_MARKERS = ("final wrap-up was interrupted",)
# 真·被杀
HARD_KILL_MARKERS = ("orchestrator restarted", "Run interrupted because")


def classify(title: str) -> tuple[str, str]:
    """返回 (等级, 人话原因)。等级: hard / soft / other"""
    t = title or ""
    if any(m in t for m in SOFT_FAIL_MARKERS):
        return "soft", "跑完但收尾被打断（产物多半已生成）"
    if any(m in t for m in HARD_KILL_MARKERS):
        return "hard", "被编排器重启杀掉（疑似 Marvis 内存守护）"
    if "did not create a session" in t:
        return "hard", "会话未拉起（排队/资源紧张）"
    return "other", (t[:60] or "未知失败")


def is_critical(name: str) -> bool:
    return any(k in (name or "") for k in CRITICAL_KEYWORDS)


def push(title: str, content: str) -> bool:
    env = dict(os.environ)
    env.setdefault("FEISHU_CHAT_ID", "oc_9ee5303497f5e0e71666b610d6bdc346")
    try:
        r = subprocess.run(
            ["bash", str(PUSH), title, content],
            capture_output=True, text=True, timeout=90, env=env,
        )
        print(r.stdout.strip()[-200:] or r.stderr.strip()[-200:])
        return r.returncode == 0
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ 推送异常: {e}")
        return False


def load_alerted() -> set:
    try:
        return set(json.loads(STATE.read_text(encoding="utf-8")))
    except Exception:
        return set()


def save_alerted(s: set) -> None:
    try:
        STATE.write_text(json.dumps(sorted(s), ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not DB.exists():
        print(f"⚠️ 数据库不存在: {DB}")
        return 1

    since_ms = int((datetime.now() - timedelta(hours=args.hours)).timestamp() * 1000)
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = con.execute(
        """
        SELECT r.automation_id, r.created_at, r.thread_title, a.name
        FROM automation_runs r
        LEFT JOIN automations a ON a.id = r.automation_id
        WHERE r.created_at > ? AND r.result_success = 0
        ORDER BY r.created_at DESC
        """,
        (since_ms,),
    ).fetchall()
    con.close()

    if not rows:
        print(f"[watchdog] 近 {args.hours}h 无失败记录 → SILENT")
        print('SUMMARY: {"failed":0,"critical":0,"pushed":false}')
        return 0

    critical, minor = [], []
    for aid, ts, title, name in rows:
        name = name or aid
        level, reason = classify(title)
        when = datetime.fromtimestamp(ts / 1000).strftime("%m-%d %H:%M")
        item = {"aid": aid, "name": name, "when": when, "ts": ts,
                "level": level, "reason": reason}
        # 关键自动化 + 硬失败 才算关键；soft(收尾打断) 一律降级
        if is_critical(name) and level == "hard":
            critical.append(item)
        else:
            minor.append(item)

    # 去重：同一条失败 (automation_id@运行时间戳) 已推送过则不再重复轰炸
    alerted = load_alerted()
    key_of = lambda it: f"{it['aid']}@{it['ts']}"
    new_critical = [it for it in critical if key_of(it) not in alerted]
    skipped = len(critical) - len(new_critical)

    print(f"[watchdog] 近 {args.hours}h 失败 {len(rows)} 条 | 关键 {len(critical)} | 次要 {len(minor)} | 已告警跳过 {skipped}")
    for it in critical + minor:
        flag = "🔴" if it in critical else "·"
        print(f"  {flag} [{it['when']}] {it['name'][:34]} — {it['reason']}")

    pushed = False
    if new_critical and not args.dry_run:
        lines = [f"🔴 近 {args.hours}h 有 {len(new_critical)} 个关键自动化静默失败（新增）", ""]
        for it in new_critical:
            lines.append(f"• [{it['when']}] {it['name']}")
            lines.append(f"   原因：{it['reason']}")
            lines.append(f"   ID：{it['aid'].replace('automation-', '')}")
            lines.append("")
        if minor:
            lines.append(f"（另有 {len(minor)} 条次要失败，未列出）")
        lines.append("")
        lines.append("建议：确认产物是否生成，未生成则手动补跑一次。")
        lines.append("若频繁出现，检查 Marvis 内存守护阈值（<1G 重启 WorkBuddy）。")
        pushed = push("自动化失败巡检", "\n".join(lines))
        if pushed:
            alerted.update(key_of(it) for it in new_critical)
            save_alerted(alerted)
    elif new_critical:
        print("[watchdog] (dry-run) 本应推送关键告警")
    elif critical:
        print(f"[watchdog] 关键失败均为已告警过的重复项 → SILENT（去重跳过 {skipped} 条）")
    else:
        print("[watchdog] 无关键失败 → SILENT（次要失败不打扰）")

    print("SUMMARY: " + json.dumps(
        {"failed": len(rows), "critical": len(critical),
         "new_critical": len(new_critical), "pushed": pushed}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
