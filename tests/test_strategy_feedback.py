"""test_strategy_feedback.py — strategy_feedback 纯函数测试。"""

import pytest
import strategy_feedback as sf


def test_classify_trade_big_win():
    assert sf.classify_trade(15.0) == "big_win"


def test_classify_trade_win():
    assert sf.classify_trade(5.0) == "win"


def test_classify_trade_flat():
    assert sf.classify_trade(0.0) == "flat"
    assert sf.classify_trade(-2.0) == "flat"


def test_classify_trade_small_loss():
    assert sf.classify_trade(-5.0) == "small_loss"


def test_classify_trade_big_loss():
    assert sf.classify_trade(-10.0) == "big_loss"


def test_classify_trade_none():
    assert sf.classify_trade(None) == "unknown"


def test_load_json_nonexistent(tmp_path):
    p = tmp_path / "nope.json"
    assert sf.load_json(p) == {}


def test_save_json_and_load(tmp_path):
    data = {"test": True, "value": 42}
    p = tmp_path / "output.json"
    sf.save_json(p, data)
    assert p.exists()
    loaded = sf.load_json(p)
    assert loaded["test"] is True
    assert loaded["value"] == 42


def test_tally_patterns_empty():
    result = sf.tally_patterns([])
    assert result["patterns"] == []


def test_tally_patterns_with_trades():
    trades = [
        {"industry": "半导体", "classification": "win", "pnl_pct": 5.0},
        {"industry": "半导体", "classification": "big_loss", "pnl_pct": -10.0},
        {"industry": "消费", "classification": "flat", "pnl_pct": 0.0},
    ]
    result = sf.tally_patterns(trades)
    assert result["summary"]["total_trades"] == 3
    assert len(result["patterns"]) >= 1
    # 行业 2+ 条交易才生成 pattern
    semi = [p for p in result["patterns"] if p["industry"] == "半导体"]
    assert len(semi) == 1
    assert semi[0]["wins"] == 1
    assert semi[0]["losses"] == 1


def test_feedback_no_user_data(monkeypatch, tmp_path, capsys):
    """feedback 在无用户数据时输出错误。"""
    monkeypatch.setattr("strategy_feedback.USER_PORTFOLIO", tmp_path / "nope.json")
    monkeypatch.setattr("strategy_feedback.STRATEGY_LIB", tmp_path / "nope.json")
    monkeypatch.setattr("strategy_feedback.FEEDBACK_LOG", tmp_path / "nope.json")
    with pytest.raises(SystemExit):
        sf.feedback()


def test_feedback_no_closed_positions(tmp_path, monkeypatch, capsys):
    portfolio = tmp_path / "portfolio.json"
    portfolio.write_text('{"closed_positions": []}', encoding="utf-8")
    monkeypatch.setattr("strategy_feedback.USER_PORTFOLIO", portfolio)
    monkeypatch.setattr("strategy_feedback.STRATEGY_LIB", tmp_path / "empty.json")
    monkeypatch.setattr("strategy_feedback.FEEDBACK_LOG", tmp_path / "empty.json")

    sf.feedback()
    captured = capsys.readouterr()
    assert "no_closed_positions" in captured.out


def test_feedback_with_trades(tmp_path, monkeypatch, capsys):
    """feedback 有 closed_positions 时处理交易并输出结果。"""
    import json
    portfolio = tmp_path / "portfolio.json"
    portfolio.write_text(json.dumps({
        "closed_positions": [
            {"code": "600000", "name": "平安银行", "pnl_pct": 5.0, "pnl": 100, "cost_price": 10.0,
             "exit_price": 10.5, "status": "已清仓", "closed_date": "2026-07-18"},
        ]
    }), encoding="utf-8")
    monkeypatch.setattr("strategy_feedback.USER_PORTFOLIO", portfolio)
    strategy_lib = tmp_path / "strategy_library.json"
    strategy_lib.write_text(json.dumps({"version": "1.0"}), encoding="utf-8")
    monkeypatch.setattr("strategy_feedback.STRATEGY_LIB", strategy_lib)
    feedback_log = tmp_path / "feedback_log.json"
    feedback_log.write_text('{"trades": []}', encoding="utf-8")
    monkeypatch.setattr("strategy_feedback.FEEDBACK_LOG", feedback_log)

    sf.feedback()
    assert feedback_log.exists()
    log = json.loads(feedback_log.read_text())
    assert len(log["trades"]) == 1
    assert log["trades"][0]["code"] == "600000"
