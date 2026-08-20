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


# 实盘止损线（系统铁律）：主板-8% / 创业板-15%（300/301开头）
STOP_LOSS_MAIN = -0.08
STOP_LOSS_CYB = -0.15


def _is_stop_loss_hit(holding: dict) -> bool:
    """已破实盘止损线 → 不做T，执行清仓优先。"""
    cost = holding.get("avg_cost") or 0
    price = holding.get("current_price") or cost
    if not cost or cost <= 0:
        return False
    pnl = price / cost - 1
    code = str(holding.get("code", ""))
    threshold = STOP_LOSS_CYB if code.startswith(("300", "301")) else STOP_LOSS_MAIN
    return pnl <= threshold


def build_eval_row(
    strategy: T0Strategy, holding: dict, t_count: int = 0, gate_pass: bool = True
) -> dict:
    """对单只持仓跑 t0_strategy.evaluate，返回展平后的展示行。
    gate_pass=False 时（大盘单边大跌/暴击）→ 除止损票清仓提示外，统一停手。"""
    code = holding.get("code", "")
    name = holding.get("name", "")
    # 前置：止损票 → 清仓优先（不做T降成本，独立于大盘闸）
    if _is_stop_loss_hit(holding):
        cost = holding.get("avg_cost") or 0
        price = holding.get("current_price") or cost
        pnl = (price / cost - 1) * 100 if cost else 0
        return {
            "code": code,
            "name": name,
            "direction": "不动(清仓优先)",
            "stop_loss_hit": True,
            "pnl_pct": pnl,
            "t_shares": None,
            "t_value": None,
            "t_cost": None,
            "vwap_note": "",
            "stop": "",
            "s1": None,
            "p": None,
            "r1": None,
            "entry_rule": "",
            "exit_rule": "",
            "summary": f"已破-8%止损线({pnl:.1f}%)，执行清仓计划，不做T",
            "blocked": True,
            "t0": True,
        }
    # 大盘闸不通过 → 停手（正T反T都不做）
    if not gate_pass:
        return {
            "code": code,
            "name": name,
            "direction": "不动(大盘停手)",
            "stop_loss_hit": False,
            "pnl_pct": None,
            "t_shares": None,
            "t_value": None,
            "t_cost": None,
            "vwap_note": "",
            "stop": "",
            "s1": None,
            "p": None,
            "r1": None,
            "entry_rule": "",
            "exit_rule": "",
            "summary": "大盘单边大跌，当日暂停做T，保本优先",
            "blocked": True,
            "t0": True,
        }
    result = strategy.evaluate(holding, t_count_today=t_count)
    pivot = result.get("pivot") or {}
    plan = result.get("plan") or {}
    flags = result.get("flags") or []
    stop = next(
        (f["reason"] for f in flags if f.get("rule") == "R11"), result.get("stop_loss_note", "")
    )
    return {
        "code": code,
        "name": name,
        "direction": result.get("direction", "?"),
        "stop_loss_hit": False,
        "pnl_pct": None,
        "t_shares": result.get("t_position_shares"),
        "t_value": result.get("t_position_value"),
        "t_cost": result.get("t_lot_cost"),
        "vwap_note": result.get("vwap_note", ""),
        "stop": stop,
        "s1": pivot.get("S1"),
        "p": pivot.get("P"),
        "r1": pivot.get("R1"),
        "buy_below": result.get("buy_below"),
        "entry_rule": plan.get("entry_rule", ""),
        "exit_rule": plan.get("exit_rule", ""),
        "summary": result.get("summary", ""),
        "blocked": result.get("blocked", False),
        "t0": result.get("t0", False),
    }


