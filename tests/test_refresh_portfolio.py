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


# ══════════════════════════════════════════════════════
# update_portfolio — 2026-10-11 审计整改配套
#   · 成本字段回退（实盘 holdings 用 avg_cost，无 cost_price）
#   · 「价格刷新」与「结构确认」分离（updated 语义）
# ══════════════════════════════════════════════════════


def _live_portfolio():
    return {
        "updated": "2026-09-14",
        "holdings": [
            {
                "code": "600584",
                "name": "长电科技",
                "shares": 200,
                "avg_cost": 94.656,  # 注意：无 cost_price 键（实盘快照真实结构）
                "current_price": 66.33,
                "market_value": 13266.0,
                "floating_pnl": -5677.45,
                "pnl": -5677.45,
            }
        ],
        "summary": {"total_assets": 14766.0, "cash_available": 1500.0, "floating_pnl": -5677.45},
    }


def test_portfolio_path_not_doubled():
    """回归：路径不得拼成 `.workbuddy/.workbuddy/...`（原实现导致文件恒定不存在）。"""
    s = str(rp.PORTFOLIO_PATH)
    assert ".workbuddy/.workbuddy" not in s
    assert s.endswith(".workbuddy/data/user/portfolio.json")


def test_update_uses_avg_cost_fallback(tmp_path, monkeypatch):
    """回归：实盘持仓只有 avg_cost 时，pnl 必须按成本算出**负数**（原实现用 cost_price→0，得正数）。"""
    monkeypatch.setattr(rp, "PORTFOLIO_PATH", tmp_path / "p.json")
    data = _live_portfolio()

    rp.update_portfolio(data, {"600584": {"price": 62.74, "name": "长电科技"}}, dry_run=True)

    h = data["holdings"][0]
    assert h["pnl"] == round((62.74 - 94.656) * 200, 2)
    assert h["pnl"] < 0, "成本回退失效 → pnl 会变成正数"
    assert h["pnl_pct"] == round((62.74 / 94.656 - 1) * 100, 2)
    assert h["floating_pnl"] == h["pnl"]
    assert data["summary"]["floating_pnl"] == h["pnl"]


def test_update_does_not_stamp_updated_by_default(tmp_path, monkeypatch):
    """价格刷新不得改动 `updated`（结构 as-of），只写 prices_refreshed_at。"""
    monkeypatch.setattr(rp, "PORTFOLIO_PATH", tmp_path / "p.json")
    data = _live_portfolio()

    rp.update_portfolio(data, {"600584": {"price": 62.74, "name": "长电科技"}}, dry_run=True)

    assert data["updated"] == "2026-09-14", "价格刷新不应把结构日期改成今天（假新鲜）"
    assert data["prices_refreshed_at"].startswith(str(__import__("datetime").date.today().year))


def test_update_confirm_structure_stamps_updated(tmp_path, monkeypatch):
    """人工确认结构后才盖章 updated=今天，并留 structure_basis。"""
    monkeypatch.setattr(rp, "PORTFOLIO_PATH", tmp_path / "p.json")
    data = _live_portfolio()

    rp.update_portfolio(
        data,
        {"600584": {"price": 62.74, "name": "长电科技"}},
        dry_run=True,
        confirm_structure=True,
    )

    from datetime import date

    assert data["updated"] == date.today().isoformat()
    assert "structure_basis" in data


def test_update_skips_holding_without_any_cost_field(tmp_path, monkeypatch):
    """缺成本价（avg_cost/cost_price 均无）→ 跳过该只盈亏，不写入错误数字。"""
    monkeypatch.setattr(rp, "PORTFOLIO_PATH", tmp_path / "p.json")
    data = _live_portfolio()
    data["holdings"].append({"code": "002185", "shares": 500, "current_price": 16.0})

    rp.update_portfolio(
        data,
        {"600584": {"price": 62.74}, "002185": {"price": 15.85}},
        dry_run=True,
    )

    skipped = data["holdings"][1]
    assert skipped.get("pnl") is None or "pnl" not in skipped or skipped.get("pnl") is None
    assert skipped.get("current_price") == 16.0, "缺成本的标的不得被写入估算值"


def test_update_saves_and_readback_passes(tmp_path, monkeypatch):
    """非 dry-run：落盘 + readback 自洽（市值+现金=总资产、汇总=明细合计）。"""
    monkeypatch.setattr(rp, "PORTFOLIO_PATH", tmp_path / "p.json")
    data = _live_portfolio()

    rp.update_portfolio(data, {"600584": {"price": 62.74, "name": "长电科技"}})

    saved = json.loads((tmp_path / "p.json").read_text(encoding="utf-8"))
    mv = saved["holdings"][0]["market_value"]
    assert abs(mv + saved["summary"]["cash_available"] - saved["summary"]["total_assets"]) < 0.5
