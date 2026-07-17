#!/usr/bin/env python3
"""
fetch_holdings_quotes.py — 获取持仓个股实时行情

从 portfolio.json 读取持仓代码 → 腾讯行情接口拉取实时报价 → 输出 JSON

用法:
    python3 fetch_holdings_quotes.py              → 用户实盘持仓
    python3 fetch_holdings_quotes.py --user       → 同上（显式指定）
    python3 fetch_holdings_quotes.py --sim        → 模拟盘持仓

输出: JSON { "quotes": [...] }，供「综合持仓监控」自动化 PHASE 2/3 消费

腾讯 qt.gtimg.cn 行情字段索引:
  [1]名称 [2]代码 [3]现价 [4]昨收 [5]今开 [6]成交量(手)
  [31]涨跌额 [32]涨跌幅% [33]最高 [34]最低
  [37]成交额(万元) [38]换手率% [43]振幅% [45]总市值(亿)
"""

import json
import sys
import urllib.request
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent  # .workbuddy/scripts/ → Claw
USER_DATA = PROJECT_DIR / ".workbuddy" / "data" / "user" / "portfolio.json"
SIM_DATA = PROJECT_DIR / ".workbuddy" / "data" / "simulation" / "portfolio.json"

QT_URL = "https://qt.gtimg.cn/q={}"


def _code_prefix(code: str) -> str:
    """600xxx → sh600xxx, 000xxx → sz000xxx"""
    code = code.strip()
    if code.startswith(("6", "5")):
        return f"sh{code}"
    if code.startswith(("0", "3")):
        return f"sz{code}"
    return code


def _load_portfolio(path: Path) -> list[dict]:
    if not path.exists():
        print(json.dumps({"error": f"文件不存在: {path}", "quotes": []}, ensure_ascii=False))
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("holdings", [])


def _parse_line(line: str) -> dict | None:
    """解析单行 qt 行情 v_sh600522="..." → dict"""
    if "=" not in line or line.startswith("pv_none_match"):
        return None
    try:
        vals = line.split('"')[1].split("~")
    except IndexError:
        return None
    if len(vals) < 40:
        return None

    def f(i):
        try:
            return float(vals[i])
        except (ValueError, TypeError):
            return None

    def s(i):
        return vals[i]

    return {
        "name": s(1),
        "code": s(2),
        "price": f(3),
        "prev_close": f(4),
        "open": f(5),
        "volume": int(vals[6]) if vals[6].isdigit() else None,
        "change": f(31),
        "change_pct": f(32),
        "high": f(33),
        "low": f(34),
        "amount": f(37),
        "turnover": f(38),
    }


def _fetch(codes: list[str]) -> dict[str, dict]:
    """批量拉取实时行情，返回 {code: quote_dict}"""
    if not codes:
        return {}
    qt_codes = [_code_prefix(c) for c in codes]
    url = QT_URL.format(",".join(qt_codes))

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
        text = raw.decode("gbk", errors="replace")
    except Exception as e:
        return {c: {"error": str(e)} for c in codes}

    results: dict[str, dict] = {}
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        parsed = _parse_line(line)
        if parsed and parsed.get("code"):
            results[parsed["code"]] = parsed
    return results


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
        print(json.dumps({"quotes": [], "source": str(portfolio_path), "note": "无持仓"}, ensure_ascii=False))
        sys.exit(0)

    codes = [h["code"] for h in holdings]
    quotes = _fetch(codes)
    output = _merge(holdings, quotes)

    print(json.dumps({
        "quotes": output,
        "source": str(portfolio_path),
    }, ensure_ascii=False, indent=2))
