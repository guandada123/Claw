"""test_market_sentiment.py — 市场情绪层纯函数测试。

覆盖: regime分级(强/中/弱) / sector_strength(命中映射/无映射/腾讯失败降级) /
行业缓存读写 / check_entry 情绪调节(弱市+弱板块block、强市RSI放宽)。
"""

from unittest.mock import patch

import advisor_rules as ar
import market_sentiment as ms
import pytest
from market_sentiment import MarketSentiment


@pytest.fixture
def sentiment():
    return MarketSentiment()


def _kline(rally_pct: float, above_ma20: bool, m5_pct: float) -> list:
    """构造新浪K线(≥25根): 用价格序列模拟反弹/MA20/动量"""
    base = 100.0
    low = base / (1 + rally_pct / 100)
    closes = [low * (1 + rally_pct / 100) * (1 + i * 0.001) for i in range(29)]
    # 让最后1根价格体现 above_ma20
    ma20 = sum(closes[-20:-1]) / 19
    closes.append(ma20 * (1.01 if above_ma20 else 0.99))
    lows = [low] * 29 + [min(closes[-1], low)]
    return [
        (f"2026-07-{i % 28 + 1:02d}", c, low, c) for i, (c, low) in enumerate(zip(closes, lows))
    ]


# ── market_regime ──


def test_regime_strong(sentiment):
    with patch.object(ms, "_fetch_kline", return_value=_kline(12.0, True, 3.0)):
        r = sentiment.market_regime()
    assert r["regime"] == "强"
    assert r["score"] >= 70


def test_regime_weak(sentiment):
    with patch.object(ms, "_fetch_kline", return_value=_kline(1.0, False, -3.0)):
        r = sentiment.market_regime()
    assert r["regime"] == "弱"
    assert r["score"] < 40


def test_regime_mid(sentiment):
    with patch.object(ms, "_fetch_kline", return_value=_kline(5.0, True, 0.5)):
        r = sentiment.market_regime()
    assert r["regime"] in ("中", "强")  # 反弹5%+站上MA20 → 中偏强


def test_regime_data_unavailable(sentiment):
    with patch.object(ms, "_fetch_kline", return_value=None):
        r = sentiment.market_regime()
    assert r["regime"] == "未知"
    assert r["score"] is None


# ── sector_strength ──


def test_sector_strength_hit(sentiment):
    with (
        patch.object(ms, "_fetch_industry_name", return_value="半导体"),
        patch.object(
            ms,
            "_fetch_tencent_quote",
            return_value={"name": "半导体", "price": 10032.0, "change_pct": 1.5},
        ),
    ):
        r = sentiment.sector_strength("600584")
    assert r["sector"] == "半导体"
    assert r["strength"] == "强"
    assert r["board_code"] == "pt01801081"


def test_sector_strength_weak(sentiment):
    with (
        patch.object(ms, "_fetch_industry_name", return_value="半导体"),
        patch.object(
            ms,
            "_fetch_tencent_quote",
            return_value={"name": "半导体", "price": 10032.0, "change_pct": -2.0},
        ),
    ):
        r = sentiment.sector_strength("600584")
    assert r["strength"] == "弱"


def test_sector_strength_no_map(sentiment):
    with (
        patch.object(ms, "_fetch_industry_name", return_value="未知行业"),
        patch.object(ms, "_fetch_tencent_quote", return_value=None),
    ):
        r = sentiment.sector_strength("600584")
    assert r["strength"] is None
    assert "无板块代码映射" in r["note"]


def test_sector_strength_quote_fail(sentiment):
    with (
        patch.object(ms, "_fetch_industry_name", return_value="半导体"),
        patch.object(ms, "_fetch_tencent_quote", return_value=None),
    ):
        r = sentiment.sector_strength("600584")
    assert r["strength"] is None
    assert "获取失败" in r["note"]


def test_sector_strength_industry_fail(sentiment):
    with patch.object(ms, "_fetch_industry_name", return_value=None):
        r = sentiment.sector_strength("600584")
    assert r is None


# ── 行业缓存 ──


def test_industry_cache_lookup(tmp_path, sentiment):
    cache_file = tmp_path / "sector_cache.json"
    cache_file.write_text('{"600584": "半导体"}', encoding="utf-8")
    with (
        patch.object(ms, "SECTOR_CACHE", cache_file),
        patch.object(ms, "_load_industry_cache", wraps=ms._load_industry_cache),
        patch.object(ms, "_fetch_industry_name") as mock_fetch,
    ):
        mock_fetch.side_effect = lambda code: ms._load_industry_cache().get(code)
        r = sentiment.sector_strength("600584")
    assert r is not None
    assert r["sector"] == "半导体"


