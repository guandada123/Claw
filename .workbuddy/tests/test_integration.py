"""
Claw 集成测试 — 端到端流程验证
覆盖: backtest CLI, sim_trade 完整买卖流程
"""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


# ═══════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════


@pytest.fixture
def mock_kline_response():
    """模拟东方财富 API 返回的 K 线数据（50天上涨趋势）"""
    klines = []
    for i in range(50):
        price = 15.0 + i * 0.2
        klines.append(
            f"2025-01-{(i % 28) + 2:02d},"
            f"{price - 0.1:.2f},{price:.2f},{price + 0.15:.2f},"
            f"{price - 0.2:.2f},500000,{500000 * price:.0f}"
        )
    return {"data": {"klines": klines}}


@pytest.fixture
def empty_portfolio(tmp_path):
    """空的模拟持仓文件"""
    pf = {
        "config": {
            "initial_capital": 30000.0,
            "created_at": "2025-01-01",
            "updated_at": "2025-01-01 00:00:00",
        },
        "cash": 30000.0,
        "positions": {},
        "transactions": [],
        "daily_snapshot": {},
        "dividends": [],
    }
    pf_file = tmp_path / "portfolio.json"
    pf_file.write_text(json.dumps(pf, ensure_ascii=False))
    return pf_file


@pytest.fixture
def portfolio_with_position(tmp_path):
    """带有一只持仓的模拟文件"""
    pf = {
        "config": {
            "initial_capital": 30000.0,
            "created_at": "2025-01-01",
            "updated_at": "2025-01-10 10:00:00",
        },
        "cash": 17000.0,
        "positions": {
            "600519": {
                "shares": 100,
                "avg_cost": 130.0,
                "current_price": 145.0,
                "highest_price": 148.0,
                "buy_date": "2025-01-05",
                "take_profit_level": 0,
                "sector": "白酒",
            }
        },
        "transactions": [
            {
                "date": "2025-01-05",
                "code": "600519",
                "type": "BUY",
                "price": 130.0,
                "shares": 100,
                "amount": 13000.0,
            }
        ],
        "daily_snapshot": {"2025-01-05": {"value": 30000.0}},
        "dividends": [],
    }
    pf_file = tmp_path / "portfolio.json"
    pf_file.write_text(json.dumps(pf, ensure_ascii=False))
    return pf_file


# ═══════════════════════════════════════
# Integration: backtest CLI 端到端
# ═══════════════════════════════════════


