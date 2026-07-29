"""test_merge_signal_ranking.py — merge_signal_ranking 测试。"""

import json

import merge_signal_ranking as msr


def test_merge_no_files(monkeypatch, tmp_path):
    """没有数据文件时返回空结构。"""
    monkeypatch.setattr(msr, "_VERIFY_REPORT", tmp_path / "nonexistent.json")
    monkeypatch.setattr(msr, "_DISCOVERED", tmp_path / "nonexistent.json")
    monkeypatch.setattr(msr, "_OUTPUT", tmp_path / "output.json")
    result = msr.merge()
    assert result["deduped"] == 0
    assert result["rss_count"] == 0
    assert result["discovered_count"] == 0


def test_merge_with_rss_data(monkeypatch, tmp_path):
    verify = tmp_path / "signal_verify_report.json"
    verify.write_text(json.dumps({
        "ranking": [
            {"account": "好运侠客", "total": 20, "verified": 15, "win_rate": 65.0, "avg_return": 3.5},
        ]
    }), encoding="utf-8")
    discovered = tmp_path / "discovered_accounts.json"
    discovered.write_text('{}', encoding="utf-8")
    output = tmp_path / "output.json"

    monkeypatch.setattr(msr, "_VERIFY_REPORT", verify)
    monkeypatch.setattr(msr, "_DISCOVERED", discovered)
    monkeypatch.setattr(msr, "_OUTPUT", output)
    result = msr.merge()
    assert result["rss_count"] == 1
    assert result["ranking"][0]["name"] == "好运侠客"


def test_merge_dedup(monkeypatch, tmp_path):
    """RSS 已有的名称在 discovered 中被去重。"""
    verify = tmp_path / "signal_verify_report.json"
    verify.write_text(json.dumps({
        "ranking": [{"account": "好运侠客", "total": 10, "verified": 5, "win_rate": 60.0}]
    }), encoding="utf-8")
    discovered = tmp_path / "discovered_accounts.json"
    discovered.write_text(json.dumps({
        "candidates": [{"name": "好运侠客", "hit_rate": 0.5, "stocks_verified": 3}]
    }), encoding="utf-8")
    output = tmp_path / "output.json"

    monkeypatch.setattr(msr, "_VERIFY_REPORT", verify)
    monkeypatch.setattr(msr, "_DISCOVERED", discovered)
    monkeypatch.setattr(msr, "_OUTPUT", output)
    result = msr.merge()
    # "好运侠客" 在 RSS 中已有 → discovered 中被去重
    assert result["deduped"] == 1
    assert result["discovered_count"] == 0
