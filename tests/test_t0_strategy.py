"""test_t0_strategy.py — 做T（T+0）子策略引擎纯函数测试。

覆盖: R7无底仓 / R1额度与铁律 / R3频率 / R4止损 / R2方向 / R5安全垫 /
R6节点窗口 / advisor_rules.check_t0 集成 / check_entry t0_suggestion。
"""

from datetime import datetime
from unittest.mock import patch

import advisor_rules as ar
import pytest
import t0_strategy as t0s
from t0_strategy import T0Strategy


@pytest.fixture
def strategy():
    return T0Strategy()


def _holding(**kw):
    h = {"code": "600584", "name": "长电科技", "shares": 300, "avg_cost": 84.2}
    h.update(kw)
    return h


# ── R7: 无底仓 ──


def test_no_base_position_no_t0(strategy):
    r = strategy.evaluate(_holding(shares=0), price=77.0, ma20=78.0)
    assert r["t0"] is False
    assert r["direction"] is None
    assert any("底仓" in f["reason"] for f in r["flags"])


# ── R1: T仓额度与铁律 ──


def test_t_position_value_is_10pct_of_base(strategy):
    r = strategy.evaluate(_holding(shares=300, avg_cost=80.0), price=80.0, ma20=79.0)
    assert r["t_position_value"] == pytest.approx(300 * 80.0 * 0.10, abs=0.01)


def test_iron_rule_t_position_never_exceeds_base():
    # ratio>1 → T仓超底仓 → 铁律拦截
    r = T0Strategy(ratio=1.5).evaluate(_holding(shares=100, avg_cost=10.0), price=10.0, ma20=9.0)
    assert r["blocked"] is True
    assert any("铁律" in f["reason"] for f in r["flags"])


# ── R3: 频率上限 ──


def test_frequency_limit_blocks(strategy):
    r = strategy.evaluate(_holding(), price=77.0, ma20=78.0, t_count_today=2)
    assert r["blocked"] is True
    assert any("上限" in f["reason"] for f in r["flags"])


def test_frequency_below_limit_ok(strategy):
    r = strategy.evaluate(_holding(), price=77.0, ma20=78.0, t_count_today=1)
    assert r["blocked"] is False
    assert r["t0"] is True


# ── R4: 单次止损 ──


def test_t_pnl_loss_blocks(strategy):
    # 无ATR数据(自动拉取失败) → 固定3%止损线，-4%触发
    with patch.object(strategy, "_fetch_atr14", return_value=None):
        r = strategy.evaluate(_holding(), price=77.0, ma20=78.0, t_pnl_pct=-0.04)
    assert r["blocked"] is True
    assert any("止损" in f["reason"] for f in r["flags"])


def test_t_pnl_small_loss_ok(strategy):
    r = strategy.evaluate(_holding(), price=77.0, ma20=78.0, t_pnl_pct=-0.02)
    assert r["blocked"] is False


# ── R2: 方向（20日线）──


def test_direction_positive_above_ma20(strategy):
    r = strategy.evaluate(_holding(), price=80.0, ma20=78.0)
    assert r["direction"] == "正T"


def test_direction_reverse_below_ma20(strategy):
    r = strategy.evaluate(_holding(), price=76.0, ma20=78.0)
    assert r["direction"] == "反T"


# ── R8: 情绪修正（强反弹下微回踩MA20 → 正T）──


def test_r8_strong_rally_slight_dip_below_ma20_is_positive():
    # 自低点+40%强反弹 + 价格仅低于MA20 0.4% → 顺势正T（修正单均线钝化）
    r = T0Strategy().evaluate(_holding(), price=77.67, ma20=78.0, rally_pct=40.0)
    assert r["direction"] == "正T"
    assert "情绪修正" in r["sentiment_note"]


def test_r8_no_strong_rally_slight_dip_is_reverse(strategy):
    # 弱反弹（自低点+2%）+ 微低于MA20 → 维持反T
    r = strategy.evaluate(_holding(), price=77.67, ma20=78.0, rally_pct=2.0)
    assert r["direction"] == "反T"
    assert r["sentiment_note"] == ""


