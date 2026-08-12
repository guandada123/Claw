"""test_advisor_rules.py — advisor_rules 纯函数测试。

覆盖：_prefix、check_timing、check_rebuy_gate、risk_reward_card、_suggest_buy_zone
以及 check_double_account（mock _load_json）、check_entry（mock 网络调用）。
"""

import json
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
    flags = advisor.check_timing(
        {
            "bought_date": "2026-07-14",
            "avg_cost": 10.0,
            "current_price": 11.0,
        },
        today=date(2026, 7, 19),
    )
    # 5天，浮盈 +10%
    assert any("锁利" in f["reason"] for f in flags)


def test_check_timing_t7_stoploss(advisor):
    flags = advisor.check_timing(
        {
            "bought_date": "2026-07-10",
            "avg_cost": 100.0,
            "current_price": 85.0,
        },
        today=date(2026, 7, 19),
    )
    # 9天，回撤 -15%
    assert any("紧急减仓" in f["reason"] for f in flags)


def test_check_timing_no_current_price(advisor):
    flags = advisor.check_timing(
        {
            "bought_date": "2026-07-10",
            "avg_cost": 100.0,
        },
        today=date(2026, 7, 19),
    )
    assert flags == []


# ── risk_reward_card ──


def test_risk_reward_card_good_ratio(advisor):
    # 需要 > RISK_REWARD_MIN(1.5):1
    card = advisor.risk_reward_card(entry_price=50.0, stop_loss_pct=-0.05, take_profit_pct=0.15)
    assert card["risk_reward_ratio"] == 3.0
    assert "风险收益良好" in card["verdict"]


def test_risk_reward_card_bad_ratio(advisor):
    card = advisor.risk_reward_card(entry_price=100.0, stop_loss_pct=-0.02, take_profit_pct=0.02)
    assert card["risk_reward_ratio"] == pytest.approx(1.0)
    # 1:1 低于 1.5:1 门槛
    assert card["risk_reward_ratio"] < ar.RISK_REWARD_MIN


def test_risk_reward_card_zero_stoploss(advisor):
    card = advisor.risk_reward_card(entry_price=50.0, stop_loss_pct=0.0, take_profit_pct=0.05)
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
    p.write_text(
        '{"holdings": [{"code": "600000", "broker": "GJ"}], "summary": {"total_assets": 100000}}',
        encoding="utf-8",
    )
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
    """中性市 RSI 超买时应被 block。"""
    with (
        patch.object(advisor, "_get_rsi", return_value=75.0),
        patch.object(advisor, "_get_day_change", return_value=2.0),
        patch.object(advisor, "_get_ma20", return_value=10.0),
        patch.object(
            advisor,
            "_get_sentiment",
            return_value={"regime": {"regime": "中", "score": 50.0, "basis": [], "indexes": {}}},
        ),
    ):
        result = advisor.check_entry("600000", price=11.0)
    assert result["blocked"] is True
    assert any("RSI" in f["reason"] for f in result["flags"])


def test_check_entry_blocked_day_gain(advisor):
    """当日涨幅超限时被 block。"""
    with (
        patch.object(advisor, "_get_rsi", return_value=50.0),
        patch.object(advisor, "_get_day_change", return_value=7.0),
        patch.object(advisor, "_get_ma20", return_value=10.0),
        patch.object(
            advisor,
            "_get_sentiment",
            return_value={"regime": {"regime": "中", "score": 50.0, "basis": [], "indexes": {}}},
        ),
    ):
        result = advisor.check_entry("600000", price=10.5)
    assert result["blocked"] is True
    assert any("涨幅" in f["reason"] for f in result["flags"])


def test_check_entry_ok(advisor):
    """正常数值时不被 block。"""
    with (
        patch.object(advisor, "_get_rsi", return_value=50.0),
        patch.object(advisor, "_get_day_change", return_value=2.0),
        patch.object(advisor, "_get_ma20", return_value=10.0),
        patch.object(
            advisor,
            "_get_sentiment",
            return_value={"regime": {"regime": "中", "score": 50.0, "basis": [], "indexes": {}}},
        ),
    ):
        result = advisor.check_entry("600000", price=10.2)
    assert result["blocked"] is False
    assert result["suggested_buy_zone"] is not None


# ── 规则 H: 日亏总额熔断 ──


