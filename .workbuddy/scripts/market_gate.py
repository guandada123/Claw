#!/usr/bin/env python3
"""
market_gate.py — 大盘环境门控（08-05 新增，A1 优化落地）
================================================================
判断大盘是否处于"防御态"：上证指数跌破 MA20 且最近 3 日连续缩量
（量能 < 前 3 日均量×0.9）→ 防御态，当日暂停新开仓（持仓止损/止盈照常）。

输出（stdout 单行）：
  NORMAL       正常态，可正常开仓
  DEFENSE      防御态，暂停新开仓
  UNKNOWN      数据失败（不阻断交易，但输出 WARN 供日志追溯）

用法：
  python3 market_gate.py            # 输出状态
  python3 market_gate.py --verbose  # 输出状态 + 详细指标
设计：
  - 数据源：腾讯日线（qt 优先铁律），失败回退 unknown（不阻塞主流程）
  - 门控是"增强"而非"核心"：数据失败不阻断交易，与 local_combo_signal 权重回退一致
"""

from __future__ import annotations

import json
import sys
import urllib.request

KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh000001,day,,,30,qfq"
SHRINK_DAYS = 3  # 连续缩量天数要求
VOL_RATIO = 0.9  # 当日量 < 前3日均量×0.9 视为缩量


def get_gate_state() -> tuple[str, dict]:
    """返回 (state, metrics)"""
    try:
        req = urllib.request.Request(KLINE_URL, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=8).read().decode("utf-8", "ignore")
        d = json.loads(raw)
        days = (
            d.get("data", {}).get("sh000001", {}).get("qfqday")
            or d.get("data", {}).get("sh000001", {}).get("day")
            or []
        )
        if len(days) < 25:
            return "UNKNOWN", {"error": f"K线数据不足({len(days)}天)"}

        closes = [float(x[2]) for x in days]
        vols = [float(x[5]) for x in days]
        last_close = closes[-1]
        ma20 = sum(closes[-20:]) / 20
        # 最近3日缩量判定：vol[i] < 前3日均量×0.9
        shrink_count = 0
        for i in range(-SHRINK_DAYS, 0):
            prev_avg = sum(vols[i - 3 : i]) / 3
            if prev_avg > 0 and vols[i] < prev_avg * VOL_RATIO:
                shrink_count += 1

        below_ma20 = last_close < ma20
        defense = below_ma20 and shrink_count >= SHRINK_DAYS
        state = "DEFENSE" if defense else "NORMAL"
        metrics = {
            "last_close": round(last_close, 2),
            "ma20": round(ma20, 2),
            "below_ma20": below_ma20,
            "shrink_count": shrink_count,
            "required_shrink": SHRINK_DAYS,
        }
        return state, metrics
    except Exception as e:
        return "UNKNOWN", {"error": str(e)}


def main() -> int:
    verbose = "--verbose" in sys.argv
    state, metrics = get_gate_state()
    print(state, flush=True)
    if verbose:
        print(json.dumps(metrics, ensure_ascii=False), flush=True)
        if state == "DEFENSE":
            print(
                "⚠️ 防御态：上证跌破MA20且连续缩量，当日暂停新开仓（持仓风控照常）",
                file=sys.stderr,
                flush=True,
            )
        elif state == "UNKNOWN":
            print("⚠️ 门控数据失败，不阻断交易（按NORMAL处理）", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
