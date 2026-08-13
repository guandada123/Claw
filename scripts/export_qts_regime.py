#!/usr/bin/env python3
"""
export_qts_regime.py — 导出当前市场状态（牛/熊/震荡/过渡）
============================================================
2026-08-13 打通: 废除 docker exec 容器注入，改 qts_client 直连 PG
读取沪深300日线，本地等价实现 MarketRegimeFilter 算法(纯函数移植，
算法来源 QTS strategy-service/services/market_regime.py，禁import代码铁律)。

输出市场状态 + 建议仓位到 data/qts_regime.json。

用法:
  python3 scripts/export_qts_regime.py
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_OUTPUT = _PROJECT_ROOT / "data" / "qts_regime.json"

# 沪深300 指数代码（QTS daily_quote 中存储）
INDEX_TS_CODE = "000300.SH"
# 腾讯降级: 指数K线拉取(非交易日/缺数据时)
TENCENT_INDEX_URL = (
    "https://web.ifzq.gtimg.cn/appstock/app/kline/kline?param=sh000300,day,,,500,"
)


# ── 算法移植（来源: QTS market_regime.py，纯函数，行为保持一致）──


def _calc_sma(prices: list[float], period: int) -> list[float]:
    n = len(prices)
    if n < period:
        return [float("nan")] * n
    result: list[float] = [float("nan")] * (period - 1)
    prefix = [0.0]
    for p in prices:
        prefix.append(prefix[-1] + p)
    for i in range(period - 1, n):
        result.append((prefix[i + 1] - prefix[i + 1 - period]) / period)
    return result


def _calc_adx(highs, lows, closes, period: int = 14) -> list[float]:
    n = len(closes)
    adx = [0.0] * n
    if n < period * 2:
        return adx
    tr = [0.0] * n
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(1, n):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        tr[i] = max(hl, hc, lc)
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        plus_dm[i] = up_move if up_move > down_move and up_move > 0 else 0
        minus_dm[i] = down_move if down_move > up_move and down_move > 0 else 0
    smooth_tr = [0.0] * n
    smooth_plus = [0.0] * n
    smooth_minus = [0.0] * n
    dx = [0.0] * n
    smooth_tr[period] = sum(tr[1 : period + 1]) / period
    smooth_plus[period] = sum(plus_dm[1 : period + 1]) / period
    smooth_minus[period] = sum(minus_dm[1 : period + 1]) / period
    for i in range(period + 1, n):
        smooth_tr[i] = smooth_tr[i - 1] - smooth_tr[i - 1] / period + tr[i]
        smooth_plus[i] = smooth_plus[i - 1] - smooth_plus[i - 1] / period + plus_dm[i]
        smooth_minus[i] = smooth_minus[i - 1] - smooth_minus[i - 1] / period + minus_dm[i]
    for i in range(period, n):
        if smooth_tr[i] > 0:
            pdi = (smooth_plus[i] / smooth_tr[i]) * 100
            mdi = (smooth_minus[i] / smooth_tr[i]) * 100
            di_sum = pdi + mdi
            if di_sum > 0:
                dx[i] = abs(pdi - mdi) / di_sum * 100
    for i in range(period * 2 - 1, n):
        adx[i] = sum(dx[i - period + 1 : i + 1]) / period
    return adx


def _calc_roc(prices: list[float], period: int = 20) -> list[float]:
    n = len(prices)
    result: list[float] = [0.0] * n
    for i in range(period, n):
        prev = prices[i - period]
        result[i] = (prices[i] - prev) / prev if prev != 0 else 0.0
    return result


def _calc_slope(series: list[float], window: int = 5) -> float:
    valid = [(i, v) for i, v in enumerate(series) if not math.isnan(v)]
    if len(valid) < window:
        return 0.0
    recent = valid[-window:]
    x_vals = [p[0] for p in recent]
    y_vals = [p[1] for p in recent]
    n = len(x_vals)
    if n < 2:
        return 0.0
    x_mean = sum(x_vals) / n
    y_mean = sum(y_vals) / n
    num = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_vals, y_vals))
    den = sum((x - x_mean) ** 2 for x in x_vals)
    return num / den if den != 0 else 0.0


def classify_regime(closes, highs, lows) -> tuple[str, float]:
    """等价 QTS MarketRegimeFilter.classify_fast()。返回 (regime, mult)。"""
    n = len(closes)
    if n < 205:
        return "unknown", 0.5  # 数据不足，诚实输出（不硬编码震荡）

    ma_fast = _calc_sma(closes, 50)
    ma_slow = _calc_sma(closes, 200)
    fast_val = ma_fast[-1] if not math.isnan(ma_fast[-1]) else closes[-1]
    slow_val = ma_slow[-1] if not math.isnan(ma_slow[-1]) else closes[-1]
    fast_slope = _calc_slope(ma_fast, 5)
    slow_slope = _calc_slope(ma_slow, 5)

    adx_values = _calc_adx(highs, lows, closes, 14)
    adx_val = adx_values[-1] if not math.isnan(adx_values[-1]) else 0.0
    roc = _calc_roc(closes, 20)
    roc_val = roc[-1] if not math.isnan(roc[-1]) else 0.0

    # 慢速判定
    if fast_val > slow_val and fast_slope > 0 and (adx_val > 22.0 or roc_val > 0.02):
        slow_regime = "bull"
    elif fast_val < slow_val and fast_slope < 0 and roc_val < -0.02:
        slow_regime = "bear"
    else:
        slow_regime = "oscillate"

    # 快速窗口 EMA20/60（SMA 近似）
    if n >= 65:
        ema20 = _calc_sma(closes, 20)
        ema60 = _calc_sma(closes, 60)
        ema20_val = ema20[-1] if not math.isnan(ema20[-1]) else closes[-1]
        ema60_val = ema60[-1] if not math.isnan(ema60[-1]) else closes[-1]
        ema20_slope = _calc_slope(ema20, 3)
        fast_bull = ema20_val > ema60_val and ema20_slope > 0
        fast_bear = ema20_val < ema60_val and ema20_slope < 0
        if slow_regime == "bull" and fast_bear:
            return "transition", 0.4
        if slow_regime == "bear" and fast_bull:
            return "transition", 0.4

    mult = {"bull": 1.0, "oscillate": 0.5, "bear": 0.25, "transition": 0.4}.get(
        slow_regime, 0.5
    )
    return slow_regime, mult


DESC = {
    "bull": "🟢 牛市 — 建议全仓(1.0x)",
    "oscillate": "🟡 震荡 — 建议半仓(0.5x)",
    "bear": "🔴 熊市 — 建议25%仓(0.25x)",
    "transition": "⚠️ 过渡态 — 建议40%仓(0.4x)，等方向明确",
    "unknown": "⚪ 数据不足 — 无法判定，保守半仓(0.5x)",
}


def _fetch_index() -> list[tuple] | None:
    """沪深300 日线 [(close, high, low) 升序]。QTS PG 优先，腾讯降级。"""
    from qts_client import get_kline

    rows = get_kline("000300", limit=500)
    if rows and len(rows) >= 205:
        rows_rev = [
            (float(r["close"]), float(r["high"]), float(r["low"]))
            for r in reversed(rows)
            if r.get("close") and r.get("high") and r.get("low")
        ]
        if len(rows_rev) >= 205:
            return rows_rev
    # 腾讯降级
    try:
        import urllib.request

        req = urllib.request.Request(
            TENCENT_INDEX_URL, headers={"User-Agent": "Mozilla/5.0"}
        )
        raw = urllib.request.urlopen(req, timeout=10).read().decode("utf-8")
        node = json.loads(raw).get("data", {}).get("sh000300", {})
        days = node.get("day") or node.get("qfqday") or []
        rows_rev = [
            (float(k[2]), float(k[3]), float(k[4]))
            for k in sorted(days, key=lambda x: x[0])
            if len(k) >= 5
        ]
        return rows_rev if len(rows_rev) >= 205 else None
    except Exception:  # noqa: BLE001
        return None


def export() -> dict:
    """导出 QTS 市场状态（服务直连，无 docker exec）"""
    try:
        bars = _fetch_index()
        if bars and len(bars) >= 205:
            closes = [b[0] for b in bars]
            highs = [b[1] for b in bars]
            lows = [b[2] for b in bars]
            regime, mult = classify_regime(closes, highs, lows)
            data = {
                "regime": regime,
                "regime_label": DESC.get(regime, str(regime)),
                "position_multiplier": mult,
                "data_points": len(bars),
                "source": "qts_pg",
                "generated_at": datetime.now().isoformat(),
            }
        else:
            data = {
                "error": "数据不足",
                "regime": "unknown",
                "regime_label": DESC["unknown"],
                "position_multiplier": 0.5,
                "generated_at": datetime.now().isoformat(),
            }
    except Exception as e:  # noqa: BLE001
        data = {
            "error": str(e),
            "regime": "unknown",
            "regime_label": DESC["unknown"],
            "position_multiplier": 0.5,
            "generated_at": datetime.now().isoformat(),
        }

    _OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    _OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


if __name__ == "__main__":
    result = export()
    print(f"市场状态: {result.get('regime_label', result.get('regime', 'unknown'))}")
    print(f"仓位系数: {result.get('position_multiplier', 0.5)}x")
    if result.get("error"):
        print(f"⚠ {result['error']}")
