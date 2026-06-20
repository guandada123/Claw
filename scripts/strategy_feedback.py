#!/usr/bin/env python3
"""
策略库双向反馈 — 实盘交易 → 策略库沉淀

执行: python3 scripts/strategy_feedback.py
触发: 收盘回顾自动化中的一步

功能:
1. 检测用户实盘新增的 closed_positions
2. 按盈亏分类记录到 strategy_library.json 的 user_trades 字段
3. 从实盘交易中提炼可复用的模式（行业+持有周期+结果）
"""

import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
USER_PORTFOLIO = PROJECT_ROOT / ".workbuddy" / "data" / "user" / "portfolio.json"
STRATEGY_LIB = PROJECT_ROOT / ".workbuddy" / "data" / "simulation" / "strategy_library.json"
FEEDBACK_LOG = PROJECT_ROOT / ".workbuddy" / "data" / "simulation" / "user_trade_feedback.json"


def load_json(path):
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[INFO] 已保存: {path}", file=sys.stderr)


def classify_trade(pnl_pct):
    """按盈亏率分类"""
    if pnl_pct is None:
        return "unknown"
    if pnl_pct >= 10:
        return "big_win"
    elif pnl_pct >= 3:
        return "win"
    elif pnl_pct >= -3:
        return "flat"
    elif pnl_pct >= -8:
        return "small_loss"
    else:
        return "big_loss"


def tally_patterns(user_trades):
    """汇总实盘交易模式"""
    if not user_trades:
        return {"patterns": [], "summary": {}}

    # 按行业+结果统计
    industry_outcomes = {}
    for trade in user_trades:
        key = trade.get("industry", "unknown")
        result = trade.get("classification", "unknown")
        if key not in industry_outcomes:
            industry_outcomes[key] = {"trades": 0, "wins": 0, "losses": 0, "total_pnl": 0}
        industry_outcomes[key]["trades"] += 1
        pnl = trade.get("pnl_pct", 0) or 0
        industry_outcomes[key]["total_pnl"] += pnl
        if result in ("big_win", "win"):
            industry_outcomes[key]["wins"] += 1
        elif result in ("big_loss", "small_loss"):
            industry_outcomes[key]["losses"] += 1

    # 提取模式
    patterns = []
    for industry, stats in sorted(industry_outcomes.items(), key=lambda x: -x[1]["trades"]):
        if stats["trades"] >= 2:
            win_rate = round(stats["wins"] / stats["trades"] * 100, 1) if stats["trades"] else 0
            avg_pnl = round(stats["total_pnl"] / stats["trades"], 1) if stats["trades"] else 0
            patterns.append({
                "source": "user_trades",
                "industry": industry,
                "trades": stats["trades"],
                "wins": stats["wins"],
                "losses": stats["losses"],
                "win_rate": win_rate,
                "avg_pnl_pct": avg_pnl,
                "last_updated": datetime.now().strftime("%Y-%m-%d"),
            })

    return {
        "patterns": patterns,
        "summary": {
            "total_trades": len(user_trades),
            "total_wins": sum(1 for t in user_trades if t.get("classification") in ("big_win", "win")),
            "total_losses": sum(1 for t in user_trades if t.get("classification") in ("big_loss", "small_loss")),
        },
    }


def feedback():
    """主入口 — 检测实盘新成交记录并写入策略库"""
    user_data = load_json(USER_PORTFOLIO)
    strategy_data = load_json(STRATEGY_LIB)

    if not user_data:
        print(json.dumps({"error": "无法读取用户持仓", "timestamp": datetime.now().isoformat()}))
        sys.exit(1)

    # 读取已有反馈记录
    feedback_data = load_json(FEEDBACK_LOG)
    if not feedback_data:
        feedback_data = {
            "version": "1.0",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "last_sync": None,
            "trades": [],
        }

    closed_positions = user_data.get("closed_positions", [])
    if not closed_positions:
        print(json.dumps({"status": "no_closed_positions", "timestamp": datetime.now().isoformat()}))
        return

    # 记录已处理的 closed_position ID
    processed_ids = {t.get("id") for t in feedback_data["trades"] if t.get("id")}

    new_trades = []
    for pos in closed_positions:
        # 用 code+closed_date 生成唯一 ID
        trade_id = f"{pos.get('code','')}_{pos.get('closed_date','')}"
        if trade_id in processed_ids:
            continue

        pnl_pct = pos.get("pnl_pct") or 0
        trade = {
            "id": trade_id,
            "code": pos.get("code", ""),
            "name": pos.get("name", ""),
            "pnl_pct": round(pnl_pct, 2),
            "pnl": round(pos.get("pnl", 0) or 0, 2),
            "cost_price": pos.get("cost_price"),
            "exit_price": pos.get("exit_price"),
            "status": pos.get("status", "已清仓"),
            "closed_date": pos.get("closed_date", ""),
            "classification": classify_trade(pnl_pct),
            "industry": "",  # 需要在运行中填写
            "recorded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        new_trades.append(trade)

    if not new_trades:
        print(json.dumps({"status": "no_new_trades", "timestamp": datetime.now().isoformat()}))
        return

    # 新增交易 → 追加到反馈记录
    feedback_data["trades"].extend(new_trades)
    feedback_data["last_sync"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 计算模式汇总
    pattern_summary = tally_patterns(feedback_data["trades"])
    feedback_data["pattern_analysis"] = pattern_summary

    # 保存反馈日志
    save_json(FEEDBACK_LOG, feedback_data)

    # → 回写到 strategy_library.json 的 user_trades 字段
    if strategy_data:
        strategy_data["user_trades"] = {
            "last_sync": feedback_data["last_sync"],
            "total_trades": len(feedback_data["trades"]),
            "recent_new": len(new_trades),
            "new_trades": [
                {
                    "code": t["code"],
                    "name": t["name"],
                    "pnl_pct": t["pnl_pct"],
                    "classification": t["classification"],
                    "status": t["status"],
                    "closed_date": t["closed_date"],
                }
                for t in new_trades
            ],
            "pattern_summary": pattern_summary,
        }
        save_json(STRATEGY_LIB, strategy_data)

    result = {
        "status": "success",
        "new_trades": len(new_trades),
        "total_trades": len(feedback_data["trades"]),
        "new_trades_detail": new_trades,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    feedback()