def _portfolio(daily_pct=None, daily_pnl=None, total=50000.0, holdings=None):
    summary = {"total_assets": total}
    if daily_pct is not None:
        summary["daily_pct"] = daily_pct
    if daily_pnl is not None:
        summary["daily_pnl"] = daily_pnl
    return {"summary": summary, "holdings": holdings or []}


def test_breaker_triggered_on_2pct_loss(advisor):
    r = advisor.check_daily_loss_breaker(_portfolio(daily_pct=-0.021))
    assert r["triggered"] is True
    assert r["stop_trading_today"] is True
    assert "停手" in r["reason"]


def test_breaker_not_triggered_small_loss(advisor):
    r = advisor.check_daily_loss_breaker(_portfolio(daily_pct=-0.015))
    assert r["triggered"] is False
    assert r["stop_trading_today"] is False


def test_breaker_percent_unit_normalization(advisor):
    # portfolio.json 的 daily_pct 是百分比数值(-0.58 表示 -0.58%) → 不触发
    r = advisor.check_daily_loss_breaker(_portfolio(daily_pct=-0.58))
    assert r["daily_pct"] == pytest.approx(-0.0058, abs=0.0001)
    assert r["triggered"] is False
    # 百分比数值达 -2.5 → 触发
    r2 = advisor.check_daily_loss_breaker(_portfolio(daily_pct=-2.5))
    assert r2["triggered"] is True


def test_breaker_derives_pct_from_pnl(advisor):
    # daily_pct 缺失 → 用 daily_pnl/total 换算
    r = advisor.check_daily_loss_breaker(_portfolio(daily_pnl=-1200.0, total=50000.0))
    assert r["daily_pct"] == pytest.approx(-0.024, abs=0.001)
    assert r["triggered"] is True


def test_breaker_fallback_holdings_estimate(advisor):
    # summary 无盈亏数据 → 用持仓现价 vs prev_close 估算
    holdings = [
        {"code": "600584", "shares": 300, "current_price": 76.0, "prev_close": 77.67},
        {"code": "002185", "shares": 400, "current_price": 18.0, "prev_close": 17.93},
    ]
    r = advisor.check_daily_loss_breaker(_portfolio(holdings=holdings))
    # 浮亏 = (76-77.67)*300 + (18-17.93)*400 = -501 + 28 = -473
    # 基准 = 77.67*300 + 17.93*400 = 23301 + 7172 = 30473
    assert r["daily_pct"] == pytest.approx(-473 / 30473, abs=0.001)
    assert r["triggered"] is False


def test_breaker_no_data_degrades(advisor):
    r = advisor.check_daily_loss_breaker(_portfolio())
    assert r["triggered"] is False
    assert r["daily_pct"] is None
    assert "缺失" in r["reason"]


def test_diagnose_portfolio_stop_trading_today(advisor):
    # 熔断触发 → 每个持仓 stop_trading_today=True + H block flag
    portfolio = _portfolio(
        daily_pct=-0.03,
        holdings=[{"code": "600584", "shares": 300, "avg_cost": 84.2, "current_price": 80.0}],
    )
    with (
        patch.object(advisor, "check_double_account", return_value=None),
        patch.object(advisor, "check_t0", return_value=None),
    ):
        r = advisor.diagnose_portfolio(portfolio, today=date(2026, 8, 12))
    assert r["stop_trading_today"] is True
    assert "🚨" in r["push_text"]
    assert r["holdings"][0]["stop_trading_today"] is True
    assert any(f["rule"] == "H" for f in r["holdings"][0]["flags"])


def test_diagnose_portfolio_normal_trading(advisor):
    portfolio = _portfolio(
        daily_pct=-0.01,
        holdings=[{"code": "600584", "shares": 300, "avg_cost": 84.2, "current_price": 80.0}],
    )
    with (
        patch.object(advisor, "check_double_account", return_value=None),
        patch.object(advisor, "check_t0", return_value=None),
    ):
        r = advisor.diagnose_portfolio(portfolio, today=date(2026, 8, 12))
    assert r["stop_trading_today"] is False
    assert r["holdings"][0]["stop_trading_today"] is False
    assert "🚨" not in r["push_text"]
    assert "今日停手" not in r["push_text"]


# ── 规则 I: 行业集中度上限 ──


def _portfolio_with_holdings(holdings, total=50000.0):
    return {
        "summary": {"total_assets": total, "daily_pct": -0.01},
        "holdings": holdings,
    }


