#!/usr/bin/env python3
"""
t0_daily_check.py — 做T盯盘自检（早盘 9:25 / 尾盘 14:25 双窗口）

落地: 2026-08-14，把《做T-SOP-清单.md》五段式流程固化成交易日自动盯盘：
  - morning   开盘前(9:25)自检 → 判势(R8情绪层)+T仓划线+目标价+大盘环境 → 飞书卡片
  - afternoon 尾盘(14:25)决策 → 实时价/VWAP/止损复核 → 正T/反T执行指令 → 飞书卡片

数据源: 腾讯 qt.gtimg 优先（行情）+ 腾讯 fqkline（K线），与 t0_strategy.py 一致。
推送: push_card.py 交互卡片 → 飞书主群 oc_9ee5303497f5e0e71666b610d6bdc346。
非交易日/休市自动跳过（is_trading_day.py）。

用法:
  python3 scripts/t0_daily_check.py morning
  python3 scripts/t0_daily_check.py afternoon
  python3 scripts/t0_daily_check.py morning --no-push   # 只打印不推送(调试)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from datetime import datetime, time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
USER_PORTFOLIO = PROJECT_ROOT / ".workbuddy" / "data" / "user" / "portfolio.json"
PUSH_CARD = PROJECT_ROOT / ".workbuddy" / "scripts" / "push_card.py"
DEFAULT_CHAT = "oc_9ee5303497f5e0e71666b610d6bdc346"
SH_INDEX_CODE = "sh000001"  # 上证指数

sys.path.insert(0, str(SCRIPTS_DIR))
from is_trading_day import is_trading_day as _is_trading_day  # noqa: E402
from is_trading_day import load_holidays
from t0_strategy import T0Strategy  # noqa: E402


# ── 大盘环境 ────────────────────────────────────────────────
def fetch_index_quote(code: str = SH_INDEX_CODE) -> dict | None:
    """拉上证指数实时 {price, change_pct}。腾讯 qt.gtimg，失败返回 None 不阻断。"""
    try:
        url = f"https://qt.gtimg.cn/q={code}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:  # nosec B310
            raw = resp.read().decode("gbk", errors="ignore")
        # 格式: v_sh000001="1~上证指数~000001~3123.45~3120.00~...~涨跌~涨跌幅~..."
        parts = raw.split('"')[1].split("~")
        if len(parts) < 33:
            return None
        return {"name": parts[1], "price": float(parts[3]), "change_pct": float(parts[32])}
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️ 大盘行情获取失败: {e}")
        return None


def market_gate(index: dict | None) -> dict:
    """大盘闸门（对标 YRFX/lianghua 大盘风控一票否决）：
    上证跌 >1% → 当日暂停做T；-1%~-0.5% → 减半T仓。"""
    if index is None:
        return {"pass": True, "note": "大盘数据不可用，不阻断（默认放行）"}
    pct = index["change_pct"]
    if pct <= -1.0:
        return {"pass": False, "note": f"上证 {pct:+.2f}%（单边大跌）→ 当日暂停做T，保本优先"}
    if pct <= -0.5:
        return {"pass": True, "note": f"上证 {pct:+.2f}%（偏弱）→ 允许做T但 T 仓减半"}
    return {"pass": True, "note": f"上证 {pct:+.2f}% → 大盘环境正常，正常做T"}


# ── 持仓遍历 ────────────────────────────────────────────────
def load_holdings(path: Path = USER_PORTFOLIO) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("holdings", [])
    except Exception as e:  # noqa: BLE001
        print(f"🔴 持仓读取失败 {path}: {e}")
        return []


def build_eval_row(strategy: T0Strategy, holding: dict, t_count: int = 0) -> dict:
    """对单只持仓跑 t0_strategy.evaluate，返回展平后的展示行。"""
    result = strategy.evaluate(holding, t_count_today=t_count)
    code = holding.get("code", "")
    name = holding.get("name", "")
    direction = result.get("direction", "?")
    t_shares = result.get("t_position_shares")
    pivot = result.get("pivot") or {}
    plan = result.get("plan") or {}
    flags = result.get("flags") or []
    stop = next(
        (f["reason"] for f in flags if f.get("rule") == "R11"), result.get("stop_loss_note", "")
    )
    return {
        "code": code,
        "name": name,
        "direction": direction,
        "t_shares": t_shares,
        "t_value": result.get("t_position_value"),
        "vwap_note": result.get("vwap_note", ""),
        "stop": stop,
        "s1": pivot.get("S1"), "p": pivot.get("P"), "r1": pivot.get("R1"),
        "entry_rule": plan.get("entry_rule", ""),
        "exit_rule": plan.get("exit_rule", ""),
        "summary": result.get("summary", ""),
        "blocked": result.get("blocked", False),
        "t0": result.get("t0", False),
    }


def fmt_row(row: dict) -> str:
    """单标的展示文本（供卡片 section 使用）。"""
    lines = [
        f"🎯 {row['name']}({row['code']}) → **{row['direction']}**",
    ]
    if row["t_shares"]:
        lines.append(f"  📐 T仓: {row['t_shares']}股 (~¥{row['t_value']:.0f})")
    if row["s1"] and row["r1"]:
        lines.append(f"  💰 挂单价: S1低吸¥{row['s1']:.2f} / P¥{row['p']:.2f} / R1高抛¥{row['r1']:.2f}")
    if row["vwap_note"]:
        lines.append(f"  📊 {row['vwap_note']}")
    if row["stop"]:
        lines.append(f"  🛑 止损: {row['stop']}")
    if row["entry_rule"]:
        lines.append(f"  📝 买/卖: {row['entry_rule']}")
    if row["exit_rule"]:
        lines.append(f"  📝 回补: {row['exit_rule']}")
    if row["blocked"]:
        lines.append("  ⛔ 被风控拦截")
    return "\n".join(lines)


# ── 推送 ────────────────────────────────────────────────────
def push_card(title: str, sections: list[tuple[str, str]], footer: str, chat_id: str = DEFAULT_CHAT) -> bool:
    """经 push_card.py 发交互卡片（含 429 退避 + markdown 兜底）。"""
    cmd = [
        sys.executable, str(PUSH_CARD),
        "--title", title,
        "--level", "info",
        "--footer", footer,
        "--chat-id", chat_id,
    ]
    for sec_title, body in sections:
        cmd += ["--section", sec_title, body]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        if r.returncode == 0:
            print(f"  ✅ 卡片已推送: {title}")
            return True
        print(f"  🔴 push_card 失败 rc={r.returncode}: {(r.stdout or r.stderr)[-300:]}")
        return False
    except Exception as e:  # noqa: BLE001
        print(f"  🔴 push_card 异常: {e}")
        return False


# ── 模式：早盘 9:25 自检 ────────────────────────────────────
def run_morning(strategy: T0Strategy, push: bool) -> None:
    now = datetime.now()
    idx = fetch_index_quote()
    gate = market_gate(idx)
    rows = [build_eval_row(strategy, h) for h in load_holdings()]

    # 早盘自检正文
    sec_market = (
        f"{idx['name']} {idx['price']:.2f}（{idx['change_pct']:+.2f}%）\n{gate['note']}"
        if idx else gate["note"]
    )
    secs = [("🌍 大盘环境", sec_market)]
    for row in rows:
        if not row["t0"]:
            secs.append((f"📭 {row['name']}", "无持仓底仓，做T需先有底仓（R7）"))
        else:
            secs.append((f"🎯 {row['name']} 今日方向", fmt_row(row)))

    footer = (
        f"做T-SOP自检 | {now:%m-%d %H:%M} | "
        "纪律: T仓≤底仓1/10·单次亏≥1.5%即止损·日交易≤2次·T仓绝不加仓成底仓"
    )
    print(f"🔔 [早盘9:25] 大盘: {gate['note']} | 标的 {len(rows)} 只")
    if push:
        push_card(f"📋 做T早盘自检 {now:%m-%d}", secs, footer)
    else:
        for t, b in secs:
            print(f"── {t} ──\n{b}\n")


# ── 模式：尾盘 14:25 决策 ───────────────────────────────────
def run_afternoon(strategy: T0Strategy, push: bool) -> None:
    now = datetime.now()
    idx = fetch_index_quote()
    gate = market_gate(idx)
    rows = [build_eval_row(strategy, h, t_count=1) for h in load_holdings()]

    secs = [("🌍 大盘环境", (f"{idx['name']} {idx['price']:.2f}（{idx['change_pct']:+.2f}%）\n{gate['note']}" if idx else gate["note"]))]
    for row in rows:
        if row["blocked"]:
            secs.append((f"⛔ {row['name']}", "被风控拦截，本时段不做T"))
        elif not row["t0"]:
            secs.append((f"📭 {row['name']}", "无持仓底仓，无法做T（R7）"))
        else:
            secs.append((f"🎯 {row['name']} 尾盘决策", fmt_row(row)))

    footer = (
        f"做T-SOP决策 | {now:%m-%d %H:%M} | "
        "主窗口14:30-14:50 · 正T需买入<成本-2% · 破位>2%只反T或停手 · 亏≥1.5%立即止损"
    )
    print(f"🔔 [尾盘14:25] 大盘: {gate['note']} | 标的 {len(rows)} 只")
    if push:
        push_card(f"📋 做T尾盘决策 {now:%m-%d}", secs, footer)
    else:
        for t, b in secs:
            print(f"── {t} ──\n{b}\n")


# ── 入口 ────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="做T盯盘自检（早盘9:25 / 尾盘14:25）")
    parser.add_argument("mode", choices=["morning", "afternoon"], help="morning=早盘自检, afternoon=尾盘决策")
    parser.add_argument("--no-push", action="store_true", help="只打印不推送(调试)")
    parser.add_argument("--portfolio", default=str(USER_PORTFOLIO), help="portfolio.json路径(默认Claw持仓)")
    args = parser.parse_args()

    # 交易日闸门
    today = datetime.now().date()
    try:
        holidays = load_holidays()
        if not _is_trading_day(today, holidays):
            print(f"📅 {today} 非交易日，跳过做T盯盘")
            return
    except SystemExit:
        pass  # holiday 文件缺失时放行，由行情接口兜底

    strategy = T0Strategy()
    if args.mode == "morning":
        run_morning(strategy, push=not args.no_push)
    else:
        run_afternoon(strategy, push=not args.no_push)


if __name__ == "__main__":
    main()
