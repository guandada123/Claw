"""
Claw 测试 — 共享 fixtures
"""

import sys
import tempfile
from pathlib import Path

import pytest

# 确保 lib 模块可导入
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))


@pytest.fixture
def temp_dir():
    """创建临时目录，测试结束后自动清理。"""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def sample_portfolio():
    """模拟持仓数据。"""
    return {
        "config": {
            "initial_capital": 30000.0,
            "created_at": "2025-01-01",
            "updated_at": "2025-01-15 10:00:00",
        },
        "cash": 25000.0,
        "positions": {
            "000001.SZ": {
                "shares": 500,
                "cost": 12.50,
                "buy_date": "2025-01-05",
                "take_profit_level": 1,
                "highest_price": 13.80,
            },
            "600519.SH": {
                "shares": 100,
                "cost": 1450.00,
                "buy_date": "2025-01-10",
                "take_profit_level": 0,
                "highest_price": 1500.00,
            },
        },
        "transactions": [
            {
                "date": "2025-01-05",
                "code": "000001.SZ",
                "type": "BUY",
                "price": 12.50,
                "shares": 500,
                "amount": 6250.00,
            }
        ],
        "daily_snapshot": {"2025-01-05": {"value": 30000.0}},
        "dividends": [],
    }


@pytest.fixture
def sample_stock_data():
    """模拟日线行情数据。"""
    import datetime

    base = datetime.date(2025, 1, 2)
    data = []
    for i in range(50):
        d = base + datetime.timedelta(days=i)
        if d.weekday() >= 5:  # 跳过周末
            continue
        price = 12.0 + i * 0.1
        data.append(
            {
                "trade_date": d.strftime("%Y%m%d"),
                "open": price - 0.05,
                "high": price + 0.10,
                "low": price - 0.10,
                "close": price,
                "vol": 100000 + i * 1000,
                "amount": (100000 + i * 1000) * price,
                "pre_close": price - 0.10 if i > 0 else price,
            }
        )
        if len(data) >= 30:
            break
    return data


@pytest.fixture
def atomic_writer(temp_dir):
    """创建 AtomicJSONWriter 实例（临时目录）。"""
    from error_handler import AtomicJSONWriter

    return AtomicJSONWriter(
        filepath=temp_dir / "portfolio.json",
        backup_dir=temp_dir / "backups",
    )
