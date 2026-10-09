"""refresh_sim_close 与「价格证据字段」的回归守卫。

守住 2026-10-09 取证到的两个缺陷：
  ① 存盘估值静默冻结：能刷新价格的命令（update / batch-update / auto-check）
     与落快照的 snapshot 无任何 ACTIVE 自动化调用 → 持仓价停在旧值，
     daily_snapshot 连续 5 个交易日数值完全相同。
     → 新增 refresh_sim_close.py 把「取价 → 刷新 → 落快照」收敛成单命令，并接回 15:10 自动化。
  ② 证据字段没跟着更新：cmd_update_all_prices 只改 current_price，
     不碰 updated / price_source → 价格是新的、字段写着旧日期（实测价已到 41.42，
     updated 仍写 2026-08-28），读文件的人据此误判「价格陈旧」。
     → 新增 _stamp_price_source()，两处 update 路径都调用。

全部离线：不取网、不碰真实 portfolio.json。
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
WS_SCRIPTS = Path(__file__).resolve().parent.parent / ".workbuddy" / "scripts"
for _p in (SCRIPTS, WS_SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import refresh_sim_close as rsc  # noqa: E402
import sim_trade as st  # noqa: E402

POS = {
    "600036": {
        "name": "招商银行",
        "shares": 200,
        "avg_cost": 38.935,
        "total_cost": 7787.0,
        "current_price": 40.88,
        "highest_price": 40.88,
        "take_profit_level": 1,
        "updated": "腾讯qt.gtimg.cn·收盘2026-08-28 15:55",
        "price_source": "腾讯qt.gtimg.cn·收盘2026-08-28 15:55",
    }
}


# ─────────────────────────── ② 证据字段 ───────────────────────────


def test_stamp_price_source_sets_both_fields():
    pos = dict(POS["600036"])
    st._stamp_price_source(pos, "tencent·收盘")
    assert pos["updated"].startswith("tencent·收盘·")
    assert pos["price_source"] == pos["updated"]


def test_stamp_price_source_falls_back_without_source():
    pos = dict(POS["600036"])
    st._stamp_price_source(pos, "")
    assert pos["updated"].startswith("batch-update·")
    assert pos["price_source"] == pos["updated"]


def _fake_load_save(monkeypatch, positions):
    fake = {"cash": 44555.18, "initial_capital": 50000.0, "positions": positions, "transactions": []}
    monkeypatch.setattr(st, "load_portfolio", lambda: fake)
    monkeypatch.setattr(st, "save_portfolio", lambda _pf: None)
    return fake


def test_update_all_prices_stamps_source(monkeypatch):
    """核心回归点：刷新价格必须同时刷新证据字段。"""
    fake = _fake_load_save(monkeypatch, {"600036": dict(POS["600036"])})
    monkeypatch.setattr(st, "_sanity_check_price", lambda c, p: {"ok": True, "reliable_price": p, "reason": ""})
    st.cmd_update_all_prices({"600036": 41.42}, "tencent·收盘")
    pos = fake["positions"]["600036"]
    assert pos["current_price"] == 41.42
    assert pos["updated"].startswith("tencent·收盘·"), "证据字段必须与价格一起更新"
    assert "2026-08-28" not in pos["updated"]


def test_update_single_price_stamps_source(monkeypatch):
    fake = _fake_load_save(monkeypatch, {"600036": dict(POS["600036"])})
    monkeypatch.setattr(st, "_sanity_check_price", lambda c, p: {"ok": True, "reliable_price": p, "reason": ""})
    st.cmd_update_price("600036", 41.42, "tencent·收盘")
    assert fake["positions"]["600036"]["price_source"].startswith("tencent·收盘·")


def test_sanity_failure_does_not_touch_evidence_fields(monkeypatch):
    """被拒的价不得留下「刚刷新过」的假证据。"""
    fake = _fake_load_save(monkeypatch, {"600036": dict(POS["600036"])})
    monkeypatch.setattr(
        st, "_sanity_check_price", lambda c, p: {"ok": False, "reliable_price": None, "reason": "G1 偏差>30%"}
    )
    res = st.cmd_update_all_prices({"600036": 4.14}, "tencent·收盘")
    assert res["sanity_failed"]
    pos = fake["positions"]["600036"]
    assert pos["current_price"] == 40.88
    assert pos["updated"] == "腾讯qt.gtimg.cn·收盘2026-08-28 15:55", "拒绝写入时证据字段也不应变"


# ─────────────────────────── ① 驱动脚本 ───────────────────────────


class _FakeSim:
    def __init__(self, positions):
        self.positions = positions
        self.update_calls: list[tuple] = []
        self.snapshot_calls = 0
        self.saved = {"total_asset": 52839.18, "cash": 44555.18, "pnl_pct": 5.68}

    def load_portfolio(self):
        return {"positions": self.positions, "cash": 44555.18}

    def cmd_update_all_prices(self, prices, source=""):
        self.update_calls.append((dict(prices), source))
        return {"ok": True, "updated": list(prices), "sanity_failed": []}

    def cmd_snapshot(self):
        self.snapshot_calls += 1
        return self.saved

    def today_str(self):
        return "2026-10-09"


@pytest.fixture()
def env(monkeypatch, tmp_path):
    """把脚本的 portfolio 路径与 sim 模块都换成替身。"""
    importlib.reload(rsc)
    pf = tmp_path / "portfolio.json"
    pf.write_text(json.dumps({"positions": {"600036": {}}, "cash": 1.0}), encoding="utf-8")
    monkeypatch.setattr(rsc, "PORTFOLIO", pf)
    fake = _FakeSim({"600036": dict(POS["600036"])})
    monkeypatch.setattr(rsc, "_load_sim_module", lambda: fake)
    return fake


def _run(argv):
    old = sys.argv
    sys.argv = ["refresh_sim_close"] + argv
    try:
        return rsc.main()
    finally:
        sys.argv = old


def _out(capsys) -> dict:
    """脚本只往 stdout 打一段 JSON，直接解析即可。"""
    return json.loads(capsys.readouterr().out)


def test_no_positions_skips_without_fetch(env, monkeypatch, capsys):
    env.positions.clear()
    called = {"n": 0}

    def _boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("无持仓时不应取价")

    monkeypatch.setattr(rsc, "fetch_quotes", _boom)
    assert _run([]) == 0
    assert called["n"] == 0
    assert env.snapshot_calls == 0


def test_fetch_error_returns_1_and_writes_nothing(env, monkeypatch, capsys):
    monkeypatch.setattr(rsc, "fetch_quotes", lambda _py: ([], "", "fetch rc=1: boom"))
    assert _run([]) == 1
    assert env.update_calls == []
    assert env.snapshot_calls == 0
    assert "取价失败" in _out(capsys)["error"]


def test_empty_quotes_returns_1(env, monkeypatch, capsys):
    monkeypatch.setattr(rsc, "fetch_quotes", lambda _py: ([], "tencent", None))
    assert _run([]) == 1
    assert env.snapshot_calls == 0
    assert "空列表" in _out(capsys)["error"]


def test_all_sanity_failed_never_writes(env, monkeypatch, capsys):
    monkeypatch.setattr(
        rsc,
        "fetch_quotes",
        lambda _py: ([{"code": "600036", "current_price": 4.14, "price_sanity": {"ok": False, "action": "REJECT"}}], "tencent", None),
    )
    assert _run([]) == 1
    assert env.update_calls == [], "全部取价失败时不得写盘"
    assert env.snapshot_calls == 0


def test_dry_run_writes_nothing(env, monkeypatch, capsys):
    monkeypatch.setattr(
        rsc,
        "fetch_quotes",
        lambda _py: ([{"code": "600036", "current_price": 41.42, "price_sanity": {"ok": True, "verified_price": 41.42}}], "tencent", None),
    )
    assert _run(["--dry-run"]) == 0
    payload = _out(capsys)
    assert payload["dry_run"] is True
    assert payload["would_update"] == {"600036": 41.42}
    assert env.update_calls == []
    assert env.snapshot_calls == 0


def test_happy_path_updates_and_snapshots_with_source(env, monkeypatch, capsys):
    monkeypatch.setattr(
        rsc,
        "fetch_quotes",
        lambda _py: ([{"code": "600036", "current_price": 41.42, "price_sanity": {"ok": True, "verified_price": 41.42}}], "tencent", None),
    )
    assert _run([]) == 0
    payload = _out(capsys)
    assert env.update_calls == [({"600036": 41.42}, "tencent·收盘")], "必须把取价源一起传下去"
    assert env.snapshot_calls == 1
    assert payload["snapshot"]["date"] == "2026-10-09"
    assert payload["deltas"][0]["changed"] is True


def test_newer_price_not_forwarded_when_sanity_reports_different_verified(env, monkeypatch, capsys):
    """verified_price 优先于 current_price（防错闸给了可信价就用它）。"""
    monkeypatch.setattr(
        rsc,
        "fetch_quotes",
        lambda _py: ([{"code": "600036", "current_price": 999.0, "price_sanity": {"ok": True, "verified_price": 41.42}}], "tencent", None),
    )
    assert _run(["--dry-run"]) == 0
    assert _out(capsys)["would_update"] == {"600036": 41.42}
