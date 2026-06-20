#!/usr/bin/env python3
"""Quick VBM backtest - inline"""

import sys

sys.path.insert(0, "/app")
from services.backtest_engine_v2 import BacktestConfig, EnhancedBacktestEngine

codes = [
    "002049.SZ",
    "600498.SH",
    "000725.SZ",
    "600522.SH",
    "002601.SZ",
    "600206.SH",
    "000001.SZ",
    "000333.SZ",
    "002415.SZ",
    "600519.SH",
    "601318.SH",
    "000858.SZ",
    "600036.SH",
    "600276.SH",
    "600887.SH",
    "600570.SH",
    "600585.SH",
    "600893.SH",
    "601899.SH",
    "002230.SZ",
    "300750.SZ",
    "688981.SH",
]

results = []
for code in codes:
    c = BacktestConfig(
        ts_codes=[code], strategies=["vbm"], start_date="20250601", end_date="20260617"
    )
    r = EnhancedBacktestEngine(c).run()
    results.append(
        {
            "code": code,
            "total_return": r.total_return,
            "sharpe": r.sharpe_ratio,
            "max_dd": r.max_drawdown,
            "win_rate": r.win_rate,
            "total_trades": r.total_trades,
            "profit_factor": r.profit_factor,
        }
    )
    print(
        f"  {code}: return={r.total_return * 100:+.2f}% sharpe={r.sharpe_ratio:.3f} trades={r.total_trades}",
        flush=True,
    )

avg_ret = sum(r["total_return"] for r in results) / len(results) * 100
avg_sharpe = sum(r["sharpe"] for r in results) / len(results)
pos = sum(1 for r in results if r["total_return"] > 0)
print("\n=== VBM Summary ===")
print(f"Avg Return: {avg_ret:+.2f}%")
print(f"Avg Sharpe: {avg_sharpe:.3f}")
print(f"Positive: {pos}/{len(results)} ({pos / len(results) * 100:.0f}%)")
print(f"Total trades: {sum(r['total_trades'] for r in results)}")
print(f"Avg trades/stock: {sum(r['total_trades'] for r in results) / len(results):.1f}")