# ── check_entry 情绪调节 ──


@pytest.fixture
def advisor():
    return ar.AdvisorRules()


def _sentiment(regime: str, sector_strength: str | None = None) -> dict:
    ctx = {"regime": {"regime": regime, "score": 90.0, "basis": [], "indexes": {}}}
    if sector_strength:
        ctx["sector"] = {
            "sector": "半导体",
            "board_code": "pt01801081",
            "change_pct": -2.0,
            "strength": sector_strength,
            "note": f"板块当日-2.00% → {sector_strength}",
        }
    return ctx


def test_check_entry_weak_market_weak_sector_blocked(advisor):
    """弱市 + 弱板块 → 直接 block"""
    with (
        patch.object(advisor, "_get_rsi", return_value=50.0),
        patch.object(advisor, "_get_day_change", return_value=1.0),
        patch.object(advisor, "_get_ma20", return_value=10.0),
        patch.object(advisor, "_get_live_price", return_value=None),
        patch.object(advisor, "_find_holding", return_value=None),
        patch.object(advisor, "_get_sentiment", return_value=_sentiment("弱", "弱")),
    ):
        r = advisor.check_entry("600000", price=10.2)
    assert r["blocked"] is True
    assert any("大盘弱" in f["reason"] for f in r["flags"])


def test_check_entry_strong_market_rsi_70_not_blocked(advisor):
    """强市 RSI=75 → 不 block（阈值放宽到80，防强趋势钝化）"""
    with (
        patch.object(advisor, "_get_rsi", return_value=75.0),
        patch.object(advisor, "_get_day_change", return_value=2.0),
        patch.object(advisor, "_get_ma20", return_value=10.0),
        patch.object(advisor, "_get_live_price", return_value=None),
        patch.object(advisor, "_find_holding", return_value=None),
        patch.object(advisor, "_get_sentiment", return_value=_sentiment("强")),
    ):
        r = advisor.check_entry("600000", price=10.2)
    assert r["blocked"] is False


def test_check_entry_mid_market_rsi_75_blocked(advisor):
    """中性市 RSI=75 → 仍 block（维持原阈值70）"""
    with (
        patch.object(advisor, "_get_rsi", return_value=75.0),
        patch.object(advisor, "_get_day_change", return_value=2.0),
        patch.object(advisor, "_get_ma20", return_value=10.0),
        patch.object(advisor, "_get_live_price", return_value=None),
        patch.object(advisor, "_find_holding", return_value=None),
        patch.object(advisor, "_get_sentiment", return_value=_sentiment("中")),
    ):
        r = advisor.check_entry("600000", price=10.2)
    assert r["blocked"] is True
    assert any("RSI" in f["reason"] for f in r["flags"])


def test_check_entry_weak_market_day_gain_gt3_blocked(advisor):
    """弱市当日涨幅>3% → block"""
    with (
        patch.object(advisor, "_get_rsi", return_value=40.0),
        patch.object(advisor, "_get_day_change", return_value=4.0),
        patch.object(advisor, "_get_ma20", return_value=10.0),
        patch.object(advisor, "_get_live_price", return_value=None),
        patch.object(advisor, "_find_holding", return_value=None),
        patch.object(advisor, "_get_sentiment", return_value=_sentiment("弱")),
    ):
        r = advisor.check_entry("600000", price=10.2)
    assert r["blocked"] is True
    assert any("弱市追涨" in f["reason"] for f in r["flags"])


def test_check_entry_sentiment_in_output(advisor):
    with (
        patch.object(advisor, "_get_rsi", return_value=50.0),
        patch.object(advisor, "_get_day_change", return_value=2.0),
        patch.object(advisor, "_get_ma20", return_value=10.0),
        patch.object(advisor, "_get_live_price", return_value=None),
        patch.object(advisor, "_find_holding", return_value=None),
        patch.object(advisor, "_get_sentiment", return_value=_sentiment("强")),
    ):
        r = advisor.check_entry("600000", price=10.2)
    assert r["sentiment"] is not None
    assert r["sentiment"]["regime"]["regime"] == "强"


def test_constants_reasonable():
    assert ms.REGIME_STRONG == 70
    assert ms.REGIME_WEAK == 40
    assert "半导体" in ms.SECTOR_CODE_MAP
