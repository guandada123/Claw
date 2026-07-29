"""test_sync_combo_signals.py — sync_combo_signals 测试。"""

import json

import sync_combo_signals as scs


def test_sync_combo_no_input(monkeypatch, tmp_path):
    monkeypatch.setattr(scs, "SIGNALS_INPUT", tmp_path / "nonexistent.json")
    monkeypatch.setattr(scs, "SIGNALS_FILE", tmp_path / "output.json")
    result = scs.sync_combo()
    assert result == 0


def test_sync_combo_with_data(monkeypatch, tmp_path):
    input_file = tmp_path / "live_signals_advisor_latest.json"
    input_file.write_text(json.dumps({
        "all": [
            {"code": "600000", "name": "平安银行", "action": "buy", "combo_score": 85, "adx": 30, "rsi": 55},
        ],
        "sell": [],
    }), encoding="utf-8")
    output_file = tmp_path / "article_signals.json"

    monkeypatch.setattr(scs, "SIGNALS_INPUT", input_file)
    monkeypatch.setattr(scs, "SIGNALS_FILE", output_file)
    result = scs.sync_combo()
    assert result == 1  # 1 new signal
    assert output_file.exists()
    data = json.loads(output_file.read_text())
    assert len(data) == 1
    assert data[0]["stock_code"] == "600000"
    assert data[0]["signal"] == "bullish"


def test_sync_combo_skip_missing_action(monkeypatch, tmp_path):
    input_file = tmp_path / "live_signals_advisor_latest.json"
    input_file.write_text(json.dumps({
        "all": [{"code": "600000", "name": "平安"}],  # no action field
    }), encoding="utf-8")

    monkeypatch.setattr(scs, "SIGNALS_INPUT", input_file)
    monkeypatch.setattr(scs, "SIGNALS_FILE", tmp_path / "out.json")
    result = scs.sync_combo()
    assert result == 0  # skipped


def test_sync_combo_dedup(monkeypatch, tmp_path):
    """写入两次相同信号，第二次应去重。"""
    input_file = tmp_path / "live_signals_advisor_latest.json"
    data = {"all": [{"code": "600000", "name": "平安", "action": "buy", "combo_score": 80}], "sell": []}
    output_file = tmp_path / "article_signals.json"

    monkeypatch.setattr(scs, "SIGNALS_INPUT", input_file)
    monkeypatch.setattr(scs, "SIGNALS_FILE", output_file)

    input_file.write_text(json.dumps(data), encoding="utf-8")
    first = scs.sync_combo()
    assert first == 1

    input_file.write_text(json.dumps(data), encoding="utf-8")
    second = scs.sync_combo()
    assert second == 0  # dedup


def test_sync_combo_with_non_list_format(monkeypatch, tmp_path):
    """兼容 dict-format 的 all 字段"""
    input_file = tmp_path / "live_signals_advisor_latest.json"
    input_file.write_text(json.dumps({
        "all": {},
        "buy": [{"code": "600000", "name": "平安", "action": "buy", "combo_score": 80}],
        "sell": [],
    }), encoding="utf-8")
    monkeypatch.setattr(scs, "SIGNALS_INPUT", input_file)
    monkeypatch.setattr(scs, "SIGNALS_FILE", tmp_path / "out.json")
    result = scs.sync_combo()
    assert result == 1


def test_sync_combo_sell_signal_bearish(monkeypatch, tmp_path):
    """sell 信号应转为 bearish"""
    input_file = tmp_path / "live_signals_advisor_latest.json"
    input_file.write_text(json.dumps({
        "all": [],
        "sell": [{"code": "600000", "name": "平安", "action": "sell", "combo_score": 70}],
    }), encoding="utf-8")
    monkeypatch.setattr(scs, "SIGNALS_INPUT", input_file)
    monkeypatch.setattr(scs, "SIGNALS_FILE", tmp_path / "out.json")
    result = scs.sync_combo()
    assert result == 1
