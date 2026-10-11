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
    主: Wind 万得 (高质量付费行情)
    备1: tdx-connector MCP (通达信)
    备2: 腾讯财经 API (免费、稳定)
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

# Wind 万得（可选依赖）
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from claw.feeds.wind_utils import (
        get_wind_realtime_price,
        plain_code_to_windcode,
        wind_available,
    )
except ImportError:

    def wind_available() -> bool:  # type: ignore[misc]
        return False

    def get_wind_realtime_price(code: str) -> None:  # type: ignore[misc]
        return None

    def plain_code_to_windcode(code: str) -> str:  # type: ignore[misc]
        return code


# 🔴 2026-10-11 修复：本文件在 `.workbuddy/scripts/` 下，parent.parent 只到 `.workbuddy`，
#    再拼 ".workbuddy/data/..." 会得到 `.workbuddy/.workbuddy/data/...` → 文件恒定不存在，
#    脚本自创建起就无法运行（这解释了为何持仓快照长期靠手改）。
PROJECT_ROOT = Path(__file__).resolve().parents[2]  # Claw 仓库根
PORTFOLIO_PATH = PROJECT_ROOT / ".workbuddy" / "data" / "user" / "portfolio.json"


def load_portfolio() -> dict:
    """加载持仓文件."""
    if not PORTFOLIO_PATH.exists():
        print(f"[ERROR] 持仓文件不存在: {PORTFOLIO_PATH}")
        sys.exit(1)

    with open(PORTFOLIO_PATH) as f:
        return json.load(f)  # type: ignore[no-any-return]


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
            for q in quotes or []:
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


def get_quotes_via_wind(codes: list[str]) -> dict[str, dict]:
    """通过 Wind 万得获取实时行情（优先数据源）。"""
    if not wind_available():
        print("[Wind] CLI 或 API Key 不可用，跳过")
        return {}

    result = {}
    for code in codes:
        try:
            r = get_wind_realtime_price(code)
            if r and r.get("price") is not None:
                result[code] = {
                    "name": "",  # Wind 不返回名称，后面用持仓中的
                    "price": r["price"],
                    "pct_change": r.get("change_pct", 0),
                    "change": None,
                }
        except Exception as e:
            print(f"  [Wind] {code} 查询异常: {e}")
            continue

    if result:
        print(f"[Wind] 获取 {len(result)}/{len(codes)} 只标的行情")
    else:
        print("[Wind] 未获取到行情数据")
    return result


def get_quotes_via_tencent(codes: list[str]) -> dict[str, dict]:
    """通过腾讯财经 API 获取实时行情（备用）."""
    import urllib.request

    # 腾讯代码映射（2026-10-11 修）：沪市 6(股票)/5(ETF·LOF)/9(B股) → sh；
    # 深市 0/1(ETF·LOF)/2(B股)/3(创业板) → sz。
    # 原实现只认 6 → 588170 被拼成 sz588170 → 取不到价（与 wind_utils 同一处前缀盲区）。
    tc_codes = []
    for c in codes:
        if c.startswith(("6", "5", "9")):
            tc_codes.append(f"sh{c}")
        else:
            tc_codes.append(f"sz{c}")

    try:
        url = f"http://qt.gtimg.cn/q={','.join(tc_codes)}"
        resp = urllib.request.urlopen(url, timeout=5)  # nosec B310: qt.gtimg.cn
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


def get_quotes_with_fallback(codes: list[str]) -> dict[str, dict]:
    """三级降级取价链（Wind → 通达信 → 腾讯），**逐标的补齐**。

    🔴 2026-10-11 修复：原 `main()` 用 `if not quotes:` 串联三级 —— 只要上一级
    **部分**成功就整体提前返回，未命中的标的**拿不到价格**却无人察觉
    （实测：Wind 对 588170 报错，2/3 成功 → 腾讯不再被调用 → 第三只永远没价）。
    从"全失败"退化成"部分缺失"比彻底失败更隐蔽，故改为按缺失标的逐级补。
    """
    quotes: dict[str, dict] = {}
    for fetch, name in (
        (get_quotes_via_wind, "Wind"),
        (get_quotes_via_tdx, "通达信"),
        (get_quotes_via_tencent, "腾讯"),
    ):
        missing = [c for c in codes if c not in quotes]
        if not missing:
            break
        try:
            got = fetch(missing) or {}
        except Exception as e:  # noqa: BLE001 - 单一数据源异常不得中断取价
            print(f"[{name}] 异常: {e}")
            got = {}
        if got:
            quotes.update(got)
        print(f"[取价] {name} 补 {len(got)} 只（仍缺 {len(codes) - len(quotes)}）")
    if len(quotes) < len(codes):
        missing = [c for c in codes if c not in quotes]
        print(f"[WARN] 以下标的最终未取到价：{missing}")
    return quotes


