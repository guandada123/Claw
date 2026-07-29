"""test_advisor_rules.py — advisor_rules 纯函数测试。

覆盖：_prefix、check_timing、check_rebuy_gate、risk_reward_card、_suggest_buy_zone
以及 check_double_account（mock _load_json）、check_entry（mock 网络调用）。
"""

from datetime import date
from pathlib import Path
from unittest.mock import patch

import advisor_rules as ar
import pytest


@pytest.fixture
def advisor():
    return ar.AdvisorRules()


# ── _prefix ──


def test_prefix_sh_6xxxx(advisor):
    assert advisor._prefix("600000") == "sh600000"


def test_prefix_sz_0xxxx(advisor):
    assert advisor._prefix("000001") == "sz000001"


def test_prefix_already_prefixed(advisor):
    assert advisor._prefix("sh600000") == "sh600000"
    assert advisor._prefix("SZ000001") == "sz000001"


# ── _suggest_buy_zone ──


def test_suggest_buy_zone_with_ma20(advisor):
    result = advisor._suggest_buy_zone(price=10.0, ma20=9.5, rsi=None)
    assert "参考买区" in result
    assert "MA20" in result


def test_suggest_buy_zone_no_ma20_overbought_rsi(advisor):
    result = advisor._suggest_buy_zone(price=10.0, ma20=None, rsi=75)
    assert "RSI 回落" in result


def test_suggest_buy_zone_no_ma20_normal_rsi(advisor):
    result = advisor._suggest_buy_zone(price=10.0, ma20=None, rsi=50)
    assert "回调" in result
    assert "9.50" in result


def test_suggest_buy_zone_none_price(advisor):
    result = advisor._suggest_buy_zone(price=None, ma20=None, rsi=None)
    assert "价格未知" in result


# ── check_timing ──


def test_check_timing_no_bought_date(advisor):
    flags = advisor.check_timing({"current_price": 10.0}, date(2026, 7, 19))
    assert flags == []


def test_check_timing_t3_profit(advisor):
    flags = advisor.check_timing({
        "bought_date": "2026-07-14",
        "avg_cost": 10.0,
        "current_price": 11.0,
    }, today=date(2026, 7, 19))
    # 5天，浮盈 +10%
    assert any("锁利" in f["reason"] for f in flags)


def test_check_timing_t7_stoploss(advisor):
    flags = advisor.check_timing({
        "bought_date": "2026-07-10",
        "avg_cost": 100.0,
        "current_price": 85.0,
    }, today=date(2026, 7, 19))
    # 9天，回撤 -15%
    assert any("紧急减仓" in f["reason"] for f in flags)


def test_check_timing_no_current_price(advisor):
    flags = advisor.check_timing({
        "bought_date": "2026-07-10",
        "avg_cost": 100.0,
    }, today=date(2026, 7, 19))
    assert flags == []


# ── risk_reward_card ──


def test_risk_reward_card_good_ratio(advisor):
    # 需要 > RISK_REWARD_MIN(1.5):1
    card = advisor.risk_reward_card(entry_price=50.0,
                                     stop_loss_pct=-0.05,
                                     take_profit_pct=0.15)
    assert card["risk_reward_ratio"] == 3.0
    assert "风险收益良好" in card["verdict"]


def test_risk_reward_card_bad_ratio(advisor):
    card = advisor.risk_reward_card(entry_price=100.0,
                                     stop_loss_pct=-0.02,
                                     take_profit_pct=0.02)
    assert card["risk_reward_ratio"] == pytest.approx(1.0)
    # 1:1 低于 1.5:1 门槛
    assert card["risk_reward_ratio"] < ar.RISK_REWARD_MIN


def test_risk_reward_card_zero_stoploss(advisor):
    card = advisor.risk_reward_card(entry_price=50.0,
                                     stop_loss_pct=0.0,
                                     take_profit_pct=0.05)
    assert card["risk_reward_ratio"] is None


# ── check_rebuy_gate ──


def test_rebuy_gate_no_recent(advisor):
    result = advisor.check_rebuy_gate("600000", [], today=date(2026, 7, 19))
    assert result["triggered"] is False


