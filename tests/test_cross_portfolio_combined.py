"""test_cross_portfolio_combined.py — cross_portfolio_monitor calc_combined_metrics 测试。"""

import pytest

import cross_portfolio_monitor as cpm


@pytest.fixture(autouse=True)
def isolate_sanity(monkeypatch):
    """隔离 _sanity_guard 网络调用（测试用受控价，不触发真实 sanity 改写）。"""
    monkeypatch.setattr(
        cpm, "_sanity_guard",
        lambda code, price: {"fail": False, "reliable_price": price, "reason": ""}
    )


def test_calc_combined_metrics_empty():
    result = cpm.calc_combined_metrics({}, [])
    assert result["shared_holdings"] == []
    assert result["sim_market_value_total"] == 0
    assert result["user_market_value_total"] == 0
    assert result["sanity_failed"] == 0


def test_calc_combined_sim_only():
    sim = {
        "600000": {"current_price": 10.0, "shares": 100, "avg_cost": 9.5, "name": "平安银行"},
        "000001": {"current_price": 20.0, "shares": 50, "avg_cost": 18.0, "name": "平安"},
    }
    result = cpm.calc_combined_metrics(sim, [])
    assert result["sim_market_value_total"] == (10*100 + 20*50)
    assert len(result["shared_holdings"]) == 0
    assert result["sanity_failed"] == 0


def test_calc_combined_shared_holdings():
    sim = {
        "600000": {"current_price": 10.0, "shares": 100, "avg_cost": 9.5, "name": "平安银行"},
    }
    user = [
        {"code": "600000", "name": "平安", "current_price": 10.5, "shares": 50,
         "cost_price": 9.0, "market_value": 525, "pnl_pct": 16.67},
    ]
    result = cpm.calc_combined_metrics(sim, user)
    assert len(result["shared_holdings"]) == 1
    shared = result["shared_holdings"][0]
    assert shared["code"] == "600000"
    assert shared["sim"]["shares"] == 100
    assert shared["user"]["shares"] == 50


def test_calc_combined_industry_concentration():
    sim = {
        "002049": {"current_price": 100.0, "shares": 10, "avg_cost": 90, "name": "紫光国微"},
        "002601": {"current_price": 20.0, "shares": 50, "avg_cost": 18, "name": "龙佰集团"},
    }
    result = cpm.calc_combined_metrics(sim, [])
    assert "🏭 科技/半导体" in result["industry_concentration"]
    assert "🛢️ 周期/资源" in result["industry_concentration"]


def test_sanity_guard_isolates_bad_price(monkeypatch):
    """_sanity_guard 标记 fail 时，calc 应隔离错误价（不计入总市值）并计数。"""
    monkeypatch.setattr(
        cpm, "_sanity_guard",
        lambda code, price: {"fail": True, "reliable_price": 0.0, "reason": "G1: 偏差>30%"}
    )
    sim = {
        "600000": {"current_price": 10.0, "shares": 100, "avg_cost": 9.5, "name": "平安银行"},
    }
    result = cpm.calc_combined_metrics(sim, [])
    # 错误价被隔离为 0 → 总市值=0，sanity_failed=1
    assert result["sim_market_value_total"] == 0
    assert result["sanity_failed"] == 1
