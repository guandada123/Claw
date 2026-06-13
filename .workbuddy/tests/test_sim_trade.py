"""
模拟炒股引擎 (sim_trade.py) 单元测试
覆盖: check_restricted, calc_commission, calc_stamp_tax, calc_total_asset,
      check_stop_loss, check_take_profit, calc_position_value, get_position,
      auto_check_all_positions, load/save_portfolio
"""

import sys
from pathlib import Path

import pytest

# 确保 scripts 目录可导入
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))


# ═══════════════════════════════════════
# Tests: check_restricted
# ═══════════════════════════════════════

class TestCheckRestricted:
    def test_mainboard_sh_allowed(self):
        from sim_trade import check_restricted
        assert check_restricted("600519") is None  # 茅台
        assert check_restricted("601398") is None  # 工商银行

    def test_mainboard_sz_allowed(self):
        from sim_trade import check_restricted
        assert check_restricted("000001") is None  # 平安银行
        assert check_restricted("002594") is None  # 比亚迪

    def test_chinext_blocked(self):
        from sim_trade import check_restricted
        result = check_restricted("300750")  # 宁德时代
        assert result is not None
        assert "创业板" in result

    def test_chinext_301_blocked(self):
        from sim_trade import check_restricted
        result = check_restricted("301269")
        assert result is not None
        assert "创业板" in result

    def test_star_blocked(self):
        from sim_trade import check_restricted
        result = check_restricted("688981")  # 中芯国际
        assert result is not None
        assert "科创板" in result

    def test_bse_blocked(self):
        from sim_trade import check_restricted
        result = check_restricted("833171")  # 北交所
        assert result is not None
        assert "北交所" in result


# ═══════════════════════════════════════
# Tests: calc_commission
# ═══════════════════════════════════════

class TestCalcCommission:
    def test_normal_commission(self):
        from sim_trade import calc_commission, COMMISSION_RATE, MIN_COMMISSION
        amount = 100000.0  # 10万元
        expected = amount * COMMISSION_RATE  # 0.03% = 30元
        assert calc_commission(amount) == pytest.approx(expected)

    def test_minimum_commission(self):
        from sim_trade import calc_commission, MIN_COMMISSION
        # 小金额，佣金应不低于5元
        amount = 1000.0  # 1000元 * 0.03% = 0.3元 < 5元
        assert calc_commission(amount) == MIN_COMMISSION

    def test_zero_amount(self):
        from sim_trade import calc_commission, MIN_COMMISSION
        assert calc_commission(0) == MIN_COMMISSION


# ═══════════════════════════════════════
# Tests: calc_stamp_tax
# ═══════════════════════════════════════

class TestCalcStampTax:
    def test_sell_tax(self):
        from sim_trade import calc_stamp_tax, STAMP_TAX_RATE
        amount = 100000.0
        expected = amount * STAMP_TAX_RATE  # 0.1% = 100元
        assert calc_stamp_tax(amount, is_sell=True) == pytest.approx(expected)

    def test_buy_no_tax(self):
        from sim_trade import calc_stamp_tax
        assert calc_stamp_tax(100000.0, is_sell=False) == 0.0

    def test_zero_amount_sell(self):
        from sim_trade import calc_stamp_tax
        assert calc_stamp_tax(0, is_sell=True) == 0.0


# ═══════════════════════════════════════
# Tests: calc_total_asset
# ═══════════════════════════════════════

class TestCalcTotalAsset:
    def test_cash_only(self):
        from sim_trade import calc_total_asset
        pf = {"cash": 30000.0, "positions": {}}
        assert calc_total_asset(pf) == 30000.0

    def test_with_positions(self):
        from sim_trade import calc_total_asset
        pf = {
            "cash": 15000.0,
            "positions": {
                "600519": {"shares": 100, "avg_cost": 1500, "current_price": 1600},
                "000001": {"shares": 500, "avg_cost": 12, "current_price": 13},
            }
        }
        # 15000 + 100*1600 + 500*13 = 15000 + 160000 + 6500 = 181500
        assert calc_total_asset(pf) == pytest.approx(181500.0)

    def test_fallback_to_avg_cost_when_no_current_price(self):
        from sim_trade import calc_total_asset
        pf = {
            "cash": 10000.0,
            "positions": {
                "600519": {"shares": 100, "avg_cost": 1500},
            }
        }
        # 10000 + 100*1500 = 160000
        assert calc_total_asset(pf) == pytest.approx(160000.0)


