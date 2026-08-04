#!/usr/bin/env python3
"""北辰绩效面板 — 组合收益率 / 单票胜率 / 最大回撤

用法:
    python3 scripts/performance_dashboard.py          # JSON 输出
    python3 scripts/performance_dashboard.py --text    # 人类可读摘要
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).parent.parent
SIM_DIR = PROJECT_ROOT / ".workbuddy" / "data" / "simulation"
PORTFOLIO_FILE = SIM_DIR / "portfolio.json"
MEMORY_FILE = SIM_DIR / "trading_memory.json"


def load_portfolio() -> dict:
    if not PORTFOLIO_FILE.exists():
        return {}
    return json.loads(PORTFOLIO_FILE.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def load_memory() -> list[dict]:
    if not MEMORY_FILE.exists():
        return []
    return json.loads(MEMORY_FILE.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def calc_dashboard() -> dict:
    pf = load_portfolio()
    memories = load_memory()

    result: dict[str, Any] = {
        "total_asset": 0,
        "total_pnl": 0,
        "total_pnl_pct": 0,
        "realized_pnl": 0,
        "unrealized_pnl": 0,
        "total_trades": 0,
        "win_trades": 0,
        "lose_trades": 0,
        "win_rate": 0,
        "max_drawdown_pct": 0,
        "memory_trades": 0,
        "per_stock": [],
    }

    if not pf:
        return result

    # 总量（含 capital_additions 加仓，与 sim_trade.py 口径一致）
    cfg = pf.get("config", {})
    capital = cfg.get("initial_capital", 50000) + sum(
        float(a.get("amount", 0)) for a in cfg.get("capital_additions", [])
    )
    cash = pf.get("cash", 0)
    positions = pf.get("positions", {})
    transactions = pf.get("transactions", [])

    # 未实现盈亏
    unrealized = 0
    for code, pos in positions.items():
        shares = pos.get("shares", 0)
        cost = pos.get("avg_cost", 0)
        price = pos.get("current_price", cost)
        unrealized += (price - cost) * shares

    # 已实现盈亏
    realized = sum(t.get("realized_pnl", 0) for t in transactions if t.get("type") == "SELL")

    # 总资产
    position_value = sum(
        p.get("shares", 0) * p.get("current_price", p.get("avg_cost", 0))
        for p in positions.values()
    )
    total_asset = cash + position_value
    total_pnl = total_asset - capital
    total_pnl_pct = round(total_pnl / capital * 100, 2)

    result["total_asset"] = round(total_asset, 2)
    result["total_pnl"] = round(total_pnl, 2)
    result["total_pnl_pct"] = total_pnl_pct
    result["realized_pnl"] = round(realized, 2)
    result["unrealized_pnl"] = round(unrealized, 2)

    # 单票统计
    sell_txns = [t for t in transactions if t.get("type") == "SELL"]
    wins = sum(1 for t in sell_txns if t.get("realized_pnl", 0) > 0)
    total_trades = len(sell_txns)

    result["total_trades"] = total_trades
    result["win_trades"] = wins
    result["lose_trades"] = total_trades - wins
    result["win_rate"] = round(wins / total_trades * 100, 2) if total_trades > 0 else None

    # 最大回撤（基于 transactions 中的 daily_snapshot 或历史）
    # 如果有 daily_snapshot 数据
    snapshots = pf.get("daily_snapshot", {})
    if snapshots:
        peak = capital
        max_dd = 0
        for date_str, snap in sorted(snapshots.items()):
            val = snap.get("total_asset", peak)
            peak = max(peak, val)
            dd = (peak - val) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)
        result["max_drawdown_pct"] = round(max_dd, 2)

    # 每只持仓
    for code, pos in positions.items():
        shares = pos.get("shares", 0)
        cost = pos.get("avg_cost", 0)
        price = pos.get("current_price", cost)
        pnl = (price - cost) * shares
        pnl_pct = round((price - cost) / cost * 100, 2) if cost > 0 else 0
        result["per_stock"].append({
            "code": code,
            "name": pos.get("name", ""),
            "shares": shares,
            "avg_cost": cost,
            "current_price": price,
            "pnl": round(pnl, 2),
            "pnl_pct": pnl_pct,
        })

    # 从 trade memory 补充胜率数据
    trade_memories = [m for m in memories if m.get("memory_type") == "trade"]
    if trade_memories:
        result["memory_trades"] = len(trade_memories)

    return result


def fmt_text(d: dict) -> str:
    win_rate_str = f"{d['win_rate']:.1f}%" if d['win_rate'] is not None else "N/A"
    lines = [
        "📊 组合绩效",
        f"  总资产: ¥{d['total_asset']:,.2f} | 累计盈亏: ¥{d['total_pnl']:+,.2f} ({d['total_pnl_pct']:+.2f}%)",
        f"  已实现: ¥{d['realized_pnl']:+,.2f} | 未实现: ¥{d['unrealized_pnl']:+,.2f}",
        f"  交易: {d['total_trades']}笔 | 胜率: {win_rate_str} ({d['win_trades']}W/{d['lose_trades']}L)",
    ]
    if d["max_drawdown_pct"]:
        lines.append(f"  最大回撤: {d['max_drawdown_pct']:.2f}%")
    if d["memory_trades"]:
        lines.append(f"  记忆收录: {d['memory_trades']}条")

    if d["per_stock"]:
        lines.append("")
        lines.append("📈 持仓明细")
        for s in d["per_stock"]:
            color = "🔴" if s["pnl_pct"] < 0 else "🟢"
            lines.append(
                f"  {color} {s['name']}({s['code']}): {s['shares']}股 "
                f"@¥{s['current_price']:.2f} | "
                f"盈亏 ¥{s['pnl']:+,.2f} ({s['pnl_pct']:+.2f}%)"
            )
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description="北辰绩效面板")
    p.add_argument("--text", action="store_true", help="人类可读摘要")
    p.add_argument("--json", action="store_true", help="JSON 输出（默认）")
    args = p.parse_args()

    dashboard = calc_dashboard()

    if args.text:
        print(fmt_text(dashboard))
    else:
        print(json.dumps(dashboard, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
