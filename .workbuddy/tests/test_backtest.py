"""
回测引擎 (backtest.py) 单元测试
覆盖: calc_ma, calc_highest, backtest_ma_cross, backtest_breakout, _calc_report
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# 确保 scripts 目录可导入
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))


# ═══════════════════════════════════════
# Fixtures: 模拟K线数据
# ═══════════════════════════════════════

@pytest.fixture
def simple_kline():
    """20根K线 — 单调上涨后回落，用于验证MA交叉"""
    prices = [
        10.0, 10.2, 10.5, 10.3, 10.6,    # 0-4
        10.8, 11.0, 11.2, 10.9, 10.7,    # 5-9
        10.5, 10.3, 10.1, 10.0, 10.2,    # 10-14
        10.4, 10.6, 10.8, 11.0, 11.3,    # 15-19
        11.5, 11.8, 12.0, 11.7, 11.4,    # 20-24
        11.1, 10.8, 10.5, 10.3, 10.0,    # 25-29
    ]
    data = []
    for i, p in enumerate(prices):
        data.append({
            "date": f"2025-01-{i+2:02d}",
            "open": round(p - 0.05, 2),
            "close": round(p, 2),
            "high": round(p + 0.1, 2),
            "low": round(p - 0.1, 2),
            "volume": 100000 + i * 1000,
            "amount": (100000 + i * 1000) * p,
        })
    return data


@pytest.fixture
def trending_kline():
    """50根K线 — 有明显趋势变化，用于完整回测"""
    import math
    prices = []
    for i in range(50):
        # 先涨后跌再涨: 正弦波 + 微涨趋势
        base = 20.0 + i * 0.08
        wave = math.sin(i / 8.0 * math.pi) * 2.0
        prices.append(round(base + wave, 2))
    data = []
    for i, p in enumerate(prices):
        data.append({
            "date": f"2025-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
            "open": round(p - 0.1, 2),
            "close": round(p, 2),
            "high": round(p + 0.2, 2),
            "low": round(p - 0.15, 2),
            "volume": 200000,
            "amount": 200000 * p,
        })
    return data


# ═══════════════════════════════════════
# Tests: calc_ma
# ═══════════════════════════════════════

class TestCalcMA:
    def test_basic_5day_ma(self, simple_kline):
        from backtest import calc_ma
        ma = calc_ma(simple_kline, 5)
        # 前4个应为 None
        assert ma[0] is None
        assert ma[3] is None
        # 第5个(index 4)应为前5天收盘价平均
        expected = sum(d["close"] for d in simple_kline[:5]) / 5
        assert ma[4] == pytest.approx(expected, abs=0.01)

    def test_ma_length_matches_input(self, simple_kline):
        from backtest import calc_ma
        ma = calc_ma(simple_kline, 5)
        assert len(ma) == len(simple_kline)

    def test_all_none_when_period_exceeds_data(self):
        from backtest import calc_ma
        data = [{"close": 10.0}] * 3
        ma = calc_ma(data, 5)
        assert all(v is None for v in ma)

    def test_single_period(self):
        from backtest import calc_ma
        data = [{"close": float(i)} for i in range(10)]
        ma = calc_ma(data, 1)
        # MA(1) == close price itself
        for i, v in enumerate(ma):
            assert v == pytest.approx(float(i), abs=0.001)


# ═══════════════════════════════════════
# Tests: calc_highest
# ═══════════════════════════════════════

class TestCalcHighest:
    def test_basic_highest(self, simple_kline):
        from backtest import calc_highest
        highest = calc_highest(simple_kline, 5)
        assert len(highest) == len(simple_kline)
        # 第一个元素 = 自身的 high
        assert highest[0] == simple_kline[0]["high"]

    def test_ascending_prices(self):
        from backtest import calc_highest
        data = [{"high": float(i)} for i in range(10)]
        highest = calc_highest(data, 3)
        # highest[5] 应该为 max(data[3:6].high) = 5.0
        assert highest[5] == 5.0

    def test_single_day(self):
        from backtest import calc_highest
        data = [{"high": 99.9}]
        highest = calc_highest(data, 20)
        assert highest[0] == 99.9


# ═══════════════════════════════════════
# Tests: backtest_ma_cross
# ═══════════════════════════════════════

class TestBacktestMACross:
    def test_returns_valid_report(self, trending_kline):
        from backtest import backtest_ma_cross
        report = backtest_ma_cross(trending_kline, init_capital=30000)
        assert report["ok"] is True
        assert "total_return_pct" in report
        assert "sharpe_ratio" in report
        assert "max_drawdown_pct" in report
        assert "win_rate" in report
        assert "trades" in report

    def test_final_capital_consistent(self, trending_kline):
        from backtest import backtest_ma_cross
        report = backtest_ma_cross(trending_kline, init_capital=30000)
        # total_return_pct 应与 init/final capital 一致
        expected_pct = (report["final_capital"] - 30000) / 30000 * 100
        assert report["total_return_pct"] == pytest.approx(expected_pct, abs=0.1)

    def test_no_trades_with_flat_prices(self):
        """完全平价的数据应该不产生交易"""
        from backtest import backtest_ma_cross
        flat_data = [
            {"date": f"2025-01-{i+1:02d}", "open": 10, "close": 10,
             "high": 10, "low": 10, "volume": 100000, "amount": 1000000}
            for i in range(30)
        ]
        report = backtest_ma_cross(flat_data, init_capital=30000)
        assert report["ok"] is True
        assert report["total_trades"] == 0

    def test_all_trades_have_required_fields(self, trending_kline):
        from backtest import backtest_ma_cross
        report = backtest_ma_cross(trending_kline, init_capital=30000)
        for trade in report["trades"]:
            assert "date" in trade
            assert "action" in trade
            assert trade["action"] in ("BUY", "SELL")
            assert "price" in trade
            assert trade["price"] > 0

    def test_drawdown_non_negative(self, trending_kline):
        from backtest import backtest_ma_cross
        report = backtest_ma_cross(trending_kline, init_capital=30000)
        assert report["max_drawdown_pct"] >= 0

    def test_small_capital(self):
        """资金不足100股时不应交易"""
        from backtest import backtest_ma_cross
        data = [
            {"date": f"2025-01-{i+1:02d}", "open": 500 + i, "close": 500 + i,
             "high": 501 + i, "low": 499 + i, "volume": 100000, "amount": 50000000}
            for i in range(30)
        ]
        report = backtest_ma_cross(data, init_capital=100)  # 100元买不起100股
        assert report["total_trades"] == 0


# ═══════════════════════════════════════
# Tests: backtest_breakout
# ═══════════════════════════════════════

class TestBacktestBreakout:
    def test_returns_valid_report(self, trending_kline):
        from backtest import backtest_breakout
        report = backtest_breakout(trending_kline, init_capital=30000)
        assert report["ok"] is True
        assert report["strategy"] == "突破策略(20日最高价/20日MA)"

    def test_buy_count_matches_sell_count_or_one_more(self, trending_kline):
        from backtest import backtest_breakout
        report = backtest_breakout(trending_kline, init_capital=30000)
        buy_count = report["buy_count"]
        sell_count = report["sell_count"]
        # 买入次数 >= 卖出次数（最后可能还有未平仓）
        assert buy_count >= sell_count
        assert buy_count - sell_count <= 1


# ═══════════════════════════════════════
# Tests: fetch_kline（网络层 mock）
# ═══════════════════════════════════════

class TestFetchKline:
    def test_fetch_returns_list_on_success(self):
        from backtest import fetch_kline
        mock_response = {
            "data": {
                "klines": [
                    "2025-01-02,10.0,10.5,10.6,9.9,100000,1050000",
                    "2025-01-03,10.5,10.8,10.9,10.4,120000,1296000",
                ]
            }
        }
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(mock_response).encode()
            mock_resp.__enter__ = lambda s: mock_resp
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            result = fetch_kline("600519", "2025-01-01", "2025-01-31")
            assert isinstance(result, list)
            assert len(result) == 2
            assert result[0]["date"] == "2025-01-02"
            assert result[0]["close"] == 10.5

    def test_fetch_returns_empty_on_network_error(self):
        from backtest import fetch_kline
        with patch("urllib.request.urlopen", side_effect=ConnectionError("timeout")):
            result = fetch_kline("600519", "2025-01-01", "2025-01-31")
            assert result == []

    def test_fetch_returns_empty_on_bad_json(self):
        from backtest import fetch_kline
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = b"invalid json"
            mock_resp.__enter__ = lambda s: mock_resp
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            result = fetch_kline("600519", "2025-01-01", "2025-01-31")
            assert result == []
