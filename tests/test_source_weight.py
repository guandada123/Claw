"""test_source_weight.py — source_weight 纯函数测试。"""

import json

import source_weight as sw


def test_win_rate_to_weight_high():
    assert sw._win_rate_to_weight(65.0, 10) == 1.0


def test_win_rate_to_weight_medium():
    assert sw._win_rate_to_weight(50.0, 5) == 0.8


def test_win_rate_to_weight_low():
    assert sw._win_rate_to_weight(25.0, 10) == 0.5


def test_win_rate_to_weight_very_low():
    assert sw._win_rate_to_weight(10.0, 10) == 0.3


def test_win_rate_to_weight_insufficient_samples():
    """信号数 < 3 → 默认保守权重 0.5"""
    assert sw._win_rate_to_weight(80.0, 2) == 0.5
    assert sw._win_rate_to_weight(80.0, 1) == 0.5


# ── compute_weights ──


def test_compute_weights_no_file(monkeypatch, tmp_path):
    monkeypatch.setattr(sw, "_VERIFY_REPORT", tmp_path / "nope.json")
    monkeypatch.setattr(sw, "_OUTPUT", tmp_path / "out.json")
    result = sw.compute_weights()
    assert "默认" in result["source"]
    assert (tmp_path / "out.json").exists()


def test_compute_weights_with_data(monkeypatch, tmp_path):
    verify = tmp_path / "verify.json"
    verify.write_text(json.dumps({
        "generated_at": "2026-07-19",
        "ranking": [
            {"account": "好运侠客", "win_rate": 65.0, "total": 20, "avg_return": 3.5},
            {"account": "垃圾号", "win_rate": 25.0, "total": 10, "avg_return": -5.0},
        ],
    }), encoding="utf-8")
    monkeypatch.setattr(sw, "_VERIFY_REPORT", verify)
    monkeypatch.setattr(sw, "_OUTPUT", tmp_path / "out.json")
    result = sw.compute_weights()
    assert result["verified_accounts"] == 2
    assert result["weights"]["好运侠客"] == 1.0
    assert len(result["details"]) == 2


def test_compute_weights_empty_ranking(monkeypatch, tmp_path):
    verify = tmp_path / "verify.json"
    verify.write_text(json.dumps({"ranking": []}), encoding="utf-8")
    monkeypatch.setattr(sw, "_VERIFY_REPORT", verify)
    monkeypatch.setattr(sw, "_OUTPUT", tmp_path / "out.json")
    result = sw.compute_weights()
    assert result["verified_accounts"] == 0
    assert "_default" in result["weights"]


def test_write_output_creates_parent(tmp_path, monkeypatch):
    monkeypatch.setattr(sw, "_OUTPUT", tmp_path / "sub" / "out.json")
    sw._write_output({"test": True})
    assert (tmp_path / "sub" / "out.json").exists()