def test_r8_deep_break_below_ma20_always_reverse():
    # 即使强反弹，破位>2% → 仍反T（情绪修正不掩盖破位）
    r = T0Strategy().evaluate(_holding(), price=75.0, ma20=78.0, rally_pct=40.0)
    assert r["direction"] == "反T"


def test_r8_above_ma20_positive_regardless(strategy):
    r = strategy.evaluate(_holding(), price=80.0, ma20=78.0, rally_pct=-5.0)
    assert r["direction"] == "正T"


def test_direction_fallback_to_avg_cost(strategy):
    # 无MA20 → 用持仓成本定向
    r = strategy.evaluate(_holding(avg_cost=75.0), price=80.0, ma20=None)
    assert r["direction"] == "正T"


# ── R5: 正T安全垫 ──


def test_positive_t_buy_below_cost_safety(strategy):
    r = strategy.evaluate(_holding(avg_cost=84.2), price=85.0, ma20=84.0)
    assert r["direction"] == "正T"
    assert r["buy_below"] == pytest.approx(84.2 * 0.98, abs=0.01)


# ── R6: 10:10 节点窗口 ──


def test_node_window_10_10(strategy):
    r = strategy.evaluate(_holding(), price=80.0, ma20=78.0, now=datetime(2026, 8, 12, 10, 10))
    assert "节点" in r["summary"]


def test_node_window_outside(strategy):
    r = strategy.evaluate(_holding(), price=80.0, ma20=78.0, now=datetime(2026, 8, 12, 14, 0))
    assert "节点" not in r["summary"]


# ── R9: VWAP 分时位 ──


def test_r9_vwap_above_positive_boost():
    # 正T + 现价>VWAP → 加力
    r = T0Strategy().evaluate(
        _holding(), price=80.0, ma20=78.0, vwap=79.0
    )
    assert r["direction"] == "正T"
    assert "正T加力" in r["vwap_note"]


def test_r9_vwap_below_reverse_boost():
    # 反T + 现价<VWAP → 加力
    r = T0Strategy().evaluate(
        _holding(), price=77.0, ma20=78.0, vwap=78.5
    )
    assert r["direction"] == "反T"
    assert "反T加力" in r["vwap_note"]


def test_r9_vwap_divergence_warns():
    # 方向与VWAP背离 → 提示谨慎不误判方向
    r = T0Strategy().evaluate(
        _holding(), price=80.0, ma20=78.0, vwap=81.0
    )
    assert r["direction"] == "正T"
    assert "背离" in r["vwap_note"]
    assert "谨慎" in r["vwap_note"]


def test_r9_vwap_neutral_zone():
    # 现价≈VWAP → 中性区，不加力
    r = T0Strategy().evaluate(
        _holding(), price=80.0, ma20=78.0, vwap=80.02
    )
    assert "中性区" in r["vwap_note"]


def test_r9_vwap_auto_fetch():
    # vwap 未传 → 自动拉取（mock 返回）
    s = T0Strategy()
    with patch.object(s, "_fetch_vwap", return_value=79.5):
        r = s.evaluate(_holding(), price=80.0, ma20=78.0)
    assert r["vwap"] == pytest.approx(79.5)
    assert "偏强" in r["vwap_note"]


# ── R10: Pivot Point 具体价位 ──


def test_r10_pivot_calculation():
    # 经典公式: P=(H+L+C)/3, R1=2P-L, S1=2P-H
    s = T0Strategy()
    p = s._calc_pivot(80.0, 77.0, 78.5)
    assert p["P"] == pytest.approx((80.0 + 77.0 + 78.5) / 3, abs=0.01)
    assert p["R1"] == pytest.approx(2 * p["P"] - 77.0, abs=0.01)
    assert p["S1"] == pytest.approx(2 * p["P"] - 80.0, abs=0.01)


def test_r10_pivot_in_plan_positive_t():
    # 正T: entry 带 S1 低吸价, exit 带 R1 高抛价
    r = T0Strategy().evaluate(
        _holding(), price=80.0, ma20=78.0, prev_bar={"high": 81.0, "low": 77.0, "close": 79.0}
    )
    assert r["pivot"] is not None
    assert "S1" in r["plan"]["entry_rule"]
    assert "R1" in r["plan"]["exit_rule"]
    assert "S1" in r["summary"]


