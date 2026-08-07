#!/usr/bin/env python3
"""
execute_buy_plan.py — 盘中买入计划消费者（方案 A 最小可行修复）

背景：
  智能选股自动化(PHASE 3)只负责写 /tmp/buy_plan.json，历史上没有任何脚本
  消费它 → 计划写了从不执行，招商银行连续 8 日未成交。本脚本补全"消费者"
  角色，由盘中监控自动化(每30min)调用，按买区+大盘企稳条件触发 sim_trade 买入。

触发逻辑：
  1. 读 /tmp/buy_plan.json（plan_date / code / name / shares / buy_zone / stop_loss / target）
  2. 防重：experiments/<plan_date>.json 的 execution_plan.status == "done" → 跳过
  3. 交易时段校验：09:30–11:30 / 13:00–14:55（非时段不触发，避免过期行情噪声）
  4. 拉实时价（腾讯 qt.gtimg.cn）：标的现价 + 上证指数涨跌幅
  5. 触发条件：现价 ∈ [buy_low, buy_high] 且 上证 ≥ -0.5%（企稳）且 同日买入未超1次
  6. --execute 才真下单（默认 --dry-run 仅校验+打印）；成交后回写状态+推卡片

用法：
  python3 execute_buy_plan.py                 # dry-run（默认，不下单）
  python3 execute_buy_plan.py --execute       # 真下单
  python3 execute_buy_plan.py --force         # 忽略交易时段/大盘条件（手动兜底用）
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from datetime import date, datetime, time

# ── 路径 ──────────────────────────────────────────────
CLAW = "/Users/guan/WorkBuddy/Claw"
SCRIPTS = os.path.join(CLAW, ".workbuddy", "scripts")
BUY_PLAN = "/tmp/buy_plan.json"
EXP_DIR = os.path.join(CLAW, ".workbuddy", "data", "simulation", "experiments")
PUSH_CARD = os.path.join(SCRIPTS, "push_card.py")

# ── 交易时段 ──────────────────────────────────────────
MORNING_OPEN = time(9, 30)
MORNING_CLOSE = time(11, 30)
AFTERNOON_OPEN = time(13, 0)
AFTERNOON_CLOSE = time(14, 55)
MARKET_STEADY_THRESHOLD = -0.5  # 上证涨跌幅%下限，低于此视为不稳

# ── name 字段护栏（防选股理由污染持仓 name）───────────────
# 选股流程曾把整段理由写入 buy_plan.name（如"午间确认早盘选中...回踩买区触发"），
# 落库后污染 portfolio.positions[name]，导致持仓名变成选股文案。
# 特征字符：确认/回踩/选中/✅/⚠️/段/：/空格过长 → 视为污染，用 gtimg 标准名兜底。
_NAME_POLLUTION_MARKERS = ("确认", "回踩", "选中", "✅", "⚠️", "段", "：", "；")


def _sanitize_name(raw: str, code: str) -> str:
    """清洗持仓 name：含选股理由特征 → 用 gtimg 实时标准名兜底。"""
    if not raw or any(m in raw for m in _NAME_POLLUTION_MARKERS) or len(raw) > 12:
        return _gtimg_name(code) or raw or code
    return raw


def _gtimg_name(code: str) -> str | None:
    """腾讯 gtimg 取标准股票名（sh/sz 自动判断）。"""
    prefix = "sh" if code.startswith(("6", "9")) else "sz"
    url = f"https://qt.gtimg.cn/q={prefix}{code}"
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            txt = r.read().decode("gbk", errors="ignore")
        # v_sh600031="1~三一重工~600031~..."
        if "~" in txt:
            return txt.split("~")[1].strip()
    except Exception:  # noqa: BLE001
        return None
    return None


def _now() -> datetime:
    return datetime.now()


def in_trading_session() -> bool:
    t = _now().time()
    return (MORNING_OPEN <= t <= MORNING_CLOSE) or (AFTERNOON_OPEN <= t <= AFTERNOON_CLOSE)


def fetch_realtime(codes: list[str]) -> dict:
    """拉腾讯实时行情，返回 {code: {name, price, pct}}"""
    url = "https://qt.gtimg.cn/q=" + ",".join(codes)
    try:
        import urllib.request

        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=10).read().decode("gbk")
    except Exception as e:  # noqa: BLE001
        print(f"[ERR] 行情拉取失败: {e}", file=sys.stderr)
        return {}
    out: dict = {}
    for line in raw.strip().split("\n"):
        if "=" not in line:
            continue
        # 行格式: v_sh600036="1~招商银行~..." 或单行多股 "v_sh600036=...;v_sh000001=..."
        # 按 ';' 切分多股，再按首个 '=' 取变量名
        for seg in line.split(";"):
            if "=" not in seg:
                continue
            var = seg.split("=", 1)[0]  # v_sh600036
            payload = seg.split("=", 1)[1].strip('"')
            f = payload.split("~")
            if len(f) < 33:
                continue
            code = var[2:] if var.startswith("v_") else var  # sh600036
        out[code] = {
            "name": f[1],
            "price": float(f[3]) if f[3] else 0.0,
            "pct": float(f[32]) if f[32] else 0.0,
        }
    return out


def load_buy_plan() -> dict | None:
    if not os.path.exists(BUY_PLAN):
        print("[SKIP] /tmp/buy_plan.json 不存在（今日无买入计划）")
        return None
    try:
        with open(BUY_PLAN) as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"[ERR] buy_plan 解析失败: {e}", file=sys.stderr)
        return None


def plan_already_done(plan: dict) -> bool:
    """experiments/<plan_date>.json 里 execution_plan.status == done → 已成交"""
    pd = plan.get("plan_date") or plan.get("date")
    if not pd:
        return False
    exp_path = os.path.join(EXP_DIR, f"{pd}.json")
    if not os.path.exists(exp_path):
        return False
    try:
        with open(exp_path) as f:
            d = json.load(f)
        return d.get("execution_plan", {}).get("status") == "done"
    except Exception:  # noqa: BLE001
        return False


def mark_plan_done(plan: dict, txn: dict, price: float):
    """回写 experiments/<plan_date>.json 的 status=done"""
    pd = plan.get("plan_date") or plan.get("date")
    if not pd:
        return
    exp_path = os.path.join(EXP_DIR, f"{pd}.json")
    if not os.path.exists(exp_path):
        return
    try:
        with open(exp_path) as f:
            d = json.load(f)
        d.setdefault("execution_plan", {})["status"] = "done"
        d.setdefault("execution_plan", {})["filled_price"] = price
        d.setdefault("execution_plan", {})["filled_at"] = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        d.setdefault("execution_plan", {})["transaction"] = txn
        with open(exp_path, "w") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        print(f"[OK] experiments/{pd}.json status → done")
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] 回写 experiments 失败: {e}", file=sys.stderr)


def push_result(plan: dict, price: float, txn: dict, dry: bool):
    """推送成交/跳过卡片"""
    code = plan["code"]
    name = plan.get("name", code)
    if dry:
        title = "📈 买入计划(DRY-RUN)"
        body = (
            f"标的：{name}({code})\n"
            f"买区：{plan['buy_zone'][0]}–{plan['buy_zone'][1]}\n"
            f"触发价：{price}（在区内）\n"
            f"股数：{plan['shares']}（100倍数）\n"
            f"[DRY-RUN] 未实际下单，加 --execute 才成交"
        )
    else:
        title = "📈 买入计划已成交"
        body = (
            f"✅ {name}({code}) 已买入\n"
            f"成交价：¥{price} | 股数：{plan['shares']}\n"
            f"占用：¥{txn.get('total', 0):,.0f} | 留现：¥{txn.get('cash_remaining', 0):,.0f}\n"
            f"止损：{plan.get('stop_loss')} | 目标：{plan.get('target')}"
        )
    payload = {
        "title": title,
        "level": "info" if dry else "success",
        "sections": [{"title": "盘中买入执行", "body": body}],
        "footer": "execute_buy_plan.py | 投顾操盘自动化",
    }
    try:
        r = subprocess.run(
            ["python3", PUSH_CARD, "--title", "x", "--json-stdin"],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if r.returncode != 0:
            print(f"[WARN] 推送失败: {r.stderr[-300:]}", file=sys.stderr)
        else:
            print("[OK] 飞书卡片已推送")
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] 推送异常: {e}", file=sys.stderr)


def do_buy(plan: dict, price: float, execute: bool) -> dict | None:
    """调用 sim_trade.py buy。execute=False 时返回模拟结果不落盘"""
    code = plan["code"]
    shares = int(plan["shares"])
    name = _sanitize_name(plan.get("name", ""), code)
    if not execute:
        # dry-run：构造模拟结果
        gross = shares * price
        sim = {
            "ok": True,
            "transaction": {
                "type": "BUY",
                "code": code,
                "shares": shares,
                "price": price,
                "total": round(gross + gross * 0.0003, 2),
            },
            "cash_remaining": 5233.0,
            "total_asset": 34200.0,
        }
        print(f"[DRY-RUN] 将买入 {name}({code}) {shares}股 @ {price} = ¥{gross:,.0f}")
        return sim
    # 真实下单
    r = subprocess.run(
        [
            "python3",
            os.path.join(SCRIPTS, "sim_trade.py"),
            "buy",
            code,
            str(shares),
            f"{price:.2f}",
            name,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if r.returncode != 0:
        print(f"[ERR] sim_trade buy 失败: {r.stderr[-400:]}", file=sys.stderr)
        return None
    try:
        return json.loads(r.stdout)
    except Exception:  # noqa: BLE001
        print(f"[ERR] sim_trade 输出解析失败: {r.stdout[-400:]}", file=sys.stderr)
        return None


def main():
    ap = argparse.ArgumentParser(description="盘中买入计划消费者")
    ap.add_argument("--execute", action="store_true", help="真实下单（默认 dry-run）")
    ap.add_argument("--force", action="store_true", help="忽略交易时段/大盘条件（手动兜底）")
    args = ap.parse_args()

    plan = load_buy_plan()
    if not plan:
        return

    # 防重
    if plan_already_done(plan):
        print(f"[SKIP] 计划 {plan.get('plan_date')} 已成交（status=done），跳过")
        return

    # 交易时段
    if not args.force and not in_trading_session():
        print(f"[SKIP] 非交易时段（{_now().strftime('%H:%M')}），不触发")
        return

    # 拉行情
    code = plan["code"]
    qcode = ("sh" if code.startswith("6") else "sz") + code
    rt = fetch_realtime([qcode, "sh000001"])
    if qcode not in rt:
        print("[SKIP] 标的实时价缺失，跳过", file=sys.stderr)
        return
    price = rt[qcode]["price"]
    sh_pct = rt.get("sh000001", {}).get("pct", 0.0)
    low, high = plan["buy_zone"]

    print(
        f"[INFO] {plan.get('name', code)}({code}) 现价={price} 上证={sh_pct:+.2f}% 买区=[{low},{high}]"
    )

    in_zone = low <= price <= high
    steady = args.force or sh_pct >= MARKET_STEADY_THRESHOLD

    if not in_zone:
        print(f"[SKIP] 现价 {price} 不在买区 [{low},{high}]，等待回踩")
        return
    if not steady:
        print(f"[SKIP] 大盘不稳（上证 {sh_pct:+.2f}% < {MARKET_STEADY_THRESHOLD}%），等待企稳")
        return

    # 触发买入
    print(f"[TRIGGER] 现价 {price} ∈ 买区 且 大盘企稳 → 执行买入")
    result = do_buy(plan, price, args.execute)
    if not result or not result.get("ok"):
        print(f"[FAIL] 买入未成功: {result}", file=sys.stderr)
        return

    if args.execute:
        mark_plan_done(plan, result.get("transaction", {}), price)
    push_result(plan, price, result.get("transaction", {}), dry=not args.execute)


if __name__ == "__main__":
    main()