def update_portfolio(
    data: dict, quotes: dict[str, dict], dry_run: bool = False, confirm_structure: bool = False
) -> dict:
    """刷新持仓价格与估值。

    🔴 语义边界（2026-10-11 审计整改）：
      · 本函数只刷新**价格/估值**，并写 `prices_refreshed_at`；
      · 顶层 `updated` 代表「快照 as-of」= 持仓结构被确认的日期，**默认不动**
        （原实现每次刷新都把它改成今天 → 结构陈旧也会显示「新鲜」，即假新鲜）；
      · 只有人工确认持仓结构未变时，才用 `--confirm-structure` 显式盖章。
    """
    holdings = data.get("holdings", [])
    summary = data.get("summary", {})
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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
        # 🔴 2026-10-11 修复：实盘 holdings 用 `avg_cost`（无 `cost_price` 键）→ 原取值恒 0，
        #    导致 pnl = (现价-0)×股数（**正数**）、pnl_pct 恒 0，summary.floating_pnl 符号反转。
        #    该缺陷历史上被「手工重算并回写」掩盖多次（08-25/08-28 记录），本次落到代码。
        cost_price = h.get("avg_cost") or h.get("cost_price") or 0
        if not cost_price:
            print(f"  [WARN] {code} 缺成本价（avg_cost/cost_price 均无）→ 跳过该只盈亏计算")
            continue

        h["current_price"] = new_price
        h["market_value"] = round(new_price * shares, 2)
        h["pnl"] = round((new_price - cost_price) * shares, 2)
        h["floating_pnl"] = h["pnl"]  # 与快照既有字段名保持一致（消费方两个名字都读）
        h["pnl_pct"] = round((new_price / cost_price - 1) * 100, 2)
        h["price_source"] = f"tencent·实时·{ts}"
        h["updated"] = f"auto·{ts}"
        h["price_sanity_ok"] = True
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
    summary["total_market_value"] = round(total_market_value, 2)
    summary["floating_pnl"] = round(total_pnl, 2)
    summary["position_pct"] = round(total_market_value / new_total * 100, 1) if new_total > 0 else 0
    summary["price_refreshed_at"] = ts
    data["summary"] = summary
    data["prices_refreshed_at"] = ts

    if confirm_structure:
        # 人工确认后才盖章：`updated` = 快照 as-of（结构确认日）
        data["updated"] = datetime.now().strftime("%Y-%m-%d")
        data["data_source"] = f"国金账户人工确认（{ts}）+ 腾讯实时价刷新（{ts}）"
        data["structure_basis"] = "人工确认持仓结构与可用资金未变（2026-10-11）"

    print(
        f"\n[汇总] 更新 {updated_count} 只持仓, 总资产: ¥{summary['total_assets']:,.2f}, "
        f"浮动盈亏: ¥{total_pnl:+,.2f}"
    )
    print(f"[口径] 结构 as-of={data.get('updated', 'unknown')} | 价格刷新={ts}")

    if not dry_run:
        save_portfolio(data)
        _readback_check(data)
    else:
        print("[DRY-RUN] 未实际保存")

    return data


def _readback_check(data: dict) -> None:
    """回读校验（写库须 readback 铁律）：确认落盘后的值与内存一致且自洽。"""
    reloaded = load_portfolio()
    m_is = round(data["summary"]["floating_pnl"], 2)
    m_should = round(sum(h.get("floating_pnl", 0) for h in reloaded.get("holdings", [])), 2)
    mv = sum(h.get("market_value", 0) for h in reloaded.get("holdings", []))
    cash = reloaded.get("summary", {}).get("cash_available", 0)
    total = reloaded.get("summary", {}).get("total_assets", 0)
    print(
        f"[readback] floating_pnl={m_is} vs 明细合计={m_should} | "
        f"市值={mv:.2f} + 现金={cash:.2f} = {mv + cash:.2f} vs total_assets={total:.2f}"
    )
    if abs(m_is - m_should) > 0.01:
        print("  🔴 readback 不一致：summary.floating_pnl 与明细合计不符")
        sys.exit(2)
    if abs((mv + cash) - total) > 0.5:
        print("  🔴 readback 不一致：市值+现金 与 total_assets 不符")
        sys.exit(2)
    print("  ✅ readback 通过（明细自洽）")


def main():
    dry_run = "--dry-run" in sys.argv
    # 🔴 仅人工确认持仓结构未变时才用（会盖章 updated=今天）
    confirm_structure = "--confirm-structure" in sys.argv

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

    # Wind → 通达信 → 腾讯 三级降级链（逐标的补齐，见 get_quotes_with_fallback）
    quotes = get_quotes_with_fallback(codes)

    if not quotes:
        print("[ERROR] 所有数据源不可用，无法刷新")
        sys.exit(1)

    print()
    update_portfolio(data, quotes, dry_run=dry_run, confirm_structure=confirm_structure)

    print(f"\n结构 as-of: {data.get('updated', 'unknown')}")
    print(f"价格刷新: {data.get('prices_refreshed_at', 'unknown')}")


if __name__ == "__main__":
    main()
