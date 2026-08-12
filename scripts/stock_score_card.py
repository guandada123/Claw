#!/usr/bin/env python3
"""
stock_score_card.py — 选股统一评分卡（P0-4，2026-08-12 落地）

来源: 用户 P0 清单第4项（stock_t_analyzer 评分卡）——现状 run_screening 三条件
并列输出无优先级，改对候选股算统一评分卡，按分排序输出 top5。

评分维度（合计100分）:
  动量 20    — 近5日涨幅%(0~15%线性, 封顶20)
  量价 20    — 量比(当日量/5日均量), 1.0~3.0 最优区间, 缩量/爆量降分
  RSI 15    — RSI14 位置: 45~65 最优(15), 超买>80/超卖<20 最低(3)
  MA20 15   — 现价相对MA20: +2%~+10% 最优(15), 远离/破位降分
  情绪 15   — 大盘 regime: 强=15 / 中=10 / 弱=5
  板块 15   — 所属板块强度: 强=15 / 中=10 / 弱=5 / 无数据=0

设计原则: 纯函数式; 数据源新浪K线(动量/量比/RSI/MA20) + market_sentiment(情绪/板块);
网络失败逐维降级为 0 分不阻断; 候选缺失数据不崩整体。

用法:
  python3 scripts/stock_score_card.py --codes 600584,002185,000333
  python3 scripts/stock_score_card.py --codes 600584 --regime 强   # 复用大盘情绪
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

# ── 评分权重 ──
W_MOMENTUM = 20
W_VOLUME_PRICE = 20
W_RSI = 15
W_MA20 = 15
W_SENTIMENT = 15
W_SECTOR = 15

# 动量: 近5日涨幅 0%~15% 线性
MOMENTUM_MAX_PCT = 15.0
# 量比: 1.0~3.0 最优; <0.5 或 >8 最低
VOL_RATIO_LOW, VOL_RATIO_HIGH = 1.0, 3.0
VOL_RATIO_FLOOR, VOL_RATIO_CEIL = 0.5, 8.0
# RSI: 45~65 最优
RSI_SWEET_LOW, RSI_SWEET_HIGH = 45.0, 65.0
RSI_OVERBOUGHT, RSI_OVERSOLD = 80.0, 20.0
# MA20 距离: +2%~+10% 最优; <-3%(破位) 或 >+25%(追高) 最低
MA20_DIST_LOW, MA20_DIST_HIGH = 0.02, 0.10
MA20_DIST_FLOOR, MA20_DIST_CEIL = -0.03, 0.25

# 大盘情绪 → 分数
REGIME_SCORE = {"强": W_SENTIMENT, "中": 10, "弱": 5, "未知": 0}
# 板块强度 → 分数
SECTOR_SCORE = {"强": W_SECTOR, "中": 10, "弱": 5}


def _fetch_kline(symbol: str, n: int = 40) -> list[dict] | None:
    """新浪日K: [{day, close, volume, high, low}]，失败返回 None"""
    try:
        url = (
            f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php"
            f"/CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={n}"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310: https 硬编码
            arr = json.loads(resp.read().decode("utf-8"))
        return [
            {
                "day": r.get("day", ""),
                "close": float(r["close"]),
                "volume": float(r.get("volume", 0) or 0),
                "high": float(r.get("high", 0) or 0),
                "low": float(r.get("low", 0) or 0),
            }
            for r in arr
            if r.get("close") is not None
        ]
    except Exception:
        return None


def _prefix(code: str) -> str:
    code = code.strip().lower()
    if code.startswith(("sh", "sz")):
        return code
    return f"sh{code}" if code.startswith("6") else f"sz{code}"


def _calc_rsi14(closes: list[float]) -> float | None:
    """简单 RSI14（Wilder 平滑）"""
    if len(closes) < 15:
        return None
    gains, losses = 0.0, 0.0
    for i in range(len(closes) - 14, len(closes)):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    if gains + losses == 0:
        return None
    rs = gains / losses if losses > 0 else float("inf")
    return 100.0 - 100.0 / (1.0 + rs)


# ── 各维度打分（纯函数） ──


def score_momentum(m5_pct: float | None) -> float:
    """近5日涨幅 → 0~20 分"""
    if m5_pct is None:
        return 0.0
    return max(0.0, min(m5_pct / MOMENTUM_MAX_PCT * W_MOMENTUM, W_MOMENTUM))


def score_volume_price(vol_ratio: float | None) -> float:
    """量比 → 0~20 分（1~3倍最优）"""
    if vol_ratio is None:
        return 0.0
    if VOL_RATIO_LOW <= vol_ratio <= VOL_RATIO_HIGH:
        return W_VOLUME_PRICE
    if vol_ratio < VOL_RATIO_LOW:
        # 缩量: 0.5~1.0 线性 4~20
        if vol_ratio <= VOL_RATIO_FLOOR:
            return 4.0
        return 4.0 + (vol_ratio - VOL_RATIO_FLOOR) / (VOL_RATIO_LOW - VOL_RATIO_FLOOR) * (
            W_VOLUME_PRICE - 4.0
        )
    # 爆量: 3~8 线性 20→6
    if vol_ratio >= VOL_RATIO_CEIL:
        return 6.0
    return W_VOLUME_PRICE - (vol_ratio - VOL_RATIO_HIGH) / (VOL_RATIO_CEIL - VOL_RATIO_HIGH) * (
        W_VOLUME_PRICE - 6.0
    )


def score_rsi(rsi: float | None) -> float:
    """RSI14 → 0~15 分"""
    if rsi is None:
        return 0.0
    if RSI_SWEET_LOW <= rsi <= RSI_SWEET_HIGH:
        return W_RSI
    if rsi >= RSI_OVERBOUGHT or rsi <= RSI_OVERSOLD:
        return 3.0
    # 甜区外线性衰减
    if rsi > RSI_SWEET_HIGH:
        return W_RSI - (rsi - RSI_SWEET_HIGH) / (RSI_OVERBOUGHT - RSI_SWEET_HIGH) * (W_RSI - 3.0)
    return W_RSI - (RSI_SWEET_LOW - rsi) / (RSI_SWEET_LOW - RSI_OVERSOLD) * (W_RSI - 3.0)


def score_ma20(ma20_dist: float | None) -> float:
    """现价相对MA20 → 0~15 分"""
    if ma20_dist is None:
        return 0.0
    if MA20_DIST_LOW <= ma20_dist <= MA20_DIST_HIGH:
        return W_MA20
    if ma20_dist < MA20_DIST_LOW:
        # 破位/贴线: -3%~+2% 线性 3~15
        if ma20_dist <= MA20_DIST_FLOOR:
            return 3.0
        return 3.0 + (ma20_dist - MA20_DIST_FLOOR) / (MA20_DIST_LOW - MA20_DIST_FLOOR) * (
            W_MA20 - 3.0
        )
    # 追高: +10%~+25% 线性 15→3
    if ma20_dist >= MA20_DIST_CEIL:
        return 3.0
    return W_MA20 - (ma20_dist - MA20_DIST_HIGH) / (MA20_DIST_CEIL - MA20_DIST_HIGH) * (
        W_MA20 - 3.0
    )


def score_sentiment(regime: str | None) -> float:
    """大盘情绪 → 0~15 分"""
    return float(REGIME_SCORE.get(regime, 0))


def score_sector(strength: str | None) -> float:
    """板块强度 → 0~15 分"""
    return float(SECTOR_SCORE.get(strength, 0))


# ── 候选股数据拉取 ──


def analyze_stock(
    code: str,
    name: str = "",
    regime: str | None = None,
    ms: Any | None = None,
) -> dict:
    """对单只候选股算评分（网络失败逐维降级 0 分）。

    ms: market_sentiment.MarketSentiment 实例（复用，避免重复构建）
    """
    symbol = _prefix(code)
    kl = _fetch_kline(symbol, 40)
    out: dict[str, Any] = {
        "code": code,
        "name": name,
        "score": 0.0,
        "breakdown": {
            "momentum": 0.0,
            "volume_price": 0.0,
            "rsi": 0.0,
            "ma20": 0.0,
            "sentiment": 0.0,
            "sector": 0.0,
        },
        "signals": {},
    }

    closes = [k["close"] for k in kl] if kl else []
    volumes = [k["volume"] for k in kl] if kl else []
    cur = closes[-1] if closes else None

    # 动量(近5日) / RSI14 / MA20距离
    m5 = None
    if len(closes) >= 6:
        m5 = (closes[-1] / closes[-6] - 1) * 100
    ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else None
    rsi = _calc_rsi14(closes)
    ma20_dist = (cur - ma20) / ma20 if (cur is not None and ma20) else None
    # 量比: 当日量 / 前5日均量
    vol_ratio = None
    if len(volumes) >= 6 and sum(volumes[-6:-1]) > 0:
        vol_ratio = volumes[-1] / (sum(volumes[-6:-1]) / 5)

    out["signals"] = {
        "close": round(cur, 2) if cur else None,
        "m5_pct": round(m5, 2) if m5 is not None else None,
        "ma20": round(ma20, 2) if ma20 else None,
        "ma20_dist_pct": round(ma20_dist * 100, 2) if ma20_dist is not None else None,
        "rsi14": round(rsi, 1) if rsi is not None else None,
        "vol_ratio": round(vol_ratio, 2) if vol_ratio is not None else None,
    }

    # 板块强度（失败降级 0 分不阻断）
    sector_strength = None
    sector_name = None
    if ms is not None:
        try:
            sec = ms.sector_strength(code)
            if sec:
                sector_strength = sec.get("strength")
                sector_name = sec.get("sector")
        except Exception:  # noqa: S110 - 板块缺失不阻断评分
            pass

    bd = out["breakdown"]
    bd["momentum"] = round(score_momentum(m5), 1)
    bd["volume_price"] = round(score_volume_price(vol_ratio), 1)
    bd["rsi"] = round(score_rsi(rsi), 1)
    bd["ma20"] = round(score_ma20(ma20_dist), 1)
    bd["sentiment"] = round(score_sentiment(regime), 1)
    bd["sector"] = round(score_sector(sector_strength), 1)
    out["score"] = round(sum(bd.values()), 1)
    out["sector_name"] = sector_name
    out["sector_strength"] = sector_strength
    return out


def rank_candidates(
    codes: list[str],
    regime: str | None = None,
    ms: Any | None = None,
    names: dict[str, str] | None = None,
    max_workers: int = 6,
) -> list[dict]:
    """并发分析候选股 → 按分降序返回全量列表（供 top5 截取）"""
    names = names or {}
    if ms is None:
        try:
            from market_sentiment import MarketSentiment

            ms = MarketSentiment()
        except Exception:
            ms = None

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(analyze_stock, c, names.get(c, ""), regime, ms): c
            for c in dict.fromkeys(codes)  # 去重保序
        }
        for f in as_completed(futures):
            try:
                results.append(f.result())
            except Exception:  # noqa: S110 - 单只失败不阻断整体
                pass
    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results


def top5(
    codes: list[str], regime: str | None = None, names: dict[str, str] | None = None
) -> list[dict]:
    """入口: 候选 → 评分排序 → top5"""
    return rank_candidates(codes, regime=regime, names=names)[:5]


def main():
    parser = argparse.ArgumentParser(description="选股统一评分卡(100分)")
    parser.add_argument("--codes", required=True, help="候选股代码,逗号分隔(6位)")
    parser.add_argument("--regime", default=None, help="大盘情绪(强/中/弱, 可选, 缺省自动拉)")
    parser.add_argument("--names", default=None, help="名称映射JSON(可选)")
    args = parser.parse_args()

    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    regime = args.regime
    if regime is None:
        try:
            from market_sentiment import MarketSentiment

            regime = MarketSentiment().market_regime().get("regime")
        except Exception:
            regime = None
    names = json.loads(args.names) if args.names else None
    ranked = rank_candidates(codes, regime=regime, names=names)
    print(
        json.dumps(
            {"regime": regime, "total": len(ranked), "top5": ranked[:5], "ranked": ranked},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
