#!/usr/bin/env python3
"""refresh_sim_close.py — 收盘后刷新模拟盘「存盘估值」并落当日快照

━━ 为什么需要它（2026-10-09 取证）━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
`.workbuddy/data/simulation/portfolio.json` 是模拟盘唯一的存盘真源，但
`save_portfolio()` 只在 buy/sell 时才被触发。而能刷新价格的三个命令
（`update` / `batch-update` / `auto-check`）和落快照的 `snapshot`：

  · 没有任何 ACTIVE 自动化调用（实测：ACTIVE 自动化只调 buy/sell/perf/history）
  · 唯一调用过 `batch-update` 的两条自动化（盘中监控 / 收盘自动检查 15:10）
    已于 2026-06-21 / 2026-06-29 删除

后果（静默、不报错）：
  · `positions[].current_price` 冻结 —— 实测 600036 存盘 40.88 vs 实时 41.42
  · `daily_snapshot` 连续 5 个交易日（09-28~10-06）**数值完全相同**
  · 下游 QTS 镜像逐字节复制这些陈旧值（`shared/claw_data/portfolio.json`）

不影响的：盘中决策。策略执行自动化 Phase 1 用 `fetch_holdings_quotes.py --sim`
取**实时**价，所以止损/止盈判定是准的。受影响的是**所有读文件的消费方**
（日报/周报/QTS 风控/飞书日报）。

━━ 链路 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  1. fetch_holdings_quotes.py --sim   （Wind → 腾讯降级）取实时价
  2. sim_trade.cmd_update_all_prices  （内置价格防错闸：校验失败**拒绝写入**，保留旧价）
  3. sim_trade.cmd_snapshot           （按当日 key upsert）

━━ 用法 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  python3 refresh_sim_close.py            # 实做
  python3 refresh_sim_close.py --dry-run  # 只打印将改什么，零写盘

退出码：0=成功（含「无持仓，无需刷新」）/ 1=取价失败或数据异常（**不写盘**）
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
PROJECT = SCRIPTS.parent.parent
FETCH = SCRIPTS / "fetch_holdings_quotes.py"
PORTFOLIO = PROJECT / ".workbuddy" / "data" / "simulation" / "portfolio.json"


def _load_sim_module():
    """把 sim_trade 当模块用（它尾部有 __main__ 守卫，import 无副作用）。"""
    sys.path.insert(0, str(SCRIPTS))
    import sim_trade  # noqa: PLC0415

    return sim_trade


def fetch_quotes(python: str) -> tuple[list[dict], str, str | None]:
    """调用取价脚本，返回 (quotes, data_source, error)。

    stdout+stderr 一并读取 —— 只读 stdout 会在异常路径下丢掉全部错误信息。
    """
    try:
        p = subprocess.run(
            [python, str(FETCH), "--sim"],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(PROJECT),
        )
    except Exception as e:  # noqa: BLE001 — 取价失败一律转成可读 error，不抛
        return [], "", f"{e.__class__.__name__}: {e}"
    if p.returncode != 0:
        return [], "", f"fetch rc={p.returncode}: {(p.stderr or p.stdout)[:200]}"
    try:
        payload = json.loads(p.stdout)
    except Exception as e:  # noqa: BLE001
        return [], "", f"输出非 JSON（{e.__class__.__name__}）: {p.stdout[:200]}"
    src = str(payload.get("data_source") or "").strip()
    return payload.get("quotes") or [], src, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不写盘")
    ap.add_argument("--python", default=sys.executable, help="用于调用取价脚本的解释器")
    args = ap.parse_args()

    if not PORTFOLIO.is_file():
        print(json.dumps({"ok": False, "error": f"portfolio 不存在: {PORTFOLIO}"}, ensure_ascii=False))
        return 1

    sim = _load_sim_module()
    pf = sim.load_portfolio()
    held = list((pf.get("positions") or {}).keys())
    if not held:
        print(json.dumps({"ok": True, "skipped": "无持仓，无需刷新"}, ensure_ascii=False))
        return 0

    quotes, data_source, err = fetch_quotes(args.python)
    if err:
        print(json.dumps({"ok": False, "error": f"取价失败: {err}"}, ensure_ascii=False))
        return 1
    if not quotes:
        print(json.dumps({"ok": False, "error": "取价返回空列表"}, ensure_ascii=False))
        return 1

    # ── 只取通过防错校验的价；失败的一律不写（保留旧价，避免脏价入账）──
    prices: dict[str, float] = {}
    skipped: list[dict] = []
    before: dict[str, float] = {}
    for q in quotes:
        code = q.get("code")
        if code not in (pf.get("positions") or {}):
            continue
        before[code] = (pf["positions"][code] or {}).get("current_price")
        sanity = q.get("price_sanity") or {}
        price = sanity.get("verified_price") or q.get("current_price")
        if sanity.get("ok") is False or price in (None, 0):
            skipped.append({"code": code, "reason": sanity.get("action") or "无有效价"})
            continue
        prices[code] = float(price)

    if not prices:
        print(
            json.dumps(
                {"ok": False, "error": "全部持仓取价失败，未写盘", "skipped": skipped},
                ensure_ascii=False,
            )
        )
        return 1

    deltas = [
        {"code": c, "before": before.get(c), "after": p, "changed": before.get(c) != p}
        for c, p in prices.items()
    ]

    if args.dry_run:
        print(
            json.dumps(
                {
                    "ok": True,
                    "dry_run": True,
                    "would_update": prices,
                    "deltas": deltas,
                    "skipped": skipped,
                    "would_snapshot_date": sim.today_str(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    upd = sim.cmd_update_all_prices(prices, f"{data_source}·收盘" if data_source else "")
    snap = sim.cmd_snapshot()
    print(
        json.dumps(
            {
                "ok": True,
                "data_source": data_source,
                "updated": upd.get("updated"),
                "sanity_failed": upd.get("sanity_failed"),
                "deltas": deltas,
                "skipped": skipped,
                "snapshot": {
                    "date": sim.today_str(),
                    "total_asset": snap.get("total_asset"),
                    "cash": snap.get("cash"),
                    "pnl_pct": snap.get("pnl_pct"),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
