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


# ── 情绪周期5段化（P1-5）──


def test_cycle_freeze_low_zt():
    r = ms.classify_cycle(zt_count=20, zhaban_rate=0.30, max_lianban=1)
    assert r["cycle"] == "冰点"
    assert r["position_ratio"] == "≤20%"


def test_cycle_freeze_high_zhaban():
    r = ms.classify_cycle(zt_count=60, zhaban_rate=0.45, max_lianban=3)
    assert r["cycle"] == "冰点"  # 炸板率>40% 优先


def test_cycle_repair():
    r = ms.classify_cycle(zt_count=40, zhaban_rate=0.20, max_lianban=3)
    assert r["cycle"] == "修复"
    assert r["position_ratio"] == "30-50%"


def test_cycle_heating():
    r = ms.classify_cycle(zt_count=65, zhaban_rate=0.15, max_lianban=5)
    assert r["cycle"] == "升温"
    assert r["position_ratio"] == "50-70%"


def test_cycle_frenzy():
    r = ms.classify_cycle(zt_count=95, zhaban_rate=0.10, max_lianban=6)
    assert r["cycle"] == "狂热"
    assert "减仓" in r["position_ratio"]


def test_cycle_tide_high_zhaban_low_lianban():
    r = ms.classify_cycle(zt_count=45, zhaban_rate=0.38, max_lianban=2)
    assert r["cycle"] == "退潮"
    assert "空仓" in r["position_ratio"]


def test_cycle_unknown_no_data():
    r = ms.classify_cycle(zt_count=None, zhaban_rate=None, max_lianban=None)
    assert r["cycle"] == "未知"
    assert r["position_ratio"] is None


def test_sentiment_cycle_degrades_on_fetch_fail(sentiment):
    with patch.object(ms, "_fetch_breadth", return_value=None):
        r = sentiment.sentiment_cycle()
    assert r["cycle"] == "未知"
    assert r["position_ratio"] is None


def test_sentiment_cycle_with_breadth(sentiment):
    with patch.object(
        ms,
        "_fetch_breadth",
        return_value={"zt_count": 60, "zb_count": 10, "max_lianban": 4, "zhaban_rate": 0.143},
    ):
        r = sentiment.sentiment_cycle()
    assert r["cycle"] == "升温"
    assert r["breadth"]["zt_count"] == 60


# ── check_entry 情绪周期拦截（P1-5）──


def _sentiment_with_cycle(regime: str, cycle: dict | None = None) -> dict:
    ctx = _sentiment(regime)
    ctx["cycle"] = cycle or {"cycle": "修复", "position_ratio": "30-50%", "basis": "涨停40家"}
    return ctx


def test_check_entry_ice_cycle_blocked(advisor):
    """情绪周期冰点 → 新开仓 block"""
    with (
        patch.object(advisor, "_get_rsi", return_value=50.0),
        patch.object(advisor, "_get_day_change", return_value=1.0),
        patch.object(advisor, "_get_ma20", return_value=10.0),
        patch.object(advisor, "_get_live_price", return_value=None),
        patch.object(advisor, "_find_holding", return_value=None),
        patch.object(
            advisor,
            "_get_sentiment",
            return_value=_sentiment_with_cycle("中", {"cycle": "冰点", "position_ratio": "≤20%", "basis": "涨停15家"}),
        ),
    ):
        r = advisor.check_entry("600000", price=10.2)
    assert r["blocked"] is True
    assert any("冰点" in f["reason"] for f in r["flags"])


def test_check_entry_frenzy_cycle_blocked(advisor):
    """情绪周期狂热 → 防高位接盘 block"""
    with (
        patch.object(advisor, "_get_rsi", return_value=50.0),
        patch.object(advisor, "_get_day_change", return_value=1.0),
        patch.object(advisor, "_get_ma20", return_value=10.0),
        patch.object(advisor, "_get_live_price", return_value=None),
        patch.object(advisor, "_find_holding", return_value=None),
        patch.object(
            advisor,
            "_get_sentiment",
            return_value=_sentiment_with_cycle("强", {"cycle": "狂热", "position_ratio": "减仓至≤40%", "basis": "涨停100家"}),
        ),
    ):
        r = advisor.check_entry("600000", price=10.2)
    assert r["blocked"] is True
    assert any("狂热" in f["reason"] for f in r["flags"])


def test_check_entry_repair_cycle_not_blocked_by_cycle(advisor):
    """修复周期 → 不由周期拦截（正常放行，除非其他规则）"""
    with (
        patch.object(advisor, "_get_rsi", return_value=50.0),
        patch.object(advisor, "_get_day_change", return_value=1.0),
        patch.object(advisor, "_get_ma20", return_value=10.0),
        patch.object(advisor, "_get_live_price", return_value=None),
        patch.object(advisor, "_find_holding", return_value=None),
        patch.object(
            advisor,
            "_get_sentiment",
            return_value=_sentiment_with_cycle("强", {"cycle": "修复", "position_ratio": "30-50%", "basis": "涨停40家"}),
        ),
    ):
        r = advisor.check_entry("600000", price=10.2)
    assert not any("周期" in f["reason"] and f["level"] == "block" for f in r["flags"])


def test_constants_reasonable():
    assert ms.REGIME_STRONG == 70
    assert ms.REGIME_WEAK == 40
    assert "半导体" in ms.SECTOR_CODE_MAP
    assert ms.CYCLE_POSITION["退潮"] == "≤10%或空仓"
