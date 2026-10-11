#!/usr/bin/env python3
"""实盘快照结构重写 — 人工确认后的**唯一**入口（2026-10-11 审计 P0-2 配套）。

为什么需要它：
    实盘账户的权威数据源只有「人工截图/录入」（ths_account_sync 已缺失、QMT 不可用），
    而历史做法是**手工编辑 JSON** —— 真金白银的账本靠手改，极易出字段/精度错误。
    本脚本把「结构确认」变成一次**有校验、有回读**的操作。

职责边界：
    · 只负责**结构**（持仓代码/股数/成本 + 可用资金 + as-of 来源），价格由 refresh_portfolio 刷新；
    · 不做任何交易、不猜数据：缺参数即报错（fail-closed）。

用法：
    python3 .workbuddy/scripts/update_live_snapshot.py \
        --position 600584:200:94.656 \
        --position 002185:500:22.829 \
        --cash 26683.72 \
        --source "国金APP截图(2026-10-11 15:36)" \
        [--dry-run]

校验（任一不过即 rc=1，不写盘）：
    · 代码为 6 位数字；股数 > 0；成本 > 0；可用资金 >= 0
    · 股数非 100 整数倍 → **告警但不擅自改写**（真实账户可能有零股，须人工确认）
退出码：0=成功（含回读通过）｜1=参数/校验失败｜2=回读不一致
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import refresh_portfolio as rp  # noqa: E402


def parse_position(raw: str) -> dict:
    """解析 `code:shares:avg_cost`。"""
    parts = raw.split(":")
    if len(parts) != 3:
        raise ValueError(f"格式应为 code:shares:avg_cost，收到 {raw!r}")
    code, shares_s, cost_s = (p.strip() for p in parts)
    if not (code.isdigit() and len(code) == 6):
        raise ValueError(f"代码须为 6 位数字: {code!r}")
    try:
        shares = int(float(shares_s))
    except ValueError as e:
        raise ValueError(f"股数非数字: {shares_s!r}") from e
    try:
        cost = float(cost_s)
    except ValueError as e:
        raise ValueError(f"成本非数字: {cost_s!r}") from e
    if shares <= 0 or cost <= 0:
        raise ValueError(f"股数与成本须为正: {raw!r}")
    return {"code": code, "shares": shares, "avg_cost": cost}


def build_holdings(positions: list[dict], old_holdings: list[dict]) -> tuple[list[dict], list[str]]:
    """按新结构构造 holdings，尽量沿用旧记录里的辅助字段（name/bought_date 等）。"""
    warnings: list[str] = []
    old_by_code = {h.get("code"): h for h in old_holdings}
    out = []
    for p in positions:
        old = old_by_code.get(p["code"], {})
        if p["shares"] % 100 != 0:
            warnings.append(
                f"⚠️ {p['code']} 股数 {p['shares']} 非 100 整数倍（可能为零股/拆股残留），已按原值写入，请核对"
            )
        out.append(
            {
                **{k: v for k, v in old.items() if k not in ("market_value", "pnl", "pnl_pct")},
                "code": p["code"],
                "name": old.get("name", p["code"]),
                "shares": p["shares"],
                "shares_available": p["shares"],
                "avg_cost": p["avg_cost"],
                "current_price": old.get("current_price", 0),
                "market_value": round(old.get("current_price", 0) * p["shares"], 2),
                "broker": old.get("broker", "GJ"),
            }
        )
    return out, warnings


def main() -> int:
    ap = argparse.ArgumentParser(description="实盘快照结构重写（人工确认后使用）")
    ap.add_argument("--position", action="append", default=[], help="可重复：code:shares:avg_cost")
    ap.add_argument("--cash", type=float, required=True, help="可用资金（元）")
    ap.add_argument(
        "--source", required=True, help="数据来源与时间，如 国金APP截图(2026-10-11 15:36)"
    )
    ap.add_argument("--clear", action="store_true", help="已清仓：持仓置空（须与 --position 互斥）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.clear and args.position:
        print("[ERROR] --clear 与 --position 互斥")
        return 1
    if not args.clear and not args.position:
        print("[ERROR] 需至少一个 --position；若是清仓，请显式传 --clear（不猜）")
        return 1
    if args.cash < 0:
        print("[ERROR] --cash 不得为负")
        return 1

    try:
        positions = [parse_position(p) for p in args.position]
    except ValueError as e:
        print(f"[ERROR] 参数校验失败: {e}")
        return 1

    data = rp.load_portfolio()
    rules = data.get("rules", {"stop_loss": -0.08, "take_profit": None, "note": "止损8%，止盈不设"})
    new_holdings, warnings = build_holdings(positions, data.get("holdings", []))
    for w in warnings:
        print(w)

    data["holdings"] = new_holdings
    data["rules"] = rules
    data["updated"] = datetime.now().strftime("%Y-%m-%d")
    data["data_source"] = f"{args.source} + 腾讯/Wind 实时价刷新({datetime.now():%Y-%m-%d %H:%M})"
    data["structure_basis"] = f"人工确认录入（{args.source}）"

    summary = data.setdefault("summary", {})
    summary["cash_available"] = round(args.cash, 2)
    summary["cash_withdrawable"] = round(args.cash, 2)
    data["summary"] = summary

    # 用实时行情补价格与盈亏（复用 refresh_portfolio 的降级链）；先 dry-run 计算，再按需落盘
    codes = [h["code"] for h in new_holdings]
    quotes = rp.get_quotes_via_wind(codes) or rp.get_quotes_via_tencent(codes) if codes else {}
    if codes and not quotes:
        print("[WARN] 行情源均不可用：只写结构与现金，价格留待每日刷新补齐")
        quotes = {}
    for h in new_holdings:
        if h["code"] not in quotes:
            h["current_price"] = 0.0
            h["market_value"] = 0.0

    rp.update_portfolio(data, quotes, dry_run=True, confirm_structure=True)

    # 来源信息在 update_portfolio 之后回填：它内部的 confirm 分支会写通用文案，
    # 会覆盖掉本次录入的真实来源（保留人工来源才可追溯）。
    data["data_source"] = f"{args.source} + 行情刷新({datetime.now():%Y-%m-%d %H:%M})"
    data["structure_basis"] = f"人工确认录入（{args.source}）"

    if not new_holdings:
        # 清仓：总资产 = 现金（update_portfolio 的「旧市值守恒」公式在空仓下不适用）
        data["summary"]["total_market_value"] = 0.0
        data["summary"]["floating_pnl"] = 0.0
        data["summary"]["position_pct"] = 0.0
        data["summary"]["total_assets"] = round(args.cash, 2)

    print(f"\n[结构] {len(new_holdings)} 只持仓；现金 ¥{args.cash:,.2f}；来源：{args.source}")
    if args.dry_run:
        print("[DRY-RUN] 未写盘")
        return 0

    rp.save_portfolio(data)
    rp._readback_check(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
