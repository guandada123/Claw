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


# ── 常量 ──


def test_constants_reasonable():
    assert pytest.approx(0.10) == t0s.T_POSITION_RATIO
    assert t0s.MAX_T_PER_DAY == 2
    assert pytest.approx(0.03) == t0s.T_STOP_LOSS_PCT
    assert pytest.approx(0.02) == t0s.T_COST_SAFETY_PCT
    assert t0s.T_NODE_TIME == "10:10"
