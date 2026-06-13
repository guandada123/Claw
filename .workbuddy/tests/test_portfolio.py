"""
持仓数据完整性测试

验证 portfolio.json 的核心约束：
    1. 写入后完整读取不丢字段
    2. 金额计算正确
    3. 交易记录格式一致
"""
import json
import sys
from pathlib import Path

import pytest

# 加脚本目录到 path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))


class TestPortfolioIntegrity:
    """模拟持仓数据验证。"""

    def test_load_portfolio_returns_valid_structure(self, sample_portfolio):
        """加载的持仓必须有完整的必需字段。"""
        required_top = ["config", "cash", "positions", "transactions", "daily_snapshot"]
        for key in required_top:
            assert key in sample_portfolio, f"缺少顶层字段: {key}"

    def test_cash_is_positive(self, sample_portfolio):
        """现金余额必须非负。"""
        assert sample_portfolio["cash"] >= 0

    def test_transaction_format(self, sample_portfolio):
        """每笔交易记录必须包含必要字段。"""
        for txn in sample_portfolio["transactions"]:
            required = ["date", "code", "type", "price", "shares", "amount"]
            for key in required:
                assert key in txn, f"交易记录缺少字段: {key}"
            assert txn["type"] in ("BUY", "SELL")
            assert txn["shares"] > 0
            assert txn["amount"] > 0

    def test_position_has_cost_and_shares(self, sample_portfolio):
        """每个持仓必须有成本价和股数。"""
        for code, pos in sample_portfolio["positions"].items():
            assert "shares" in pos
            assert "cost" in pos
            assert pos["shares"] > 0
            assert pos["cost"] > 0

    def test_roundtrip_via_atomic_writer(self, temp_dir, sample_portfolio):
        """通过原子写入后的持仓数据往返完整。"""
        from error_handler import atomic_write_json, atomic_read_json
        f = temp_dir / "pf.json"
        atomic_write_json(f, sample_portfolio)
        result = atomic_read_json(f)
        assert result == sample_portfolio


class TestRealPortfolioLoading:
    """真实 portfolio.json 加载测试。"""

    def test_sim_portfolio_loadable(self):
        """模拟持仓文件可以被正确加载。"""
        import sim_trade
        pf = sim_trade.load_portfolio()
        assert "cash" in pf
        assert "positions" in pf
        assert isinstance(pf["cash"], (int, float))
        assert isinstance(pf["positions"], dict)

    def test_sim_portfolio_save_load_roundtrip(self, temp_dir):
        """写入后立即可读且数据一致。"""
        import sim_trade
        from error_handler import atomic_write_json, atomic_read_json

        pf = sim_trade.load_portfolio()
        backup = temp_dir / "pf_backup.json"
        atomic_write_json(backup, pf)
        reloaded = atomic_read_json(backup)

        assert reloaded["cash"] == pf["cash"]
        assert len(reloaded["positions"]) == len(pf["positions"])
