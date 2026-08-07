#!/usr/bin/env python3
"""
fetch_holdings_quotes.py — 获取持仓个股实时行情

从 portfolio.json 读取持仓代码 → Wind 万得(优先) → 腾讯(降级) → 输出 JSON

用法:
    python3 fetch_holdings_quotes.py              → 用户实盘持仓
    python3 fetch_holdings_quotes.py --user       → 同上（显式指定）
    python3 fetch_holdings_quotes.py --sim        → 模拟盘持仓

输出: JSON { "quotes": [...], "data_source": "wind"/"tencent" }
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from price_sanity import check as _price_sanity  # noqa: E402
from wind_quote import fetch_quotes  # noqa: E402


def _prefix(code: str) -> str:
    """补齐交易所前缀（腾讯接口需要 sh/sz）。"""
    code = str(code).strip()
    if code[:2] in ("sh", "sz"):
        return code
    if code[0] in ("6",):
        return "sh" + code
    if code[0] in ("0", "3", "2"):
        return "sz" + code
    return code


def _apply_sanity(item: dict) -> dict:
    """对 current_price 做合理性校验，偏差>30%或超52周区间→标记失败+可信价。

    防御目标：杜绝 8/6 早报同类事故——Wind/腾讯降级或返回错误价时，
    监控报告仍拿可疑价算盈亏/止损价。
    """
    price = item.get("current_price")
    if price is None or not isinstance(price, (int, float)) or price <= 0:
        return item
    try:
        res = _price_sanity(_prefix(item["code"]), float(price))
    except Exception:
        # 校验器自身失败不影响主流程，仅跳过校验
        return item
    item["price_sanity"] = {
        "ok": res["ok"],
        "verified_price": res.get("verified_price"),
        "gtimg_price": res.get("gtimg_price"),
        "fail_reasons": res.get("fail_reasons", []),
        "action": res.get("action"),
    }
    if not res["ok"]:
        item["price_sanity_fail"] = True
        item["reliable_current_price"] = res.get("verified_price")
    return item

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent  # .workbuddy/scripts/ → Claw
USER_DATA = PROJECT_DIR / ".workbuddy" / "data" / "user" / "portfolio.json"
SIM_DATA = PROJECT_DIR / ".workbuddy" / "data" / "simulation" / "portfolio.json"


def _load_portfolio(path: Path) -> list[dict]:
    if not path.exists():
        print(json.dumps({"error": f"文件不存在: {path}", "quotes": []}, ensure_ascii=False))
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    # 优先读 positions（模拟盘权威源，由 sim_trade.py 维护）；
    # 无 positions 时回退 holdings（实盘 user/portfolio.json 结构）。
    # 背景：sim 的 holdings 数组是遗留死副本，不随交易更新（2026-07-27 曾漏招商600036）。
    positions = data.get("positions")
    if isinstance(positions, dict) and positions:
        return [
            {
                "code": code,
                "name": p.get("name", ""),
                "shares": p.get("shares", 0),
                "avg_cost": p.get("avg_cost", 0),
            }
            for code, p in positions.items()
        ]
    return data.get("holdings", [])  # type: ignore[no-any-return]


def _merge(holdings: list[dict], quotes: dict[str, dict]) -> list[dict]:
    """合并持仓信息与实时行情"""
    result = []
    for h in holdings:
        code = h["code"]
        q = quotes.get(code, {})
        price = q.get("price")
        cost = h.get("avg_cost", 0)
        shares = h.get("shares", 0)

        item = {
            "code": code,
            "name": q.get("name") or h.get("name", ""),
            "shares": shares,
            "avg_cost": cost,
            "current_price": price,
            "change": q.get("change"),
            "change_pct": q.get("change_pct"),
            "prev_close": q.get("prev_close"),
            "open": q.get("open"),
            "high": q.get("high"),
            "low": q.get("low"),
            "volume": q.get("volume"),
            "amount": q.get("amount"),
            "turnover": q.get("turnover"),
        }
        if price is not None and cost and cost > 0:
            item["pnl"] = round((price - cost) * shares, 2)
            item["pnl_pct"] = round((price - cost) / cost * 100, 2)
        else:
            item["pnl"] = None
            item["pnl_pct"] = None
        result.append(item)
    return result


if __name__ == "__main__":
    use_sim = "--sim" in sys.argv
    portfolio_path = SIM_DATA if use_sim else USER_DATA

    holdings = _load_portfolio(portfolio_path)
    if not holdings:
        print(
            json.dumps(
                {"quotes": [], "source": str(portfolio_path), "note": "无持仓"}, ensure_ascii=False
            )
        )
        sys.exit(0)

    codes = [h["code"] for h in holdings]
    quotes = fetch_quotes(codes)
    data_source = quotes.pop("_source", "tencent")
    output = _merge(holdings, quotes)

    # 价格防错兜底（2026-08-07 落地，根因=8/6早报选股价数量级错误）
    sanity_failed = 0
    for item in output:
        _apply_sanity(item)
        if item.get("price_sanity_fail"):
            sanity_failed += 1

    print(
        json.dumps(
            {
                "quotes": output,
                "source": str(portfolio_path),
                "data_source": data_source,
                "sanity_failed": sanity_failed,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
