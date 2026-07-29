"""test_refresh_portfolio.py — refresh_portfolio 测试。"""

import json

import refresh_portfolio as rp


def test_load_portfolio_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr(rp, "PORTFOLIO_PATH", tmp_path / "nope.json")
    import pytest
    with pytest.raises(SystemExit):
        rp.load_portfolio()


def test_save_portfolio_creates_file(tmp_path, monkeypatch):
    p = tmp_path / "portfolio.json"
    monkeypatch.setattr(rp, "PORTFOLIO_PATH", p)
    rp.save_portfolio({"holdings": []})
    assert p.exists()
    data = json.loads(p.read_text())
    assert data["holdings"] == []


def test_load_portfolio_returns_dict(tmp_path, monkeypatch):
    p = tmp_path / "portfolio.json"
    p.write_text('{"holdings": [{"code": "600000"}]}', encoding="utf-8")
    monkeypatch.setattr(rp, "PORTFOLIO_PATH", p)
    data = rp.load_portfolio()
    assert data["holdings"][0]["code"] == "600000"