def test_r10_pivot_in_plan_reverse_t():
    # 反T: entry 带 R1 高抛价, exit 带 S1 低吸回补价
    r = T0Strategy().evaluate(
        _holding(), price=76.0, ma20=78.0, prev_bar={"high": 80.0, "low": 75.0, "close": 77.5}
    )
    assert r["pivot"] is not None
    assert "R1" in r["plan"]["entry_rule"]
    assert "S1" in r["plan"]["exit_rule"]


def test_r10_pivot_auto_fetch():
    # prev_bar 未传 → 自动拉取（mock）
    s = T0Strategy()
    with patch.object(s, "_fetch_prev_bar", return_value={"high": 81.0, "low": 77.0, "close": 79.0}):
        r = s.evaluate(_holding(), price=80.0, ma20=78.0)
    assert r["pivot"] is not None
    assert r["pivot"]["R1"] > r["pivot"]["P"] > r["pivot"]["S1"]


def test_r10_no_prev_bar_degrades():
    # 无昨日K线 → pivot=None 不阻断
    r = T0Strategy().evaluate(_holding(), price=80.0, ma20=78.0)
    assert r["t0"] is True
    assert r["pivot"] is None or r["pivot"]  # 自动拉取成功与否都不影响 t0


# ── advisor_rules.check_t0 集成 ──


@pytest.fixture
def advisor():
    return ar.AdvisorRules()


def test_check_t0_integration(advisor):
    holding = {"code": "600584", "shares": 300, "avg_cost": 84.2, "current_price": 80.0}
    quotes = {"600584": {"price": 80.0, "ma20": 78.0}}
    with patch.object(advisor, "_get_ma20", return_value=78.0):
        r = advisor.check_t0(holding, quotes)
    assert r is not None
    assert r["direction"] == "正T"
    assert r["t0"] is True


def test_check_t0_no_holding_returns_none_or_inactive(advisor):
    holding = {"code": "600584", "shares": 0}
    r = advisor.check_t0(holding, {"600584": {"price": 80.0, "ma20": 78.0}})
    # 无底仓 → t0=False（不返回None，返回inactive结果）
    assert r is not None
    assert r["t0"] is False


def test_diagnose_holding_contains_t0(advisor):
    holding = {"code": "600584", "shares": 300, "avg_cost": 84.2, "current_price": 80.0}
    quotes = {"600584": {"price": 80.0, "ma20": 78.0}}
    with (
        patch.object(advisor, "check_double_account", return_value=None),
        patch.object(advisor, "_get_ma20", return_value=78.0),
    ):
        diag = advisor.diagnose_holding(holding, quotes)
    assert diag["t0_strategy"] is not None
    assert any(f["rule"] == "G" for f in diag["flags"])


def test_check_entry_attaches_t0_suggestion(advisor):
    holding = {"code": "600584", "shares": 300, "avg_cost": 84.2, "current_price": 80.0}
    with (
        patch.object(advisor, "_get_rsi", return_value=50.0),
        patch.object(advisor, "_get_day_change", return_value=2.0),
        patch.object(advisor, "_get_ma20", return_value=78.0),
        patch.object(advisor, "_get_live_price", return_value=None),
        patch.object(advisor, "_find_holding", return_value=holding),
        patch.object(advisor, "check_sector_block", return_value=None),
        patch.object(
            advisor,
            "check_t0",
            return_value={
                "code": "600584",
                "t0": True,
                "direction": "正T",
                "t_position_value": 2400.0,
                "flags": [],
                "blocked": False,
                "summary": "长电科技 正T | T仓额度¥2400(底仓10%)",
            },
        ),
    ):
        r = advisor.check_entry("600584", price=80.0)
    assert r["blocked"] is False
    assert r["t0_suggestion"] is not None
    assert r["t0_suggestion"]["direction"] == "正T"


# ── R11: ATR 动态止损 ──


