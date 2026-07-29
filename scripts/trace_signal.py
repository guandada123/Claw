#!/usr/bin/env python3
"""信号追溯链查询 — 给定股票代码，追溯完整链路

用法:
    python3 scripts/trace_signal.py 000333   # 查单只
    python3 scripts/trace_signal.py --all     # 全持仓
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
SIM_DIR = PROJECT_ROOT / ".workbuddy" / "data" / "simulation"
DEBATE_DIR = PROJECT_ROOT / ".workbuddy" / "data" / "debate"


def load_json(path: Path) -> dict | list:
    if not path.exists():
        return {} if path.suffix == ".json" else []
    return json.loads(path.read_text(encoding="utf-8"))


def trace(code: str | None = None):
    pf = load_json(SIM_DIR / "portfolio.json")
    if isinstance(pf, list):
        return "无 portfolio 数据"

    positions = pf.get("positions", {})
    transactions = pf.get("transactions", [])
    trade_memories = load_json(SIM_DIR / "trading_memory.json")
    if isinstance(trade_memories, dict):
        trade_memories = []
    debates = load_json(DEBATE_DIR / "debate_result.json")
    if isinstance(debates, dict):
        debates = []

    codes = [code] if code else list(positions.keys())

    for c in codes:
        pos = positions.get(c, {})
        if not pos and code:
            print(f"{c}: 无持仓")
            continue

        name = pos.get("name", c)
        print(f"\n{'='*60}")
        print(f"  {name} ({c})")
        print(f"{'='*60}")

        # 持仓
        shares = pos.get("shares", 0)
        cost = pos.get("avg_cost", 0)
        price = pos.get("current_price", cost)
        pnl = (price - cost) * shares if cost > 0 else 0
        pnl_pct = round((price - cost) / cost * 100, 2) if cost > 0 else 0
        print(f"  持仓: {shares}股 | 成本 ¥{cost:.2f} | 现价 ¥{price:.2f}")
        print(f"  盈亏: ¥{pnl:+,.2f} ({pnl_pct:+.2f}%)")
        first_buy = pos.get("first_buy_date", "未知")
        print(f"  首次建仓: {first_buy} | 止盈层级: {pos.get('take_profit_level', 1)}")

        # 交易记录
        related_txns = [t for t in transactions if t.get("code") == c]
        if related_txns:
            print(f"\n  ── 交易记录 ({len(related_txns)}笔) ──")
            for t in related_txns[-5:]:
                action = "买入" if t.get("type") == "BUY" else "卖出"
                pnl_str = ""
                if t.get("type") == "SELL":
                    pnl_str = f"| 盈亏 ¥{t.get('realized_pnl',0):+,.2f} ({t.get('pnl_pct',0):+.2f}%)"
                print(f"  {t.get('date','')} {action} {t.get('shares',0)}股 @¥{t.get('price',0):.2f} {pnl_str}")

        # 交易记忆
        memories = [m for m in trade_memories if c in m.get("symbols", [])] if isinstance(trade_memories, list) else []
        if memories:
            print(f"\n  ── 交易记忆 ({len(memories)}条) ──")
            for m in memories[-3:]:
                print(f"  {m.get('created_at','')[:10]}: {m.get('title','')} — {m.get('lesson','')[:60]}")

        # 辩论
        stock_debates = [d for d in debates if d.get("code") == c] if isinstance(debates, list) else []
        if stock_debates:
            print(f"\n  ── 辩论历史 ({len(stock_debates)}次) ──")
            for d in stock_debates[-5:]:
                v = d.get("verdict", {})
                buys = sum(1 for s in d.get("stances", []) if s["stance"] == "BUY")
                holds = sum(1 for s in d.get("stances", []) if s["stance"] == "HOLD")
                sells = sum(1 for s in d.get("stances", []) if s["stance"] == "SELL")
                print(f"  {d.get('timestamp','')[:16]} → {v.get('consensus','?')} [{buys}B/{holds}H/{sells}S] conf={v.get('confidence',0):.0%}")
                if v.get("summary"):
                    print(f"    {v['summary'][:100]}")

        # 总结
        print("\n  ── 追溯总结 ──")
        has_debates = len(stock_debates) > 0
        has_memories = len(memories) > 0
        has_trades = len(related_txns) > 0
        print(f"  信号→决策: {'✅' if has_debates else '❌ 缺辩论'} | "
              f"  决策→执行: {'✅' if has_trades else '❌ 无交易'} | "
              f"  执行→回顾: {'✅' if has_memories else '❌ 缺记忆'}")


def main():
    p = argparse.ArgumentParser(description="信号追溯链查询")
    p.add_argument("code", nargs="?", help="股票代码（6位），不提供则查全持仓")
    args = p.parse_args()
    trace(args.code)


if __name__ == "__main__":
    main()
