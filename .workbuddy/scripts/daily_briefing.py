#!/usr/bin/env python3
"""daily_briefing.py — 盘前持仓 / 盘面摘要推送（飞书）

读取 实盘(user/portfolio.json) + 模拟盘(simulation/portfolio.json)，
刷新腾讯 qt.gtimg 实时价，生成结构化摘要卡片推飞书群。

用法:
  python3 daily_briefing.py            # 默认 dry-run（仅打印，不推送）
  python3 daily_briefing.py --push    # 实际推送飞书群（自动化调用）

设计:
  - 实时价铁律: 盘中/监控取价走腾讯 qt.gtimg.cn（Wind 仅降级兜底），本脚本同理。
  - 非交易时段 qt.gtimg 返回最近收盘，摘要中标注来源，不伪造时间戳。
  - 止损判定: 实盘/主板 -8%、创业板(300/301) -15%（与 sim_trade.py 口径一致）。
"""

from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # .workbuddy
DATA = ROOT / "data"
SCRIPTS = ROOT / "scripts"
QT_URL = "https://qt.gtimg.cn/q={}"


def code_prefix(code: str) -> str:
    """沪深代码加交易所前缀；科创/北交按 sh 处理。"""
    if code.startswith(("60", "68", "8", "4")):
        return "sh" + code
    return "sz" + code


def fetch_price(code: str) -> dict:
    """返回 {price, prev_close, pct, name} 或 {error}。"""
    try:
        raw = (
            urllib.request.urlopen(QT_URL.format(code_prefix(code)), timeout=8).read().decode("gbk")
        )
        s = raw.split('"')[1]
        f = s.split("~")
        cur = float(f[3])
        prev = float(f[4])
        pct = (cur - prev) / prev * 100 if prev else 0.0
        return {"price": cur, "prev_close": prev, "pct": round(pct, 2), "name": f[1]}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def load_json(p: Path) -> dict:
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def sim_positions() -> dict:
    return load_json(DATA / "simulation" / "portfolio.json").get("positions", {})


def user_holdings() -> list:
    return load_json(DATA / "user" / "portfolio.json").get("holdings", [])


def fmt_money(x) -> str:
    try:
        return f"¥{float(x):,.0f}"
    except (TypeError, ValueError):
        return "¥?"


def build_items(raw_holdings, key_map, default_thr=-8.0):
    """统一把持仓列表/字典转成展示条目。"""
    items = []
    for code, h in raw_holdings:
        q = fetch_price(code)
        if "error" in q:
            pnl = h.get("pnl_pct", 0.0) or 0.0
            items.append(
                {
                    "code": code,
                    "name": h.get("name", code),
                    "price": "?",
                    "pct": 0.0,
                    "pnl": round(pnl, 2),
                    "mv": h.get("market_value", 0) or 0,
                    "flag": "⚠️ 取价失败",
                }
            )
            continue
        avg = h.get("avg_cost", 0) or 0
        pnl = (q["price"] - avg) / avg * 100 if avg else 0.0
        thr = -15.0 if code.startswith(("300", "301")) else default_thr
        flag = f"🔴 破止损(≤{thr:.0f}%)" if pnl <= thr else ""
        items.append(
            {
                "code": code,
                "name": q["name"],
                "price": q["price"],
                "pct": q["pct"],
                "pnl": round(pnl, 2),
                "mv": h.get("market_value", 0) or 0,
                "flag": flag,
            }
        )
    return items


def section(title, items) -> list:
    out = [f"### {title}"]
    if not items:
        out.append("- （空）")
        return out
    for it in items:
        price = it["price"] if it["price"] != "?" else "?"
        pct = f"{it['pct']:+.2f}%" if isinstance(it["pct"], (int, float)) else ""
        out.append(
            f"- {it['name']}({it['code']}) 现价 {price} {pct} ｜ 持仓 {it['pnl']:+.2f}% ｜ 市值 {fmt_money(it['mv'])} {it['flag']}"
        )
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true", help="实际推送飞书（默认 dry-run）")
    args = ap.parse_args()

    # 实盘: 列表 [(code, holding)]
    real_raw = [(h["code"], h) for h in user_holdings()]
    # 模拟盘: 字典 -> [(code, position)]
    sim_raw = list(sim_positions().items())

    real_items = build_items(real_raw, None, default_thr=-8.0)
    sim_items = build_items(sim_raw, None, default_thr=-8.0)

    today = datetime.date.today().strftime("%Y-%m-%d")
    weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][
        datetime.date.today().weekday()
    ]

    body = [f"📅 **盘前持仓 / 盘面摘要 · {today} {weekday}**", ""]
    body += section("📊 实盘（国金）", real_items)
    body += section("📈 模拟盘（投顾）", sim_items)
    body += [
        "",
        "---",
        "⏰ 盘前 08:50 自动推送 ｜ 实时价来源 腾讯 qt.gtimg.cn（非交易时段为最近收盘，不伪造时间戳）",
    ]

    text = "\n".join(body)
    title = f"盘前摘要 {today}"

    if args.push:
        r = subprocess.run(
            ["bash", str(SCRIPTS / "push_feishu.sh"), title, text],
            capture_output=True,
            text=True,
        )
        print(r.stdout)
        if r.stderr:
            print(r.stderr, file=__import__("sys").stderr)
        print("PUSH exit:", r.returncode)
    else:
        print("=== DRY-RUN（不加 --push 不推送）===")
        print(title)
        print(text)


if __name__ == "__main__":
    main()
