#!/usr/bin/env python3
"""
correlation_monitor.py — 持仓相关性风险监控（08-05 新增，B1 优化落地）
================================================================
计算模拟盘持仓两两 Pearson 相关性（基于腾讯日线 60 日收盘价）。
当相关性 >0.7 的持仓对数 ≥1 且涉及 ≥3 只持仓时 → 触发"高相关预警"：
策略执行须将单只仓位上限从 50% 降至 40%（减仓最相关的一只或禁止再加仓）。

输出（stdout）：
  NORMAL          无高相关组合
  WARN  pair1,pair2,...   高相关组合（>0.7 且涉及≥3只）
  UNKNOWN         数据失败

用法：
  python3 correlation_monitor.py                # 从模拟盘 portfolio.json 读持仓
  python3 correlation_monitor.py --codes 600036,601668  # 指定代码
  python3 correlation_monitor.py --threshold 0.7 --verbose

数据源：腾讯日线（qt 优先铁律），失败降级 unknown（不阻断主流程）
"""

from __future__ import annotations

import json
import math
import statistics
import sys
import urllib.request

KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,60,qfq"
THRESHOLD = 0.7  # 相关性预警阈值
MIN_HOLDINGS = 3  # 涉及持仓数 ≥3 才触发（B1 规则）
PORTFOLIO = "/Users/guan/WorkBuddy/Claw/.workbuddy/data/simulation/portfolio.json"


def _to_symbol(code: str) -> str:
    """6位代码 → 腾讯带市场前缀：6开头sh，其余sz（0/3开头深市，8/4北交排除）"""
    code = code.strip()
    if code[:2].lower() in ("sh", "sz"):
        return code
    if code.startswith("6"):
        return "sh" + code
    return "sz" + code


def _fetch_kline(symbol: str) -> list[float]:
    """拉取标的日线收盘价序列（腾讯）"""
    sym = _to_symbol(symbol)
    req = urllib.request.Request(KLINE_URL.format(sym=sym), headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=8).read().decode("utf-8", "ignore")
    d = json.loads(raw)
    days = (
        d.get("data", {}).get(sym, {}).get("qfqday")
        or d.get("data", {}).get(sym, {}).get("day")
        or []
    )
    closes = [float(x[2]) for x in days]
    return closes


def _pearson(a: list[float], b: list[float]) -> float | None:
    """两序列 Pearson 相关系数（对齐尾部窗口）"""
    n = min(len(a), len(b))
    if n < 20:
        return None
    a, b = a[-n:], b[-n:]
    ma, mb = statistics.mean(a), statistics.mean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    if den == 0:
        return None
    return num / den


def get_correlation_state(codes: list[str]) -> tuple[str, list[tuple[str, str, float]]]:
    """返回 (state, high_pairs)"""
    series: dict[str, list[float]] = {}
    for c in codes:
        try:
            closes = _fetch_kline(c)
            if len(closes) >= 20:
                series[c] = closes
        except Exception:
            continue
    if len(series) < 2:
        return "UNKNOWN", []

    high_pairs: list[tuple[str, str, float]] = []
    involved = set()
    keys = list(series.keys())
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            r = _pearson(series[keys[i]], series[keys[j]])
            if r is not None and r > THRESHOLD:
                high_pairs.append((keys[i], keys[j], round(r, 2)))
                involved.add(keys[i])
                involved.add(keys[j])

    if high_pairs and len(involved) >= MIN_HOLDINGS:
        return "WARN", high_pairs
    return "NORMAL", high_pairs


def main() -> int:
    codes: list[str] = []
    verbose = "--verbose" in sys.argv
    if "--codes" in sys.argv:
        idx = sys.argv.index("--codes")
        codes = [c.strip() for c in sys.argv[idx + 1].split(",") if c.strip()]
    else:
        with open(PORTFOLIO) as f:
            sim = json.load(f)
        pos = sim.get("positions", {})
        codes = [c for c in pos if c.isdigit()]

    state, pairs = get_correlation_state(codes)
    print(state, flush=True)
    if verbose:
        print(json.dumps({"codes": codes, "high_pairs": pairs}, ensure_ascii=False), flush=True)
        if state == "WARN":
            print(
                f"⚠️ 高相关预警：{len(pairs)} 对相关>0.7（{len({c for p in pairs for c in p[:2]})}只），单只上限50%→40%",
                file=sys.stderr,
                flush=True,
            )
        elif state == "UNKNOWN":
            print("⚠️ 相关性数据不足，不阻断（按NORMAL处理）", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
