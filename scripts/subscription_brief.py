#!/usr/bin/env python3
"""
subscription_brief.py — 全开订阅增长简报（C 可选周报推送）

每周六输出「当前已订阅 / 本周新增 / 达上限 pending / 累计候选」卡片推飞书。
依赖：data/subscribe_candidates.json（由 discover_gzh_accounts.py 写入）
本地 WeChat Download API localhost:5001（可选，降级仍可出报告）

用法:
  python3 scripts/subscription_brief.py          # 正常执行，推飞书
  python3 scripts/subscription_brief.py --dry    # 仅打印，不推送
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CANDIDATES_FILE = PROJECT_ROOT / "data" / "subscribe_candidates.json"
LOCAL_API = "http://localhost:5001"

CHAT_ID = "oc_9ee5303497f5e0e71666b610d6bdc346"


def load_candidates() -> list[dict]:
    """读取 subscribe_candidates.json 候选列表。"""
    if not CANDIDATES_FILE.exists():
        return []
    try:
        d = json.loads(CANDIDATES_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else d.get("candidates", [])
    except Exception:
        return []


def fetch_local_sub_count() -> int | None:
    """从本地 API 获取当前订阅数。不可达返回 None。"""
    try:
        req = urllib.request.Request(f"{LOCAL_API}/api/rss/subscriptions")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return len(data.get("data", []))
    except Exception:
        return None


def build_brief(dry: bool = False) -> dict:
    candidates = load_candidates()
    sub_count = fetch_local_sub_count()

    # 统计分类
    total = len(candidates)
    subscribed = sum(1 for c in candidates if c.get("status") == "subscribed")
    pending_cap = sum(1 for c in candidates if c.get("status") == "pending_cap")
    pending_other = sum(1 for c in candidates if c.get("status") == "pending")
    already = sum(1 for c in candidates if c.get("status") == "already_subscribed")
    failed = sum(1 for c in candidates if c.get("status") == "subscribe_failed")

    # 近 7 天新增
    now = datetime.now()
    cutoff = now - timedelta(days=7)
    new_7d = sum(1 for c in candidates if c.get("status") == "subscribed"
                 and _parse_iso(c.get("discovered_at", ""))
                 and _parse_iso(c["discovered_at"]) >= cutoff)
    new_total_7d = sum(1 for c in candidates if _parse_iso(c.get("discovered_at", ""))
                       and _parse_iso(c["discovered_at"]) >= cutoff)

    title = f"📬【订阅周报】{now.strftime('%Y-%m-%d')}"

    lines = [
        f"**已订阅**：{subscribed}",
    ]
    if sub_count is not None:
        lines[0] += f"（本地API={sub_count}）"
    lines += [
        f"**本周新增订阅**：{new_7d}（全部新增候选{new_total_7d}）",
        f"**达上限 pending**：{pending_cap}",
        f"**其他待审核**：{pending_other}",
        f"**已有跳过**：{already}",
        f"**失败**：{failed}",
        f"**累计候选总数**：{total}",
        "",
        f"最近候选：",
    ]
    # 列出最近 5 条候选
    recent = sorted(
        [c for c in candidates if _parse_iso(c.get("discovered_at"))],
        key=lambda c: _parse_iso(c["discovered_at"]),
        reverse=True,
    )[:5]
    for c in recent:
        ds = c.get("discovered_at", "")[:10]
        st = c.get("status", "?")
        icon = {"subscribed": "✅", "pending_cap": "🟡", "pending": "⏳", "subscribe_failed": "❌", "already_subscribed": "📌"}.get(st, "⚪")
        lines.append(f"  {icon} {c['name']}—{st}（{ds}）")

    brief = {
        "generated_at": now.isoformat(),
        "subscribed": subscribed,
        "sub_count_api": sub_count,
        "new_7d": new_7d,
        "new_total_7d": new_total_7d,
        "pending_cap": pending_cap,
        "pending_other": pending_other,
        "already": already,
        "failed": failed,
        "total": total,
        "lines": lines,
    }

    if dry:
        print("[DRY RUN] 以下为简报内容：")
        print(title)
        print("\n".join(lines))
        return brief

    # 推飞书
    env = {"FEISHU_CHAT_ID": CHAT_ID}
    env["FEISHU_CHAT_ID"] = CHAT_ID
    push = subprocess.run(
        ["bash", str(PROJECT_ROOT / ".workbuddy" / "scripts" / "push_feishu.sh"),
         title, "\n".join(lines)],
        capture_output=True, text=True, env=env, check=False, timeout=30,
    )
    brief["push_ok"] = push.returncode == 0
    brief["push_detail"] = push.stdout[:200]
    print(f"[brief] 推送{'成功' if brief['push_ok'] else '失败'} rc={push.returncode}")
    return brief


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


if __name__ == "__main__":
    dry = "--dry" in sys.argv
    build_brief(dry=dry)