def test_rebuy_gate_loss_sells_trigger(advisor):
    trade_log = [
        {"date": "2026-07-01", "side": "sell", "pnl": -0.1},
        {"date": "2026-07-05", "side": "sell", "pnl": -0.05},
    ]
    result = advisor.check_rebuy_gate("600000", trade_log, today=date(2026, 7, 19))
    assert result["triggered"] is True
    assert "亏损卖出" in " ".join(result["reasons"])


def test_rebuy_gate_rapid_buys(advisor):
    trade_log = [
        {"date": "2026-07-12", "side": "buy"},
        {"date": "2026-07-13", "side": "buy"},
        {"date": "2026-07-14", "side": "buy"},
    ]
    result = advisor.check_rebuy_gate("600000", trade_log, today=date(2026, 7, 19))
    assert result["triggered"] is True
    assert "摊薄" in " ".join(result["reasons"])


# ── check_double_account ──


def test_check_double_account_single_broker(tmp_path, advisor):
    p = tmp_path / "portfolio.json"
    p.write_text('{"holdings": [{"code": "600000", "broker": "GJ"}], "summary": {"total_assets": 100000}}', encoding="utf-8")
    result = advisor.check_double_account("600000", portfolio_path=p)
    assert result is None  # 单账户，不触发


def test_check_double_account_double_broker_over_limit(tmp_path, advisor):
    p = tmp_path / "portfolio.json"
    p.write_text(
        '{"holdings": [{"code": "600000", "broker": "GJ", "shares": 100, "avg_cost": 500},'
        '{"code": "600000", "broker": "GF", "shares": 100, "avg_cost": 500}],'
        '"summary": {"total_assets": 100000}}',
        encoding="utf-8",
    )
    result = advisor.check_double_account("600000", portfolio_path=p)
    assert result is not None
    assert result["double_account"] is True
    assert result["over_limit"] is True


def test_check_double_account_no_portfolio(advisor):
    result = advisor.check_double_account("600000", portfolio_path=Path("/nonexistent/path.json"))
    assert result is None


# ── check_entry (mock 外部依赖) ──


def test_check_entry_blocked_rsi_overbought(advisor):
    """RSI 超买时应被 block。"""
    with patch.object(advisor, "_get_rsi", return_value=75.0), \
         patch.object(advisor, "_get_day_change", return_value=2.0), \
         patch.object(advisor, "_get_ma20", return_value=10.0):
        result = advisor.check_entry("600000", price=11.0)
    assert result["blocked"] is True
    assert any("RSI" in f["reason"] for f in result["flags"])


def test_check_entry_blocked_day_gain(advisor):
    """当日涨幅超限时被 block。"""
    with patch.object(advisor, "_get_rsi", return_value=50.0), \
         patch.object(advisor, "_get_day_change", return_value=7.0), \
         patch.object(advisor, "_get_ma20", return_value=10.0):
        result = advisor.check_entry("600000", price=10.5)
    assert result["blocked"] is True
    assert any("涨幅" in f["reason"] for f in result["flags"])


def test_check_entry_ok(advisor):
    """正常数值时不被 block。"""
    with patch.object(advisor, "_get_rsi", return_value=50.0), \
         patch.object(advisor, "_get_day_change", return_value=2.0), \
         patch.object(advisor, "_get_ma20", return_value=10.0):
        result = advisor.check_entry("600000", price=10.2)
    assert result["blocked"] is False
    assert result["suggested_buy_zone"] is not None


# ── 常量 ──


def test_constants_reasonable():
    assert ar.RSI_OVERBOUGHT == 70
    assert ar.DAY_GAIN_WARN == 5.0
    assert ar.DEFAULT_STOP_LOSS == -0.08
    assert ar.DEFAULT_TAKE_PROFIT == 0.05


def test_thresholds_positive():
    assert ar.T3_LOCK_PROFIT_DAYS >= 3
    assert ar.T7_STOPLOSS_DAYS >= 7
    assert ar.RISK_REWARD_MIN >= 1.0
