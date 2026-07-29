#!/usr/bin/env python3
"""北辰多智能体辩论 CLI

用法:
    # 单只股票辩论（数据内嵌）
    python3 scripts/run_debate.py --code 000333 --name "美的集团" --price 85.92 --change 1.6 \
        --pe 14.5 --pb 3.2 --roe 22 --rsi 58 --macd bullish --sector 家用电器

    # 从 scan 候选 JSON 批量
    python3 scripts/run_debate.py --from-scan output/scan_candidates.json

    # 对当前持仓辩论
    python3 scripts/run_debate.py --from-holdings .workbuddy/data/simulation/portfolio.json

    # 查看最近辩论结果
    python3 scripts/run_debate.py --latest
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 确保 src/claw 在 path 中
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from claw.debate import batch_debate, run_debate

RESULT_FILE = Path(__file__).parent.parent / ".workbuddy" / "data" / "debate" / "debate_result.json"


def parse_args():
    p = argparse.ArgumentParser(description="北辰多智能体辩论")
    p.add_argument("--code", help="股票代码（6位）")
    p.add_argument("--name", help="股票名称")
    p.add_argument("--price", type=float, help="最新价")
    p.add_argument("--change", type=float, default=0, help="涨跌幅%")
    p.add_argument("--pe", type=float, help="市盈率")
    p.add_argument("--pb", type=float, help="市净率")
    p.add_argument("--roe", type=float, help="ROE(%)")
    p.add_argument("--rsi", type=float, help="RSI")
    p.add_argument("--macd", help="MACD信号(bullish/bearish/neutral)")
    p.add_argument("--sector", default="未知", help="所属行业")
    p.add_argument("--mcap", help="市值")
    p.add_argument("--from-scan", help="从 scan 候选 JSON 批量")
    p.add_argument("--from-holdings", help="从持仓 JSON 批量")
    p.add_argument("--codes", help="逗号分隔的股票代码列表，自动拉行情后批量辩论")
    p.add_argument("--latest", action="store_true", help="查看最近辩论结果")
    p.add_argument("--dry-run", action="store_true", help="仅打印参数，不调 LLM")
    return p.parse_args()


def build_data(args) -> dict:
    """从 CLI 参数构建 data dict"""
    data = {
        "price": args.price,
        "change_pct": args.change,
        "sector": args.sector,
        "market_cap": args.mcap or "N/A",
        "technical": {},
        "fundamental": {},
        "fund_flow": {},
        "sentiment": {},
    }
    if any([args.rsi, args.macd]):
        tech = {}
        if args.rsi is not None:
            tech["rsi"] = args.rsi
        if args.macd:
            tech["macd"] = args.macd
        data["technical"] = tech
    if any([args.pe, args.pb, args.roe]):
        fund = {}
        if args.pe is not None:
            fund["pe"] = args.pe
        if args.pb is not None:
            fund["pb"] = args.pb
        if args.roe is not None:
            fund["roe"] = f"{args.roe}%"
        data["fundamental"] = fund
    return data


def debate_from_scan(path: str):
    """从 scan 候选 JSON 批量辩论"""
    scan_file = Path(path)
    if not scan_file.exists():
        print(f"错误：{path} 不存在")
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        scan_data = json.load(f)

    stocks = []
    for item in scan_data if isinstance(scan_data, list) else scan_data.get("candidates", []):
        stocks.append({
            "code": item.get("code", ""),
            "name": item.get("name", ""),
            "data": item.get("data", {}),
        })

    if not stocks:
        print("无候选股需要辩论")
        return

    print(f"开始对 {len(stocks)} 只候选股进行多智能体辩论...\n")
    results = batch_debate(stocks)
    _print_summary(results)


def debate_from_codes(codes_str: str):
    """从逗号分隔的代码列表自动拉行情后批量辩论"""
    import re
    import subprocess

    codes = [c.strip() for c in codes_str.split(",") if c.strip()]
    if not codes:
        print("无股票代码")
        return

    # 自动拉取行情
    market_codes = ",".join(
        f"sh{c}" if c.startswith("6") else f"sz{c}" for c in codes
    )
    quotes = {}
    try:
        raw = subprocess.run(
            ["curl", "-s", f"http://qt.gtimg.cn/q={market_codes}"],
            capture_output=True, text=True, timeout=10,
        )
        for line in raw.stdout.split("\n"):
            m = re.search(r'v_\w+="[^"]*~([^~]*)~([^~]*)~([^~]*)~', line)
            if m:
                qt_name = m.group(1)
                qt_price = float(m.group(3)) if m.group(3).replace(".", "").replace("-", "").isdigit() else 0
                # 用代码匹配（非名称）
                for c in codes:
                    if c in line:
                        quotes[c] = {"name": qt_name, "price": qt_price}
    except Exception:
        pass

    stocks = []
    for c in codes:
        q = quotes.get(c, {})
        stocks.append({
            "code": c,
            "name": q.get("name", c),
            "data": {"price": q.get("price", 0), "change_pct": 0},
        })

    print(f"开始对 {len(stocks)} 只股票进行多智能体辩论...\n")
    results = batch_debate(stocks)
    _print_summary(results)


def debate_from_holdings(path: str):
    """从持仓 JSON 批量辩论，自动拉取实时行情和基本面数据"""
    pf_file = Path(path)
    if not pf_file.exists():
        print(f"错误：{path} 不存在")
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        pf = json.load(f)

    positions = pf.get("positions", {})
    if not positions:
        print("无持仓")
        return

    # 自动拉取行情
    codes = list(positions.keys())
    market_codes = ",".join(f"sh{c}" if c.startswith("6") else f"sz{c}" for c in codes)
    quotes = {}
    try:
        import re
        import subprocess
        raw = subprocess.run(
            ["curl", "-s", f"http://qt.gtimg.cn/q={market_codes}"],
            capture_output=True, text=True, timeout=10,
        )
        for line in raw.stdout.split("\n"):
            m = re.search(r'v_\w+="[^"]*~([^~]*)~([^~]*)~([^~]*)~', line)
            if m:
                name = m.group(1)
                price = float(m.group(3)) if m.group(3).replace(".", "").isdigit() else 0
                for code in codes:
                    if code in name or code in line:
                        quotes[code] = price
    except Exception:
        pass

    stocks = []
    for code, pos in positions.items():
        price = quotes.get(code, pos.get("current_price", pos.get("avg_cost", 0)))
        prev_close = pos.get("avg_cost", price)
        change_pct = round((price - prev_close) / prev_close * 100, 2) if prev_close and prev_close > 0 else 0
        stocks.append({
            "code": code,
            "name": pos.get("name", code),
            "data": {
                "price": price,
                "change_pct": change_pct,
                "sector": pos.get("sector", "未知"),
            },
        })

    print(f"开始对 {len(stocks)} 只持仓进行多智能体辩论...\n")
    results = batch_debate(stocks)
    _print_summary(results)


def _print_summary(results: list[dict]):
    for r in results:
        v = r.get("verdict", {})
        buys = sum(1 for s in r.get("stances", []) if s["stance"] == "BUY")
        holds = sum(1 for s in r.get("stances", []) if s["stance"] == "HOLD")
        sells = sum(1 for s in r.get("stances", []) if s["stance"] == "SELL")
        sl = v.get("stop_loss_pct", -8.0)
        fs = v.get("factor_scores", {})
        factor_str = (
            f"V{fs.get('value',50)}/Q{fs.get('quality',50)}"
            f"/G{fs.get('growth',50)}/M{fs.get('momentum',50)}"
        )
        print(f"  {r['name']}({r['code']}): {v.get('consensus','?')} "
              f"[{buys}B/{holds}H/{sells}S] "
              f"conf={v.get('confidence',0):.0%} "
              f"止损{sl:+.0f}% "
              f"因子[{factor_str}] "
              f"({v.get('summary','')[:60]})")


def show_latest():
    """查看最近辩论结果"""
    if not RESULT_FILE.exists():
        print("暂无辩论结果")
        return
    with open(RESULT_FILE, encoding="utf-8") as f:
        records = json.load(f)
    for r in records[-5:]:
        v = r.get("verdict", {})
        fs = v.get("factor_scores", {})
        factor_str = (
            f"V{fs.get('value',50)}/Q{fs.get('quality',50)}"
            f"/G{fs.get('growth',50)}/M{fs.get('momentum',50)}"
        ) if fs else ""
        print(f"[{r.get('timestamp','')[:16]}] {r.get('name','')}({r.get('code','')}): "
              f"{v.get('consensus','?')} | conf={v.get('confidence',0):.0%} | "
              f"止损{v.get('stop_loss_pct',-8.0):+.0f}% | {factor_str} | "
              f"{v.get('summary','')[:80]}")


def main():
    args = parse_args()

    if args.latest:
        show_latest()
        return

    if args.from_scan:
        debate_from_scan(args.from_scan)
        return

    if args.from_holdings:
        debate_from_holdings(args.from_holdings)
        return

    if args.codes:
        debate_from_codes(args.codes)
        return

    if not args.code:
        print("请提供 --code 或 --from-scan/--from-holdings/--codes/--latest")
        sys.exit(1)

    data = build_data(args)

    if args.dry_run:
        print(f"DRY RUN: {args.name}({args.code}), data={json.dumps(data, ensure_ascii=False)}")
        return

    result = run_debate(args.code, args.name or args.code, data)
    _print_summary([result])


if __name__ == "__main__":
    main()