def test_sector_concentration_warn_and_block(tmp_path, advisor):
    data_dir = tmp_path / ".workbuddy" / "data"
    data_dir.mkdir(parents=True)
    cache = data_dir / "sector_cache.json"
    cache.write_text('{"600584": "半导体", "002185": "半导体", "000333": "家电"}', encoding="utf-8")
    holdings = [
        {"code": "600584", "shares": 100, "avg_cost": 100.0, "current_price": 100.0},  # 半导体 40%
        {"code": "002185", "shares": 100, "avg_cost": 60.0, "current_price": 60.0},  # 半导体 24%
        {"code": "000333", "shares": 100, "avg_cost": 90.0, "current_price": 90.0},  # 家电 36%
    ]
    portfolio = tmp_path / "portfolio.json"
    portfolio.write_text(json.dumps(_portfolio_with_holdings(holdings)), encoding="utf-8")
    with patch.object(ar, "PROJECT_ROOT", tmp_path):
        r = advisor.check_sector_concentration(portfolio)
    # 半导体占比 (10000+6000)/25000 = 64% >50% → block；家电 36% 不超
    assert "半导体" in r["blocks"]
    assert "半导体" in r["warns"]
    assert "家电" not in r["blocks"]
    assert r["sectors"]["半导体"] == pytest.approx(0.64, abs=0.01)


def test_sector_concentration_no_holdings(advisor):
    r = advisor.check_sector_concentration()
    # 真实持仓文件可能为空或异常 → 不抛异常即可
    assert "sectors" in r
    assert "blocks" in r


def test_check_sector_block_blocks_sector(tmp_path, advisor):
    data_dir = tmp_path / ".workbuddy" / "data"
    data_dir.mkdir(parents=True)
    cache = data_dir / "sector_cache.json"
    cache.write_text('{"600584": "半导体", "002185": "半导体"}', encoding="utf-8")
    holdings = [
        {"code": "600584", "shares": 100, "avg_cost": 100.0, "current_price": 100.0},
        {"code": "002185", "shares": 100, "avg_cost": 60.0, "current_price": 60.0},
    ]
    portfolio = tmp_path / "portfolio.json"
    portfolio.write_text(json.dumps(_portfolio_with_holdings(holdings)), encoding="utf-8")
    with patch.object(ar, "PROJECT_ROOT", tmp_path):
        r = advisor.check_sector_block("600584", "半导体", portfolio)
    assert r is not None
    assert r["level"] == "block"
    assert "禁止" in r["reason"]


def test_check_sector_block_safe_sector(tmp_path, advisor):
    data_dir = tmp_path / ".workbuddy" / "data"
    data_dir.mkdir(parents=True)
    cache = data_dir / "sector_cache.json"
    cache.write_text('{"600584": "半导体"}', encoding="utf-8")
    holdings = [{"code": "600584", "shares": 100, "avg_cost": 100.0, "current_price": 100.0}]
    portfolio = tmp_path / "portfolio.json"
    portfolio.write_text(json.dumps(_portfolio_with_holdings(holdings)), encoding="utf-8")
    with patch.object(ar, "PROJECT_ROOT", tmp_path):
        r = advisor.check_sector_block("000333", "家电", portfolio)
    assert r is None  # 未持仓板块 → 不拦截


def test_check_entry_sector_block_applied(advisor):
    """check_entry: 推荐标的所属板块超50% → block"""
    with (
        patch.object(advisor, "_get_rsi", return_value=50.0),
        patch.object(advisor, "_get_day_change", return_value=1.0),
        patch.object(advisor, "_get_ma20", return_value=10.0),
        patch.object(advisor, "_get_live_price", return_value=None),
        patch.object(advisor, "_find_holding", return_value=None),
        patch.object(advisor, "_get_sentiment", return_value=None),
        patch.object(
            advisor,
            "check_sector_block",
            return_value={
                "level": "block",
                "rule": "I",
                "sector": "半导体",
                "pct": 0.64,
                "reason": "🚫 板块「半导体」持仓占比 64% 超 50% 上限 → 禁止新推荐该板块标的",
            },
        ),
    ):
        r = advisor.check_entry("600584", price=80.0)
    assert r["blocked"] is True
    assert any(f["rule"] == "I" for f in r["flags"])


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