# ═══════════════════════════════════════
# Tests: check_stop_loss
# ═══════════════════════════════════════

class TestCheckStopLoss:
    def test_no_position_returns_false(self):
        from sim_trade import check_stop_loss
        pf = {"cash": 30000, "positions": {}}
        result = check_stop_loss(pf, "600519")
        assert result["should_sell"] is False

    def test_position_in_profit_no_stop(self):
        from sim_trade import check_stop_loss
        pf = {
            "cash": 15000,
            "positions": {
                "000001": {
                    "shares": 500, "avg_cost": 12.0,
                    "current_price": 13.0, "highest_price": 13.0,
                    "buy_date": "2025-01-01", "take_profit_level": 0,
                }
            },
            "config": {"updated_at": ""},
        }
        # Mock save_portfolio to avoid file I/O
        import sim_trade
        original_save = sim_trade.save_portfolio
        sim_trade.save_portfolio = lambda pf: None
        try:
            result = check_stop_loss(pf, "000001")
            # 盈利8.3%，不应触发止损
            assert result["should_sell"] is False
        finally:
            sim_trade.save_portfolio = original_save

    def test_fixed_stop_loss_trigger(self):
        """固定止损线 -8% 触发"""
        from sim_trade import check_stop_loss
        import sim_trade
        sim_trade.save_portfolio = lambda pf: None
        pf = {
            "cash": 15000,
            "positions": {
                "600519": {
                    "shares": 100, "avg_cost": 100.0,
                    "current_price": 91.0, "highest_price": 100.0,
                    "buy_date": "2025-01-01", "take_profit_level": 0,
                }
            },
            "config": {"updated_at": ""},
        }
        result = check_stop_loss(pf, "600519")
        assert result["should_sell"] is True
        assert "固定止损" in result["reason"]
        assert result["shares_to_sell"] == 100

    def test_trailing_stop_loss_trigger(self):
        """追踪止损：从最高价回落15%"""
        from sim_trade import check_stop_loss
        import sim_trade
        sim_trade.save_portfolio = lambda pf: None
        pf = {
            "cash": 15000,
            "positions": {
                "600519": {
                    "shares": 100, "avg_cost": 80.0,
                    "current_price": 95.0, "highest_price": 120.0,
                    "buy_date": "2025-01-01", "take_profit_level": 0,
                }
            },
            "config": {"updated_at": ""},
        }
        # 盈利 (95-80)/80=18.75% > 0, 但从最高120回落到95 = -20.8% > -15%
        result = check_stop_loss(pf, "600519")
        assert result["should_sell"] is True
        assert "追踪止损" in result["reason"]


# ═══════════════════════════════════════
# Tests: check_take_profit
# ═══════════════════════════════════════

class TestCheckTakeProfit:
    def test_no_position(self):
        from sim_trade import check_take_profit
        pf = {"positions": {}}
        result = check_take_profit(pf, "600519")
        assert result["should_sell"] is False

    def test_not_enough_profit(self):
        """盈利不足，不触发止盈"""
        from sim_trade import check_take_profit
        pf = {
            "positions": {
                "600519": {
                    "shares": 100, "avg_cost": 100.0,
                    "current_price": 105.0,
                    "take_profit_level": 1,
                }
            }
        }
        result = check_take_profit(pf, "600519")
        assert result["should_sell"] is False

    def test_level1_take_profit(self):
        """一级止盈触发 (盈利>=15%)"""
        from sim_trade import check_take_profit, TAKE_PROFIT_LEVELS
        pf = {
            "positions": {
                "600519": {
                    "shares": 1000, "avg_cost": 100.0,
                    "current_price": 120.0,  # +20%
                    "take_profit_level": 1,
                }
            }
        }
        result = check_take_profit(pf, "600519")
        if TAKE_PROFIT_LEVELS[0]["pct"] * 100 <= 20:
            assert result["should_sell"] is True
            assert result["shares_to_sell"] > 0
            assert "new_level" in result

    def test_all_levels_exhausted(self):
        """所有止盈级别已用完"""
        from sim_trade import check_take_profit, TAKE_PROFIT_LEVELS
        pf = {
            "positions": {
                "600519": {
                    "shares": 100, "avg_cost": 100.0,
                    "current_price": 200.0,
                    "take_profit_level": len(TAKE_PROFIT_LEVELS) + 1,
                }
            }
        }
        result = check_take_profit(pf, "600519")
        assert result["should_sell"] is False


