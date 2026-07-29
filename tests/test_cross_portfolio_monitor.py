"""test_cross_portfolio_monitor.py — cross_portfolio_monitor 常量测试。"""

import cross_portfolio_monitor as cpm


def test_industry_map_contains_known_stocks():
    assert "002049" in cpm.INDUSTRY_MAP
    assert "601899" in cpm.INDUSTRY_MAP
    assert "300750" in cpm.INDUSTRY_MAP


def test_industry_map_values_valid():
    for code, industry in cpm.INDUSTRY_MAP.items():
        if len(code) == 6:  # 股票代码 6 位
            assert industry != ""
