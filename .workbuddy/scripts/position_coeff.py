#!/usr/bin/env python3
"""
position_coeff.py — 仓位双信号系数（08-05 新增，B2 优化落地）
================================================================
合成仓位系数（0.60 ~ 1.00），乘入单只建仓上限（默认 ¥25,000）：

  coeff = 量价系数 × 估值系数

量价系数（市场状态，复用 market_gate 逻辑）：
  NORMAL（上证站上MA20）        → 1.00
  UNKNOWN（数据失败，不阻断）   → 0.90
  DEFENSE（跌破MA20且连续缩量） → 0.60

估值系数（组合股息率 vs 10年国债利差，博道"估值防大错"思想简化版）：
  利差 ≥ 1.5pp（股息率显著高于无风险利率） → 1.00
  利差 1.0~1.5pp                            → 0.90
  利差 < 1.0pp                              → 0.80
  数据不足                                  → 0.95

输出（stdout）：
  <coeff>           如 0.90（单行小数）
  --verbose 追加 JSON：{coeff, price_factor, value_factor, market_state, spread_pp}

用法：
  python3 position_coeff.py
持仓股息率映射（静态表，大盘蓝筹典型值，标注更新时间）：
  000333 美的集团 4.2 | 600036 招商银行 5.1 | 601899 紫金矿业 1.9
  601668 中国建筑 3.6 | 600900 长江电力 3.9 | 601088 中国神华 5.8
  600584 长电科技 0.5 | 002185 华天科技 0.4 | 601398 工商银行 5.5
  601288 农业银行 5.2 | 601857 中国石油 4.5 | 600028 中国石化 5.0
10年国债收益率：2026-08 约 1.7%（外部数据，见搜索记录）
"""

from __future__ import annotations

import json
import sys
import urllib.request

KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh000001,day,,,30,qfq"
TEN_YEAR_YIELD = 1.7  # 10年国债收益率%（2026-08 基准）
PORTFOLIO = "/Users/guan/WorkBuddy/Claw/.workbuddy/data/simulation/portfolio.json"

# 持仓股息率静态映射（2026-08-05 基准，大盘蓝筹典型值）
DIV_YIELD = {
    "000333": 4.2, "600036": 5.1, "601899": 1.9, "601668": 3.6,
    "600900": 3.9, "601088": 5.8, "600584": 0.5, "002185": 0.4,
    "601398": 5.5, "601288": 5.2, "601857": 4.5, "600028": 5.0,
}


def _market_state() -> str:
    """量价状态：NORMAL/DEFENSE/UNKNOWN（与 market_gate 一致）"""
    try:
        req = urllib.request.Request(
            KLINE_URL, headers={"User-Agent": "Mozilla/5.0"}
        )
        raw = urllib.request.urlopen(req, timeout=8).read().decode("utf-8", "ignore")
        d = json.loads(raw)
        days = d.get("data", {}).get("sh000001", {}).get("qfqday") or d.get("data", {}).get("sh000001", {}).get("day") or []
        if len(days) < 25:
            return "UNKNOWN"
        closes = [float(x[2]) for x in days]
        vols = [float(x[5]) for x in days]
        ma20 = sum(closes[-20:]) / 20
        shrink = sum(
            1 for i in range(-3, 0)
            if (lambda avg: avg > 0 and vols[i] < avg * 0.9)(sum(vols[i - 3 : i]) / 3)
        )
        if closes[-1] < ma20 and shrink >= 3:
            return "DEFENSE"
        return "NORMAL"
    except Exception:
        return "UNKNOWN"


def _value_factor(codes: list[str]) -> tuple[float, float | None]:
    """估值系数：组合加权股息率 - 10年国债利差"""
    y = [DIV_YIELD.get(c) for c in codes if c in DIV_YIELD]
    if not y:
        return 0.95, None
    combo_yield = sum(y) / len(y)
    spread = combo_yield - TEN_YEAR_YIELD
    if spread >= 1.5:
        return 1.00, round(spread, 2)
    if spread >= 1.0:
        return 0.90, round(spread, 2)
    return 0.80, round(spread, 2)


def get_position_coeff(codes: list[str]) -> tuple[float, dict]:
    state = _market_state()
    price_factor = {"NORMAL": 1.00, "UNKNOWN": 0.90, "DEFENSE": 0.60}[state]
    vf, spread = _value_factor(codes)
    coeff = round(price_factor * vf, 2)
    coeff = max(0.60, min(1.00, coeff))
    return coeff, {
        "price_factor": price_factor,
        "value_factor": vf,
        "spread_pp": spread,
        "market_state": state,
    }


def main() -> int:
    verbose = "--verbose" in sys.argv
    codes: list[str] = []
    if "--codes" in sys.argv:
        idx = sys.argv.index("--codes")
        codes = [c.strip() for c in sys.argv[idx + 1].split(",") if c.strip()]
    else:
        sim = json.load(open(PORTFOLIO))
        codes = [c for c in sim.get("positions", {}).keys() if c.isdigit()]
    coeff, info = get_position_coeff(codes)
    print(coeff, flush=True)
    if verbose:
        print(json.dumps(info, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