class TestBacktestCLI:
    """测试 backtest.py 作为 CLI 工具的完整流程"""

    def test_cli_no_args_shows_usage(self):
        """无参数运行应输出用法说明"""
        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "backtest.py")],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        output = json.loads(result.stdout)
        assert output["ok"] is False
        assert "strategies" in output
        assert "ma-cross" in output["strategies"]

    def test_cli_invalid_strategy_shows_error(self):
        """无效策略名应报错"""
        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "backtest.py"), "invalid", "600519"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        output = json.loads(result.stdout)
        assert output["ok"] is False
        assert "未知策略" in output["error"]

    def test_backtest_ma_cross_with_mocked_data(self, mock_kline_response):
        """MA交叉策略完整回测（mock网络层）"""
        from backtest import backtest_ma_cross, fetch_kline

        # Mock fetch_kline 返回模拟数据
        with patch("backtest.urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(mock_kline_response).encode()
            mock_resp.__enter__ = lambda s: mock_resp
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            data = fetch_kline("600519", "2025-01-01", "2025-03-01")

        assert len(data) == 50

        # 执行回测
        report = backtest_ma_cross(data, init_capital=30000)

        # 验证报告完整性
        assert report["ok"] is True
        assert report["init_capital"] == 30000
        assert report["final_capital"] > 0
        assert report["trading_days"] == 50
        assert isinstance(report["trades"], list)
        assert report["total_return_pct"] == pytest.approx(
            (report["final_capital"] - 30000) / 30000 * 100, abs=0.1
        )

    def test_breakout_strategy_end_to_end(self, mock_kline_response):
        """突破策略端到端回测"""
        from backtest import backtest_breakout, fetch_kline

        with patch("backtest.urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(mock_kline_response).encode()
            mock_resp.__enter__ = lambda s: mock_resp
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            data = fetch_kline("000001", "2025-01-01", "2025-03-01")

        report = backtest_breakout(data, init_capital=30000)
        assert report["ok"] is True
        assert report["max_drawdown_pct"] >= 0
        assert report["sharpe_ratio"] is not None


# ═══════════════════════════════════════
# Integration: sim_trade 完整买卖流程
# ═══════════════════════════════════════


class TestSimTradeWorkflow:
    """测试模拟交易的完整生命周期"""

    def test_check_restricted_integration(self):
        """板块限制完整验证"""
        from sim_trade import check_restricted

        # 所有允许的代码
        allowed = ["600519", "601398", "000001", "002594", "000858"]
        for code in allowed:
            assert check_restricted(code) is None, f"{code} 应该被允许"

        # 所有禁止的代码
        blocked = ["300750", "301269", "688981", "833171"]
        for code in blocked:
            result = check_restricted(code)
            assert result is not None, f"{code} 应该被禁止"

    def test_buy_commission_calculation(self):
        """买入佣金计算（含最低佣金兜底）"""
        from sim_trade import COMMISSION_RATE, MIN_COMMISSION, calc_commission, calc_stamp_tax

        # 大金额：佣金 > 最低线
        assert calc_commission(100000) == pytest.approx(100000 * COMMISSION_RATE)
        # 小金额：佣金 < 最低线，取最低值
        assert calc_commission(1000) == MIN_COMMISSION
        # 买入无印花税
        assert calc_stamp_tax(100000, is_sell=False) == 0.0
        # 卖出有印花税
        assert calc_stamp_tax(100000, is_sell=True) > 0

    def test_portfolio_total_asset_calculation(self, portfolio_with_position):
        """持仓总资产计算"""
        from sim_trade import calc_total_asset

        pf = json.loads(portfolio_with_position.read_text())
        # cash: 17000 + positions: 100 * 145 = 14500 → total: 31500
        total = calc_total_asset(pf)
        assert total == pytest.approx(31500.0)

    def test_full_buy_sell_cycle(self, empty_portfolio, tmp_path):
        """完整买卖周期：空仓 → 买入 → 验证持仓 → 卖出 → 验证收益"""
        import sim_trade

        # 重定向 portfolio 文件到临时目录
        original_file = sim_trade.PORTFOLIO_FILE
        sim_trade.PORTFOLIO_FILE = empty_portfolio
        sim_trade.HISTORY_DIR = tmp_path / "history"
        sim_trade.HISTORY_DIR.mkdir()

        try:
            # Step 1: 加载空仓
            pf = sim_trade.load_portfolio()
            assert pf["cash"] == 30000.0
            assert len(pf["positions"]) == 0

            # Step 2: 模拟买入 600519 (100股 @ 130元)
            buy_price = 130.0
            shares = 100
            cost = buy_price * shares  # 13000
            commission = sim_trade.calc_commission(cost)

            pf["cash"] -= cost + commission
            pf["positions"]["600519"] = {
                "shares": shares,
                "avg_cost": buy_price,
                "current_price": buy_price,
                "highest_price": buy_price,
                "buy_date": "2025-01-05",
                "take_profit_level": 0,
            }
            pf["transactions"].append(
                {
                    "date": "2025-01-05",
                    "code": "600519",
                    "type": "BUY",
                    "price": buy_price,
                    "shares": shares,
                    "amount": cost,
                }
            )
            sim_trade.save_portfolio(pf)

            # Step 3: 验证持仓
            pf = sim_trade.load_portfolio()
            assert "600519" in pf["positions"]
            assert pf["positions"]["600519"]["shares"] == 100
            assert pf["cash"] < 30000.0

            # Step 4: 模拟卖出（股价涨到150）
            sell_price = 150.0
            sell_amount = sell_price * shares
            stamp_tax = sim_trade.calc_stamp_tax(sell_amount, is_sell=True)
            sell_commission = sim_trade.calc_commission(sell_amount)

            pf["cash"] += sell_amount - stamp_tax - sell_commission
            del pf["positions"]["600519"]
            pf["transactions"].append(
                {
                    "date": "2025-01-15",
                    "code": "600519",
                    "type": "SELL",
                    "price": sell_price,
                    "shares": shares,
                    "amount": sell_amount,
                }
            )
            sim_trade.save_portfolio(pf)

            # Step 5: 验证收益
            pf = sim_trade.load_portfolio()
            assert len(pf["positions"]) == 0
            # 收益 = (150-130) * 100 - 佣金*2 - 印花税
            expected_profit = (
                (sell_price - buy_price) * shares - commission - sell_commission - stamp_tax
            )
            assert pf["cash"] == pytest.approx(30000.0 + expected_profit, abs=1.0)
            assert pf["cash"] > 30000.0  # 确保盈利

        finally:
            sim_trade.PORTFOLIO_FILE = original_file

    def test_stop_loss_trigger(self, portfolio_with_position, tmp_path):
        """止损触发验证"""
        import sim_trade

        original_file = sim_trade.PORTFOLIO_FILE
        sim_trade.PORTFOLIO_FILE = portfolio_with_position
        # Mock save_portfolio
        sim_trade.save_portfolio = lambda pf: portfolio_with_position.write_text(
            json.dumps(pf, ensure_ascii=False)
        )

        try:
            pf = sim_trade.load_portfolio()
            # 将当前价格设为大幅亏损（-10%）
            pf["positions"]["600519"]["current_price"] = 117.0  # 成本130，跌到117 = -10%
            pf["positions"]["600519"]["highest_price"] = 130.0

            result = sim_trade.check_stop_loss(pf, "600519")
            # -10% > -8% 止损线，应触发
            assert result["should_sell"] is True
        finally:
            sim_trade.PORTFOLIO_FILE = original_file
