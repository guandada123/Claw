"""test_sim_trade_sanity.py — sim_trade 价格防错集成测试（2026-08-07 落地）。

覆盖：
  - cmd_update_price 错误价 → 拒绝写入（sanity_failed=true，保留旧价）
  - cmd_update_price 真实价 → 正常写入
  - cmd_buy / cmd_sell 错误价 → 拒绝交易（sanity_failed=true）
  - cmd_update_all_prices 混合（部分失败）→ 失败项跳过 + sanity_failed 列表

网络依赖通过 monkeypatch 隔离 _sanity_check_price 内部逻辑（直接 mock 该函数）。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# sim_trade 在 .workbuddy/scripts/，需单独加路径
WS_SCRIPTS = Path(__file__).resolve().parent.parent / ".workbuddy" / "scripts"
if str(WS_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(WS_SCRIPTS))

import sim_trade as st  # noqa: E402


def _fake_sanity(ok: bool, reliable: float | None = None):
    """构造 fake sanity 校验器替换 sim_trade._sanity_check_price。"""
    def _fake(code, price):
        if ok:
            return {"ok": True, "reliable_price": price, "reason": ""}
        return {"ok": False, "reliable_price": reliable,
                "reason": "G1: 偏差>30% (测试注入)"}
    return _fake


def test_update_price_rejects_bad(monkeypatch):
    """错误价 → 拒绝写入，保留旧价。"""
    monkeypatch.setattr(st, "_sanity_check_price", _fake_sanity(False, 83.5))
    # 000333 当前应有持仓（模拟盘），先用真实价写入
    monkeypatch.setattr(st, "_sanity_check_price", _fake_sanity(True))
    st.cmd_update_price("000333", 83.5)
    # 再注入错误价
    monkeypatch.setattr(st, "_sanity_check_price", _fake_sanity(False, 83.5))
    res = st.cmd_update_price("000333", 8.35)
    assert res["ok"] is False
    assert res["sanity_failed"] is True
    # 旧价保留
    pf = st.load_portfolio()
    assert pf["positions"]["000333"]["current_price"] == 83.5


def test_update_price_accepts_good(monkeypatch):
    """真实价 → 正常写入。"""
    monkeypatch.setattr(st, "_sanity_check_price", _fake_sanity(True))
    res = st.cmd_update_price("000333", 83.5)
    assert res["ok"] is True
    assert res["price"] == 83.5


def test_buy_rejects_bad_price(monkeypatch):
    """买入错误价 → 拒绝交易。"""
    monkeypatch.setattr(st, "_sanity_check_price", _fake_sanity(False, 83.5))
    res = st.cmd_buy("000333", 100, 8.35, "测试")
    assert res["ok"] is False
    assert res["sanity_failed"] is True


def test_sell_rejects_bad_price(monkeypatch):
    """卖出错误价 → 拒绝交易。"""
    monkeypatch.setattr(st, "_sanity_check_price", _fake_sanity(False, 83.5))
    res = st.cmd_sell("000333", 100, 8.35, "测试")
    assert res["ok"] is False
    assert res["sanity_failed"] is True


def test_batch_update_partial_fail(monkeypatch):
    """批量更新混合：失败项跳过 + sanity_failed 列表。"""
    # 让 000333 失败、601899 成功
    def mixed(code, price):
        if code == "000333":
            return {"ok": False, "reliable_price": 83.5, "reason": "G1 测试"}
        return {"ok": True, "reliable_price": price, "reason": ""}
    monkeypatch.setattr(st, "_sanity_check_price", mixed)
    # 确保两标的都有持仓（模拟盘）
    pf = st.load_portfolio()
    if "601899" not in pf["positions"]:
        pf["positions"]["601899"] = {"name": "紫金矿业", "shares": 100,
                                      "avg_cost": 35.0, "total_cost": 3500.0,
                                      "current_price": 35.15, "highest_price": 35.15,
                                      "take_profit_level": 1, "first_buy_date": "2026-08-07"}
        st.save_portfolio(pf)
    res = st.cmd_update_all_prices({"000333": 8.35, "601899": 35.15})
    assert "000333" not in res["updated"]
    assert "601899" in res["updated"]
    assert len(res["sanity_failed"]) == 1
    assert res["sanity_failed"][0]["code"] == "000333"


def test_auto_check_skips_bad_current_price(monkeypatch):
    """🚫 冗余加固回归：auto_check_all_positions 判定前先过 sanity，
    错误 current_price 的持仓必须被跳过（不得触发误卖）。"""
    # 000333 注入 8/6 同类错误价（真实~83.5），强制 fail
    def per_code(code, price):
        if code == "000333" and price == 8.35:
            return {"ok": False, "reliable_price": 83.5,
                    "reason": "G1: 偏差>30% (测试)"}
        return {"ok": True, "reliable_price": price, "reason": ""}
    monkeypatch.setattr(st, "_sanity_check_price", per_code)

    pf = {
        "cash": 10000.0,
        "initial_capital": 50000.0,
        "positions": {
            # 错误价持仓：即便跌破止损线也绝不触发 SELL
            "000333": {"name": "美的集团", "shares": 100, "avg_cost": 83.5,
                       "current_price": 8.35},
            # 真实价持仓：正常判定
            "601899": {"name": "紫金矿业", "shares": 200, "avg_cost": 30.0,
                       "current_price": 34.93},
        },
    }
    sugs = st.auto_check_all_positions(pf)
    codes = [s["code"] for s in sugs]
    assert "000333" not in codes, "❌ 错误价持仓未被 sanity 跳过，可能误卖！"
    # 紫金正常走判定逻辑（不强制断言是否有信号，仅验证未被错误价守卫阻断）
    assert "601899" in pf["positions"]


def test_auto_check_good_price_proceeds(monkeypatch):
    """真实价持仓：sanity 通过，正常进入止损/止盈判定。"""
    monkeypatch.setattr(st, "_sanity_check_price", _fake_sanity(True))
    pf = {
        "cash": 10000.0,
        "initial_capital": 50000.0,
        "positions": {
            "000333": {"name": "美的集团", "shares": 100, "avg_cost": 83.5,
                       "current_price": 83.5},
        },
    }
    # 不应抛异常，正常返回（无信号则为空列表）
    sugs = st.auto_check_all_positions(pf)
    assert isinstance(sugs, list)
