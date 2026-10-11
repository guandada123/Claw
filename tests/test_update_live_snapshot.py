"""update_live_snapshot 的守卫测试（2026-10-11 审计 P0-2 配套）。

钉住三条铁律：
  · **不猜数据**：缺参数/非法参数一律 fail-closed（rc=1），不写盘；
  · **清仓需显式**：`--clear` 与 `--position` 互斥，缺一不猜「是不是清仓了」；
  · **写盘须回读**：落盘后 summary 与明细必须自洽（市值+现金=总资产）。

全部离线：行情源被替身，PORTFOLIO_PATH 指向 tmp。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / ".workbuddy" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import refresh_portfolio as rp  # noqa: E402
import update_live_snapshot as uls  # noqa: E402


def _seed(tmp_path, monkeypatch):
    """准备一份最小实盘快照并指到 tmp。"""
    p = tmp_path / "portfolio.json"
    p.write_text(
        json.dumps(
            {
                "broker": "GJ",
                "account_type": "REAL",
                "created": "2026-08-01",
                "updated": "2026-09-14",
                "holdings": [
                    {
                        "code": "600584",
                        "name": "长电科技",
                        "shares": 200,
                        "avg_cost": 94.656,
                        "current_price": 62.74,
                        "market_value": 12548.0,
                        "bought_date": "2026-07-15",
                    }
                ],
                "summary": {
                    "total_assets": 14048.0,
                    "cash_available": 1500.0,
                    "floating_pnl": -6383.2,
                },
                "rules": {"stop_loss": -0.08, "take_profit": None},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(rp, "PORTFOLIO_PATH", p)
    monkeypatch.setattr(rp, "get_quotes_via_wind", lambda codes: {})
    monkeypatch.setattr(
        rp,
        "get_quotes_via_tencent",
        lambda codes: {c: {"price": 62.74, "name": "长电科技"} for c in codes},
    )
    return p


# ── parse_position ────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "ok"),
    [
        ("600584:200:94.656", True),
        ("002185:500:22.829", True),
        ("60058:200:94.6", False),  # 代码不足 6 位
        ("600584:0:94.6", False),  # 股数为 0
        ("600584:200:0", False),  # 成本为 0
        ("600584:abc:1", False),  # 股数非数字
        ("600584:200", False),  # 缺字段
    ],
)
def test_parse_position_validation(raw, ok):
    if ok:
        assert uls.parse_position(raw)["code"] == raw.split(":")[0]
    else:
        with pytest.raises(ValueError):
            uls.parse_position(raw)


def test_parse_position_keeps_odd_lot_as_is():
    """非整手不擅自改写（真实账户可能有零股）——由 build_holdings 出告警。"""
    parsed = uls.parse_position("600584:150:10.0")
    assert parsed["shares"] == 150


# ── build_holdings ────────────────────────────────────


def test_build_holdings_preserves_aux_fields_and_warns_on_odd_lot():
    old = [{"code": "600584", "name": "长电科技", "bought_date": "2026-07-15", "broker": "GJ"}]
    holdings, warns = uls.build_holdings([{"code": "600584", "shares": 150, "avg_cost": 10.0}], old)
    assert holdings[0]["name"] == "长电科技"
    assert holdings[0]["bought_date"] == "2026-07-15"
    assert any("非 100 整数倍" in w for w in warns)


# ── main：fail-closed 与清仓语义 ──────────────────────


def _run(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["update_live_snapshot.py", *argv])
    return uls.main()


def test_missing_args_fail_closed(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    assert _run(monkeypatch, ["--cash", "100", "--source", "x"]) == 1  # 无持仓也未声明清仓
    assert (
        _run(
            monkeypatch, ["--clear", "--position", "600584:200:10", "--cash", "1", "--source", "x"]
        )
        == 1
    )
    assert _run(monkeypatch, ["--position", "600584:200:10", "--cash", "-1", "--source", "x"]) == 1


def test_full_update_writes_and_readback_passes(tmp_path, monkeypatch):
    p = _seed(tmp_path, monkeypatch)
    rc = _run(
        monkeypatch,
        ["--position", "600584:200:94.656", "--cash", "1500", "--source", "单测"],
    )
    assert rc == 0
    saved = json.loads(p.read_text(encoding="utf-8"))
    assert saved["updated"] != "2026-09-14"  # 结构确认后盖章
    assert saved["structure_basis"].startswith("人工确认录入")
    mv = saved["holdings"][0]["market_value"]
    assert abs(mv + saved["summary"]["cash_available"] - saved["summary"]["total_assets"]) < 0.5


def test_clear_sets_total_equals_cash(tmp_path, monkeypatch):
    p = _seed(tmp_path, monkeypatch)
    rc = _run(monkeypatch, ["--clear", "--cash", "14048.0", "--source", "单测清仓"])
    assert rc == 0
    saved = json.loads(p.read_text(encoding="utf-8"))
    assert saved["holdings"] == []
    assert saved["summary"]["total_assets"] == 14048.0
    assert saved["summary"]["floating_pnl"] == 0.0


def test_full_update_recomputes_total_from_cash(tmp_path, monkeypatch):
    """回归：结构重写会改现金 → 总资产必须 = 现金 + 市值。

    不能沿用 `update_portfolio` 的「旧总资产 ± 市值变化」守恒式（其前提是现金不变）。
    实犯：录入真实快照时该式给出 55,816.72，而正确值是 45,094.98。
    """
    p = _seed(tmp_path, monkeypatch)
    rc = _run(
        monkeypatch,
        ["--position", "600584:200:94.656", "--cash", "9999.0", "--source", "单测"],
    )
    assert rc == 0
    saved = json.loads(p.read_text(encoding="utf-8"))
    mv = saved["holdings"][0]["market_value"]  # 替身行情 62.74 × 200 = 12548
    assert saved["summary"]["total_market_value"] == mv
    assert saved["summary"]["total_assets"] == round(9999.0 + mv, 2)
    assert abs(saved["summary"]["position_pct"] - round(mv / (9999.0 + mv) * 100, 1)) < 0.05
