#!/usr/bin/env python3
"""
Claw 用户持仓自动刷新脚本。

功能:
- 读取 user/portfolio.json 获取当前持仓
- 通过 tdx-connector (通达信 MCP) 获取标的最新行情
- 更新持仓中的现价、盈亏、总资产
- 保存更新后的 portfolio.json

Usage:
    python3 refresh_portfolio.py [--dry-run]

数据源:
    主: tdx-connector MCP (通过 tdx_quotes)
    备: 腾讯财经 API (免费、稳定)
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PORTFOLIO_PATH = PROJECT_ROOT / ".workbuddy" / "data" / "user" / "portfolio.json"


def load_portfolio() -> dict:
    """加载持仓文件."""
    if not PORTFOLIO_PATH.exists():
        print(f"[ERROR] 持仓文件不存在: {PORTFOLIO_PATH}")
        sys.exit(1)

    with open(PORTFOLIO_PATH) as f:
        return json.load(f)


def save_portfolio(data: dict) -> None:
    """保存持仓文件."""
    with open(PORTFOLIO_PATH, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[OK] 持仓已保存: {PORTFOLIO_PATH}")


def get_quotes_via_tdx(codes: list[str]) -> dict[str, dict]:
    """通过通达信 MCP 获取实时行情."""
    try:
        import httpx  # noqa: F811
    except ImportError:
        print("[TDX] httpx 未安装，跳过通达信")
        return {}
    except Exception as e:
        print(f"[TDX] 导入异常: {e}")
        return {}

    try:
        resp = httpx.post(
            "http://localhost:8300/mcp/get_quotes",
            json={
                "codes": codes,
                "fields": ["code", "name", "price", "pct_change", "pre_close", "change"],
            },
            timeout=10.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            quotes = data.get("data", data)
            result = {}
            for q in (quotes or []):
                code = q.get("code", "").split(".")[0]
                result[code] = {
                    "name": q.get("name", ""),
                    "price": float(q.get("price", 0)),
                    "pct_change": float(q.get("pct_change", 0)),
                    "change": float(q.get("change", 0)),
                }
            print(f"[TDX] 获取 {len(result)}/{len(codes)} 只标的行情")
            return result
        print(f"[TDX] HTTP {resp.status_code}: {resp.text[:200]}")
    except httpx.ConnectError:
        print("[TDX] 连接失败 — tdx-connector 未运行")
    except Exception as e:
        print(f"[TDX] 异常: {e}")
    return {}


def get_quotes_via_tencent(codes: list[str]) -> dict[str, dict]:
    """通过腾讯财经 API 获取实时行情（备用）."""
    import urllib.request

    # 腾讯代码映射: 6开头→sh, 0/3开头→sz
    tc_codes = []
    for c in codes:
        if c.startswith("6"):
            tc_codes.append(f"sh{c}")
        else:
            tc_codes.append(f"sz{c}")

    try:
        url = f"http://qt.gtimg.cn/q={','.join(tc_codes)}"
        resp = urllib.request.urlopen(url, timeout=5)
        raw = resp.read().decode("gbk", errors="replace")

        result = {}
        for code, tc in zip(codes, tc_codes):
            prefix = f'v_{tc}="'
            try:
                start = raw.index(prefix) + len(prefix)
                end = raw.index('";', start)
                fields = raw[start:end].split("~")
                if len(fields) >= 32:
                    result[code] = {
                        "name": fields[1],
                        "price": float(fields[3]) if fields[3] else 0.0,
                        "pct_change": float(fields[32]) if fields[32] else 0.0,
                        "change": float(fields[31]) if fields[31] else 0.0,
                    }
            except (ValueError, IndexError):
                pass
        print(f"[腾讯] 获取 {len(result)}/{len(codes)} 只标的行情")
        return result
    except Exception as e:
        print(f"[腾讯] 失败: {e}")
    return {}


def update_portfolio(data: dict, quotes: dict[str, dict], dry_run: bool = False) -> dict:
    """更新持仓数据（兼容 holdings schema）."""
    holdings = data.get("holdings", [])
    summary = data.get("summary", {})

    # 记录旧市值（更新前）
    old_holdings_mv = sum(h.get("market_value", 0) for h in holdings)

    total_market_value = 0.0
    total_pnl = 0.0
    updated_count = 0

    for h in holdings:
        code = h.get("code", "")
        q = quotes.get(code)
        if not q:
            print(f"  [WARN] {code} 无行情数据，跳过")
            continue

        old_price = h.get("current_price", 0)
        new_price = q["price"]
        shares = h.get("shares", 0)
        cost_price = h.get("cost_price", 0)

        h["current_price"] = new_price
        h["market_value"] = round(new_price * shares, 2)
        h["pnl"] = round((new_price - cost_price) * shares, 2)
        h["pnl_pct"] = round((new_price / cost_price - 1) * 100, 2) if cost_price > 0 else 0
        h["name"] = q.get("name") or h.get("name", code)

        total_market_value += h["market_value"]
        total_pnl += h["pnl"]
        updated_count += 1

        direction = "▲" if new_price > old_price else "▼" if new_price < old_price else "→"
        print(
            f"  [{direction}] {h['name']}: "
            f"¥{old_price:.2f} → ¥{new_price:.2f} "
            f"({h['pnl_pct']:+.2f}%) "
            f"PnL: ¥{h['pnl']:+.2f}"
        )

    # 更新汇总: new_total = old_total - old_mv + new_mv（现金不变）
    old_total = summary.get("total_assets", 0)
    new_total = round(old_total - old_holdings_mv + total_market_value, 2)

    summary["total_assets"] = new_total
    summary["floating_pnl"] = round(total_pnl, 2)
    summary["position_pct"] = round(total_market_value / new_total * 100, 1) if new_total > 0 else 0
    data["summary"] = summary
    data["updated"] = datetime.now().strftime("%Y-%m-%d")

    print(f"\n[汇总] 更新 {updated_count} 只持仓, 总资产: ¥{summary['total_assets']:,.2f}, 浮动盈亏: ¥{total_pnl:+,.2f}")

    if not dry_run:
        save_portfolio(data)
    else:
        print("[DRY-RUN] 未实际保存")

    return data


def main():
    dry_run = "--dry-run" in sys.argv

    print("=" * 50)
    print("  Claw 用户持仓自动刷新")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    data = load_portfolio()
    holdings = data.get("holdings", [])
    if not holdings:
        print("[INFO] 无持仓数据，跳过")
        return

    codes = [h["code"] for h in holdings]
    print(f"\n持仓标的: {', '.join(codes)}")

    # 优先通达信，降级到腾讯
    quotes = get_quotes_via_tdx(codes)
    if not quotes:
        print("[降级] 通达信不可用，切换到腾讯财经")
        quotes = get_quotes_via_tencent(codes)

    if not quotes:
        print("[ERROR] 所有数据源不可用，无法刷新")
        sys.exit(1)

    print()
    update_portfolio(data, quotes, dry_run=dry_run)

    print(f"\n数据更新至: {data.get('updated', 'unknown')}")


if __name__ == "__main__":
    main()
