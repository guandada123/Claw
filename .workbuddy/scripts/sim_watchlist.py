#!/usr/bin/env python3
"""
模拟炒股 — 选股池管理
从微信公众号文章中提取股票标的，维护观察列表
"""

import json
import sys
from datetime import date, datetime
from pathlib import Path

# 加载 Claw 公共库
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))
from error_handler import atomic_write_json

DATA_DIR = Path(__file__).parent.parent / "data" / "simulation"
WATCHLIST_FILE = DATA_DIR / "watchlist.json"
PICKS_FILE = DATA_DIR / "picks_history.json"


def today_str():
    return date.today().isoformat()


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_watchlist():
    if WATCHLIST_FILE.exists():
        return json.loads(WATCHLIST_FILE.read_text())
    return []


def save_watchlist(wl):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_json(WATCHLIST_FILE, wl)


def load_picks():
    if PICKS_FILE.exists():
        return json.loads(PICKS_FILE.read_text())
    return []


def save_picks(picks):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_json(PICKS_FILE, picks)


def add_to_watchlist(
    code: str,
    name: str,
    source: str,
    reason: str,
    suggested_entry: float = 0,
    target: float = 0,
    stop_loss: float = 0,
):
    """添加股票到观察列表"""
    wl = load_watchlist()

    # 去重
    for item in wl:
        if item["code"] == code:
            item["name"] = name
            item["source"] = source
            item["reason"] = reason
            item["updated_at"] = now()
            if suggested_entry:
                item["suggested_entry"] = suggested_entry
            if target:
                item["target"] = target
            if stop_loss:
                item["stop_loss"] = stop_loss
            save_watchlist(wl)
            return {"ok": True, "action": "updated", "code": code}

    item = {
        "code": code,
        "name": name,
        "source": source,
        "reason": reason,
        "suggested_entry": suggested_entry,
        "target": target,
        "stop_loss": stop_loss,
        "status": "watching",  # watching | bought | sold | dropped
        "added_at": now(),
        "updated_at": now(),
    }
    wl.append(item)
    save_watchlist(wl)

    # 记录选股历史
    picks = load_picks()
    picks.append(
        {
            "code": code,
            "name": name,
            "source": source,
            "reason": reason,
            "date": today_str(),
            "time": now(),
        }
    )
    save_picks(picks)

    return {"ok": True, "action": "added", "code": code}


def update_status(code: str, status: str):
    """更新观察状态"""
    wl = load_watchlist()
    for item in wl:
        if item["code"] == code:
            item["status"] = status
            item["updated_at"] = now()
            save_watchlist(wl)
            return {"ok": True, "code": code, "status": status}
    return {"ok": False, "error": "未找到该股票"}


def get_watchlist(status: str = None):
    """获取观察列表"""
    wl = load_watchlist()
    if status:
        wl = [i for i in wl if i["status"] == status]
    return wl


def get_picks_history(days: int = 30):
    """获取选股历史"""
    picks = load_picks()
    cutoff = (date.today() - __import__("datetime").timedelta(days=days)).isoformat()
    return [p for p in picks if p["date"] >= cutoff]


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: sim_watchlist.py <命令> [参数...]")
        print("  add <代码> <名称> <来源> <理由> [入场价] [目标价] [止损价]")
        print("  list [watching|bought|sold|dropped]")
        print("  status <代码> <状态>")
        print("  picks [天数]")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "add":
        result = add_to_watchlist(
            sys.argv[2],
            sys.argv[3],
            sys.argv[4],
            sys.argv[5],
            float(sys.argv[6]) if len(sys.argv) > 6 else 0,
            float(sys.argv[7]) if len(sys.argv) > 7 else 0,
            float(sys.argv[8]) if len(sys.argv) > 8 else 0,
        )
    elif cmd == "list":
        status = sys.argv[2] if len(sys.argv) > 2 else None
        result = get_watchlist(status)
    elif cmd == "status":
        result = update_status(sys.argv[2], sys.argv[3])
    elif cmd == "picks":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        result = get_picks_history(days)
    print(json.dumps(result, ensure_ascii=False, indent=2))