def fmt_row(row: dict) -> str:
    """单标的极简展示（用户可直接照着下单）。

    模板（2026-08-19 用户要求"简单明了"后固化）：
      🎯 标的 → 方向
      理由：一句话
      ─ 正T ─ 买¥X / 卖¥Y / T仓N股 / 止损≥1.5%即平
      ─ 反T ─ 卖¥X / 买回¥Y / T仓N股 / 止损≥1.5%即平
      ─ 不动 ─ 原因
    """
    direction = row["direction"]
    lines = [f"🎯 {row['name']}({row['code']}) → **{direction}**"]

    if row.get("stop_loss_hit"):
        lines.append(f"  理由: 已破-8%止损线({row['pnl_pct']:.1f}%)，执行清仓计划，不做T降成本")
        lines.append("  操作: 持有等下一开盘清仓，今日不买不卖")
        return "\n".join(lines)

    if direction == "正T":
        buy = row.get("buy_below")
        sell = row.get("r1")
        t = row.get("t_shares")
        lines.append("  理由: 趋势向上(价>MA20)，低买高卖做正T")
        if buy:
            lines.append(f"  ✅ 买: ¥{buy:.2f}（≤成本-2%安全垫）")
        if sell:
            lines.append(f"  ✅ 卖: ¥{sell:.2f}（反弹到压力位R1高抛原底仓）")
        if t:
            lines.append(f"  📐 T仓: {t}股（整手，当日只做1次）")
        lines.append("  🛑 止损: 单次亏≥1.5%立即平T仓")
    elif direction == "反T":
        sell = row.get("r1")
        buy = row.get("s1")
        t = row.get("t_shares")
        lines.append("  理由: 破位或冲高遇阻，先卖后买做反T")
        if sell:
            lines.append(f"  ✅ 卖: ¥{sell:.2f}（分时双顶/冲高先减原底仓）")
        if buy:
            lines.append(f"  ✅ 买回: ¥{buy:.2f}（回落支撑S1回补同量）")
        if t:
            lines.append(f"  📐 T仓: {t}股（=卖出量，当日买回等量）")
        lines.append("  🛑 止损: 回补价>卖出价≥1.5%即认错收手")
    else:
        lines.append(f"  理由: {row.get('summary') or '当前信号不足'}")
        lines.append("  操作: 不动，等下一时段")
    return "\n".join(lines)


# ── 推送 ────────────────────────────────────────────────────
def push_card(
    title: str, sections: list[tuple[str, str]], footer: str, chat_id: str = DEFAULT_CHAT
) -> bool:
    """经 push_card.py 发交互卡片（含 429 退避 + markdown 兜底）。"""
    cmd = [
        sys.executable,
        str(PUSH_CARD),
        "--title",
        title,
        "--level",
        "info",
        "--footer",
        footer,
        "--chat-id",
        chat_id,
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
    rows = [build_eval_row(strategy, h, gate_pass=gate["pass"]) for h in load_holdings()]

    # 早盘自检正文
    sec_market = (
        f"{idx['name']} {idx['price']:.2f}（{idx['change_pct']:+.2f}%）\n{gate['note']}"
        if idx
        else gate["note"]
    )
    secs = [("🌍 大盘环境", sec_market)]
    for row in rows:
        if row.get("stop_loss_hit"):
            secs.append((f"🛑 {row['name']} 清仓优先", fmt_row(row)))
        elif not row["t0"]:
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
    rows = [build_eval_row(strategy, h, t_count=1, gate_pass=gate["pass"]) for h in load_holdings()]

    secs = [
        (
            "🌍 大盘环境",
            (
                f"{idx['name']} {idx['price']:.2f}（{idx['change_pct']:+.2f}%）\n{gate['note']}"
                if idx
                else gate["note"]
            ),
        )
    ]
    for row in rows:
        if row.get("stop_loss_hit"):
            secs.append((f"🛑 {row['name']} 清仓优先", fmt_row(row)))
        elif row["blocked"]:
            secs.append((f"⏸️ {row['name']} 停手", fmt_row(row)))
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
    parser.add_argument(
        "mode", choices=["morning", "afternoon"], help="morning=早盘自检, afternoon=尾盘决策"
    )
    parser.add_argument("--no-push", action="store_true", help="只打印不推送(调试)")
    parser.add_argument(
        "--portfolio", default=str(USER_PORTFOLIO), help="portfolio.json路径(默认Claw持仓)"
    )
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