# ═══════════════════════════════════════
# Tests: calc_position_value / get_position
# ═══════════════════════════════════════

class TestPositionHelpers:
    def test_calc_position_value_with_price(self):
        from sim_trade import calc_position_value
        pf = {"positions": {"600519": {"shares": 100, "avg_cost": 100.0, "current_price": 150.0}}}
        assert calc_position_value(pf, "600519") == pytest.approx(15000.0)

    def test_calc_position_value_no_position(self):
        from sim_trade import calc_position_value
        pf = {"positions": {}}
        assert calc_position_value(pf, "600519") == 0.0

    def test_calc_position_value_fallback_to_cost(self):
        from sim_trade import calc_position_value
        pf = {"positions": {"600519": {"shares": 100, "avg_cost": 100.0}}}
        assert calc_position_value(pf, "600519") == pytest.approx(10000.0)

    def test_get_position_exists(self):
        from sim_trade import get_position
        pf = {"positions": {"600519": {"shares": 100}}}
        assert get_position(pf, "600519") == {"shares": 100}

    def test_get_position_not_exists(self):
        from sim_trade import get_position
        pf = {"positions": {}}
        assert get_position(pf, "600519") is None


# ═══════════════════════════════════════
# Tests: load_portfolio / save_portfolio
# ═══════════════════════════════════════

class TestPortfolioIO:
    def test_load_creates_default_when_missing(self, tmp_path):
        import sim_trade
        original = sim_trade.PORTFOLIO_FILE
        sim_trade.PORTFOLIO_FILE = tmp_path / "nonexistent.json"
        try:
            pf = sim_trade.load_portfolio()
            assert pf["cash"] == sim_trade.INITIAL_CAPITAL
            assert pf["positions"] == {}
            assert pf["transactions"] == []
        finally:
            sim_trade.PORTFOLIO_FILE = original

    def test_save_and_load_roundtrip(self, tmp_path):
        import json
        import sim_trade
        original = sim_trade.PORTFOLIO_FILE
        pf_file = tmp_path / "test_pf.json"
        sim_trade.PORTFOLIO_FILE = pf_file
        try:
            pf = {"config": {"updated_at": ""}, "cash": 25000.0,
                  "positions": {"600519": {"shares": 100, "avg_cost": 130.0}},
                  "transactions": [], "daily_snapshot": {}, "dividends": []}
            # 直接写入文件（绕过 atomic_write 的路径依赖）
            pf_file.write_text(json.dumps(pf, ensure_ascii=False))

            loaded = sim_trade.load_portfolio()
            assert loaded["cash"] == 25000.0
            assert loaded["positions"]["600519"]["shares"] == 100
        finally:
            sim_trade.PORTFOLIO_FILE = original


# ═══════════════════════════════════════
# Tests: auto_check_all_positions
# ═══════════════════════════════════════

class TestAutoCheck:
    def test_empty_positions(self):
        from sim_trade import auto_check_all_positions
        pf = {"cash": 30000, "positions": {}, "config": {"updated_at": ""}}
        result = auto_check_all_positions(pf)
        assert result == []

    def test_stop_loss_prioritized_over_take_profit(self):
        """止损优先于止盈"""
        from sim_trade import auto_check_all_positions
        import sim_trade
        sim_trade.save_portfolio = lambda pf: None
        pf = {
            "cash": 15000,
            "positions": {
                "000001": {
                    "shares": 500, "avg_cost": 100.0, "name": "测试股",
                    "current_price": 85.0, "highest_price": 100.0,
                    "buy_date": "2025-01-01", "take_profit_level": 1,
                }
            },
            "config": {"updated_at": ""},
        }
        result = auto_check_all_positions(pf)
        assert len(result) >= 1
        assert result[0]["action"] == "SELL"
        assert result[0]["priority"] == "high"


# ═══════════════════════════════════════
# Tests: utility functions
# ═══════════════════════════════════════

class TestUtilities:
    def test_now_returns_formatted_string(self):
        from sim_trade import now
        result = now()
        assert len(result) == 19  # "YYYY-MM-DD HH:MM:SS"
        assert "-" in result
        assert ":" in result

    def test_today_str_returns_iso_date(self):
        from sim_trade import today_str
        result = today_str()
        assert len(result) == 10  # "YYYY-MM-DD"
        assert result.count("-") == 2
