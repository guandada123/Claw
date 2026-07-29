"""test_prompt_builder.py — prompt_builder 核心函数测试。

覆盖：build_prompt（各任务类型）、_compact_stock_list、_fmt_market_cap。
"""

import prompt_builder as pb


def test_static_system_not_empty():
    """静态系统提示词不为空。"""
    assert len(pb.STATIC_SYSTEM) > 500
    assert "A股投资" in pb.STATIC_SYSTEM


def test_static_token_estimate_reasonable():
    assert pb.STATIC_TOKEN_ESTIMATE > 0
    assert pb.OVERHEAD_PER_CALL > 0


def test_build_prompt_stock_screen():
    result = pb.build_prompt("stock_screen", {
        "sector": "半导体",
        "criteria": "量价背离",
        "stocks": [
            {"code": "600123", "name": "测试A", "price": 10.0, "change_pct": 2.5, "volume_ratio": 1.2, "pe": 15},
        ],
    })
    assert result["has_cache"] is True
    assert result["estimated_tokens"] > 0
    assert result["task_type"] == "stock_screen"
    assert len(result["messages"]) == 2


def test_build_prompt_single_analysis():
    result = pb.build_prompt("single_analysis", {
        "code": "000001", "name": "平安银行", "price": 12.5,
        "change_pct": 2.1, "closes_5": "12,13,14", "ma5": 12.3, "ma20": 11.8,
        "volume": "10亿", "low_52w": 10, "high_52w": 15,
        "pe": 5.2, "pb": 0.6, "roe": 11.5, "market_cap": 24000000,
        "question": "是否入场？",
    })
    assert result["task_type"] == "single_analysis"
    assert "平安银行" in result["user_prompt"]


def test_build_prompt_generic_fallback():
    """未知任务类型回退到 generic builder"""
    result = pb.build_prompt("unknown_task", {"prompt": "帮我看下大盘"})
    assert result["task_type"] == "generic"
    assert "帮我看下大盘" in result["user_prompt"]


def test_build_prompt_market_summary():
    result = pb.build_prompt("market_summary", {"summary_200chars": "今日沪指上涨"})
    assert result["task_type"] == "market_summary"


def test_build_prompt_trend_analysis():
    result = pb.build_prompt("trend_analysis", {
        "code": "600000", "name": "浦发银行",
        "close": "10,11,12", "ma5": 11, "ma10": 10.5, "ma20": 10,
        "macd": "金叉", "rsi": 55, "volume": "5亿",
    })
    assert result["task_type"] == "trend_analysis"


def test_build_prompt_breakout_check():
    result = pb.build_prompt("breakout_check", {
        "code": "600000", "name": "浦发", "current": 13.0,
        "high_52w": 15.0, "low_52w": 10.0, "volume_ratio": 1.5, "change_pct": 3.0,
    })
    assert result["task_type"] == "breakout_check"


def test_build_prompt_backtest_summary():
    result = pb.build_prompt("backtest_summary", {
        "strategy": "COMBO", "period": "2026Q2",
        "total_return": 15.5, "max_drawdown": 8.2, "win_rate": 62.0,
        "sharpe_ratio": 1.8, "trade_count": 45, "avg_holding_days": 5.2,
    })
    assert result["task_type"] == "backtest_summary"


def test_compact_stock_list_empty():
    result = pb._compact_stock_list([])
    assert "空列表" in result


def test_compact_stock_list_normal():
    stocks = [
        {"code": "600001", "name": "测试", "price": 10.5, "change_pct": 3.2, "volume_ratio": 1.2, "pe": 15},
    ]
    result = pb._compact_stock_list(stocks)
    assert "600001" in result
    assert "测试" in result


def test_fmt_market_cap_yi():
    assert "亿" in pb._fmt_market_cap(1_000_000)


def test_fmt_market_cap_wan():
    assert "万" in pb._fmt_market_cap(50000)