def test_r11_atr_calculation():
    # 15根bar → TR序列 → 14日均值
    bars = [(10.0 + i * 0.5, 9.8 + i * 0.5, 9.9 + i * 0.5) for i in range(15)]
    atr = T0Strategy._calc_atr14(bars)
    assert atr is not None
    assert 0 < atr < 2


def test_r11_atr_insufficient_bars_returns_none():
    assert T0Strategy._calc_atr14([(10, 9, 9.5)]) is None


def test_r11_low_volatility_tightens_stop():
    # 低波动: ATR14/价 ≈ 1% → 动态止损=1.5%(下限), 低于固定3%
    r = T0Strategy().evaluate(
        _holding(), price=100.0, ma20=99.0, atr14=1.0
    )
    assert r["atr_stop_pct"] == pytest.approx(0.015, abs=0.0001)
    assert "低波动收紧" in r["stop_loss_note"]
    # -2% 浮亏在动态1.5%下应触发止损(固定3%不触发)
    r2 = T0Strategy().evaluate(
        _holding(), price=100.0, ma20=99.0, atr14=1.0, t_pnl_pct=-0.02
    )
    assert r2["blocked"] is True
    assert any("止损" in f["reason"] for f in r2["flags"])


def test_r11_high_volatility_widens_stop():
    # 高波动: ATR14/价 ≈ 5% → 动态止损=5%(夹在6%内), 高于固定3%
    r = T0Strategy().evaluate(
        _holding(), price=100.0, ma20=99.0, atr14=5.0
    )
    assert r["atr_stop_pct"] == pytest.approx(0.05, abs=0.0001)
    assert "高波动放宽" in r["stop_loss_note"]
    # -4% 浮亏在动态5%下不触发(固定3%会触发)
    r2 = T0Strategy().evaluate(
        _holding(), price=100.0, ma20=99.0, atr14=5.0, t_pnl_pct=-0.04
    )
    assert r2["blocked"] is False


def test_r11_atr_cap_at_6pct():
    # 极端高波动: ATR14/价 = 10% → 夹到6%上限
    r = T0Strategy().evaluate(
        _holding(), price=100.0, ma20=99.0, atr14=10.0
    )
    assert r["atr_stop_pct"] == pytest.approx(0.06, abs=0.0001)


def test_r11_no_atr_falls_back_to_fixed():
    # 无ATR(网络失败/未传) → 回退固定3%止损，不阻断
    s = T0Strategy()
    with patch.object(s, "_fetch_atr14", return_value=None):
        r = s.evaluate(_holding(), price=80.0, ma20=78.0)
    assert r["atr14"] is None
    assert r["atr_stop_pct"] is None
    assert r["stop_loss_note"] == ""
    assert r["t0"] is True
    # 固定3%下 -4% 触发止损（第二个evaluate不在patch上下文 → 用独立实例mock）
    s2 = T0Strategy()
    with patch.object(s2, "_fetch_atr14", return_value=None):
        r2 = s2.evaluate(_holding(), price=80.0, ma20=78.0, t_pnl_pct=-0.04)
    assert r2["blocked"] is True


def test_r11_atr_auto_fetch():
    # atr14 未传 → 自动拉取（mock）
    s = T0Strategy()
    with patch.object(s, "_fetch_atr14", return_value=2.0):
        r = s.evaluate(_holding(), price=100.0, ma20=99.0)
    assert r["atr14"] == pytest.approx(2.0)
    assert r["atr_stop_pct"] == pytest.approx(0.02, abs=0.0001)


# ── 常量 ──


def test_constants_reasonable():
    assert pytest.approx(0.10) == t0s.T_POSITION_RATIO
    assert t0s.MAX_T_PER_DAY == 2
    assert pytest.approx(0.03) == t0s.T_STOP_LOSS_PCT
    assert pytest.approx(0.02) == t0s.T_COST_SAFETY_PCT
    assert t0s.T_NODE_TIME == "10:10"
    assert t0s.ATR_PERIOD == 14
    assert pytest.approx(0.015) == t0s.ATR_STOP_FLOOR
    assert pytest.approx(0.06) == t0s.ATR_STOP_CAP
