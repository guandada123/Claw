"""test_stock_score_card.py — 选股统一评分卡纯函数测试（P0-4）。

覆盖: 六维打分边界 / 排序降序 / 降级 / top5 / wind_monitor 集成。
"""

from unittest.mock import patch

import pytest
import stock_score_card as ssc
from stock_score_card import (
    score_ma20,
    score_momentum,
    score_rsi,
    score_sector,
    score_sentiment,
    score_volume_price,
)

# ── 各维度打分边界 ──


def test_momentum_linear_and_cap():
    assert score_momentum(None) == 0.0
    assert score_momentum(7.5) == pytest.approx(10.0, abs=0.5)  # 7.5/15*20=10
    assert score_momentum(15.0) == 20.0
    assert score_momentum(30.0) == 20.0  # 封顶
    assert score_momentum(-5.0) == 0.0  # 负涨幅0分


def test_volume_price_sweet_zone_and_edges():
    assert score_volume_price(None) == 0.0
    assert score_volume_price(2.0) == 20.0  # 最优区
    assert score_volume_price(1.0) == 20.0
    assert score_volume_price(3.0) == 20.0
    assert score_volume_price(0.5) < score_volume_price(1.0)  # 缩量降分
    assert score_volume_price(8.0) < score_volume_price(3.0)  # 爆量降分


def test_rsi_sweet_and_edges():
    assert score_rsi(None) == 0.0
    assert score_rsi(55.0) == 15.0  # 甜区
    assert score_rsi(45.0) == 15.0
    assert score_rsi(65.0) == 15.0
    assert score_rsi(85.0) == 3.0  # 超买最低
    assert score_rsi(10.0) == 3.0  # 超卖最低
    assert score_rsi(70.0) < score_rsi(65.0)  # 甜区外衰减


def test_ma20_distance():
    assert score_ma20(None) == 0.0
    assert score_ma20(0.05) == 15.0  # +5% 最优
    assert score_ma20(0.02) == 15.0
    assert score_ma20(0.10) == 15.0
    assert score_ma20(-0.03) == 3.0  # 破位最低
    assert score_ma20(0.25) == 3.0  # 追高最低
    assert score_ma20(0.15) < score_ma20(0.10)


def test_sentiment_and_sector_scores():
    assert score_sentiment("强") == 15.0
    assert score_sentiment("中") == 10.0
    assert score_sentiment("弱") == 5.0
    assert score_sentiment("未知") == 0.0
    assert score_sentiment(None) == 0.0
    assert score_sector("强") == 15.0
    assert score_sector("中") == 10.0
    assert score_sector("弱") == 5.0
    assert score_sector(None) == 0.0


# ── analyze_stock 数据降级 ──


def test_analyze_stock_all_degraded():
    with patch.object(ssc, "_fetch_kline", return_value=None):
        r = ssc.analyze_stock("600584", "测试", regime=None, ms=None)
    assert r["score"] == 0.0
    assert r["signals"]["close"] is None
    assert r["signals"]["rsi14"] is None


def test_analyze_stock_with_data():
    kl = [
        {"day": f"2026-08-{i:02d}", "close": 10.0 + i * 0.1, "volume": 1000.0 + i * 100}
        for i in range(40)
    ]
    import types

    ms = types.SimpleNamespace()
    ms.sector_strength = lambda code: {"sector": "半导体", "strength": "强"}
    with patch.object(ssc, "_fetch_kline", return_value=kl):
        r = ssc.analyze_stock("600584", "长电科技", regime="强", ms=ms)
    assert r["score"] > 50.0  # 上升趋势+强情绪+强板块 → 高分
    assert r["signals"]["m5_pct"] is not None
    assert r["sector_name"] == "半导体"
    assert r["sector_strength"] == "强"


# ── 排序 / top5 ──


def test_rank_candidates_sorts_desc():
    codes = ["600584", "000333", "002185"]
    names = {"600584": "长电科技", "000333": "美的集团", "002185": "华天科技"}
    with patch.object(
        ssc,
        "analyze_stock",
        side_effect=lambda c, n, r, m: {
            "code": c,
            "name": n,
            "score": {"600584": 80.0, "000333": 90.0, "002185": 70.0}[c],
            "signals": {},
            "sector_name": None,
            "sector_strength": None,
        },
    ):
        ranked = ssc.rank_candidates(codes, regime="强", names=names)
    scores = [r["score"] for r in ranked]
    assert scores == sorted(scores, reverse=True)
    assert ranked[0]["code"] == "000333"  # 最高分在前


def test_top5_limits():
    with patch.object(
        ssc,
        "analyze_stock",
        side_effect=lambda c, n, r, m: {
            "code": c,
            "name": n,
            "score": 50.0,
            "signals": {},
            "sector_name": None,
            "sector_strength": None,
        },
    ):
        result = ssc.top5(["600584", "000333", "002185", "600900", "601899", "000001", "002475"])
    assert len(result) <= 5


def test_rank_dedup():
    # 重复代码去重
    with patch.object(
        ssc,
        "analyze_stock",
        side_effect=lambda c, n, r, m: {
            "code": c,
            "name": n,
            "score": 50.0,
            "signals": {},
            "sector_name": None,
            "sector_strength": None,
        },
    ) as m:
        ranked = ssc.rank_candidates(["600584", "600584", "000333"], names={})
        assert len(ranked) == 2
        assert m.call_count == 2


# ── 常量 ──


def test_weights_sum_100():
    assert (
        ssc.W_MOMENTUM
        + ssc.W_VOLUME_PRICE
        + ssc.W_RSI
        + ssc.W_MA20
        + ssc.W_SENTIMENT
        + ssc.W_SECTOR
        == 100
    )
