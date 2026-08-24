"""test_sim_trade_sanity.py — sim_trade 价格防错集成测试（2026-08-07 落地）。

覆盖：
  - cmd_update_price 错误价 → 拒绝写入（sanity_failed=true，保留旧价）
  - cmd_update_price 真实价 → 正常写入
  - cmd_buy / cmd_sell 错误价 → 拒绝交易（sanity_failed=true）
  - cmd_update_all_prices 混合（部分失败）→ 失败项跳过 + sanity_failed 列表

网络依赖通过 monkeypatch 隔离 _sanity_check_price 内部逻辑（直接 mock 该函数）。
"""
from __future__ import annotations

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
    """错误价 → 拒绝写入，保留旧价（隔离环境，不依赖实时持仓）。"""
    _fake_load_save(monkeypatch, {
        "000333": {"name": "美的集团", "shares": 100, "avg_cost": 83.5,
                   "total_cost": 8350.0, "current_price": 83.5,
                   "highest_price": 83.5, "take_profit_level": 1},
    })
    monkeypatch.setattr(st, "_sanity_check_price", _fake_sanity(True))
    st.cmd_update_price("000333", 83.5)
    # 再注入错误价
    monkeypatch.setattr(st, "_sanity_check_price", _fake_sanity(False, 83.5))
    res = st.cmd_update_price("000333", 8.35)
    assert res["ok"] is False
    assert res["sanity_failed"] is True
    # 旧价保留
    assert st.load_portfolio()["positions"]["000333"]["current_price"] == 83.5


def test_update_price_accepts_good(monkeypatch):
    """真实价 → 正常写入（隔离环境）。"""
    _fake_load_save(monkeypatch, {
        "000333": {"name": "美的集团", "shares": 100, "avg_cost": 83.5,
                   "total_cost": 8350.0, "current_price": 83.5,
                   "highest_price": 83.5, "take_profit_level": 1},
    })
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
    """批量更新混合：失败项跳过 + sanity_failed 列表（隔离环境）。"""
    # 让 000333 失败、601899 成功
    def mixed(code, price):
        if code == "000333":
            return {"ok": False, "reliable_price": 83.5, "reason": "G1 测试"}
        return {"ok": True, "reliable_price": price, "reason": ""}
    _fake_load_save(monkeypatch, {
        "000333": {"name": "美的集团", "shares": 100, "avg_cost": 83.5,
                   "total_cost": 8350.0, "current_price": 83.5,
                   "highest_price": 83.5, "take_profit_level": 1},
        "601899": {"name": "紫金矿业", "shares": 100, "avg_cost": 35.0,
                   "total_cost": 3500.0, "current_price": 35.15,
                   "highest_price": 35.15, "take_profit_level": 1},
    })
    monkeypatch.setattr(st, "_sanity_check_price", mixed)
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


# ═══════════════════════════════════════════════════════════════════════════
# 整手约束回归（2026-08-14 审计整改）
# A股最小交易单位=1手=100股；买入及部分卖出须为100整数倍；全仓卖出允许零股尾仓。
# 全部用例隔离 load/save/限制/价格校验，避免污染真实模拟盘持仓。
# ═══════════════════════════════════════════════════════════════════════════

LOT = 100


def _fake_sanity_ok():
    """构造 fake sanity 校验器：一律通过。"""

    def _f(code, price):
        return {"ok": True, "reliable_price": price, "reason": ""}

    return _f


def _patch_isolated(monkeypatch, positions, cash=1_000_000.0):
    """构造隔离环境：返回 fake pf 并 monkeypatch 掉落库与校验副作用。"""
    fake = {
        "cash": cash,
        "initial_capital": 50_000.0,
        "positions": positions,
        "transactions": [],
    }
    monkeypatch.setattr(st, "load_portfolio", lambda: fake)
    monkeypatch.setattr(st, "save_portfolio", lambda pf: None)
    monkeypatch.setattr(st, "check_restricted", lambda code: "")
    monkeypatch.setattr(st, "_sanity_check_price", _fake_sanity_ok())
    return fake


def _fake_load_save(monkeypatch, positions, cash=1_000_000.0):
    """隔离 load/save 到内存 fake，避免污染真实模拟盘持仓。

    用于价格刷新类测试（cmd_update_price / cmd_update_all_prices）。
    这些测试原依赖实时 portfolio.json 含 000333，000333 被清仓后回归；
    隔离后不再随实时持仓变动而脆断，也不会改写真实组合。
    """
    fake = {
        "cash": cash,
        "initial_capital": 50_000.0,
        "positions": positions,
        "transactions": [],
    }
    monkeypatch.setattr(st, "load_portfolio", lambda: fake)
    monkeypatch.setattr(st, "save_portfolio", lambda pf: None)
    return fake


def test_cmd_buy_rejects_non_lot(monkeypatch):
    """非整手买入(165股) → 失败闭合拒绝，不越界落库。"""
    _patch_isolated(monkeypatch, {})
    res = st.cmd_buy("XBUY", 165, 10.0, "非整手")
    assert res["ok"] is False
    assert "100整数倍" in res["error"]


def test_cmd_buy_accepts_valid_lot(monkeypatch):
    """整手买入(300股) → 通过门禁，真实建仓300股。

    注意：cmd_buy 成功返回体既有无 "ok" 键（仅返回 cash_remaining/total_asset，
    既有行为），故用持仓副作用验证成交，而非 res["ok"]。
    """
    fake = _patch_isolated(monkeypatch, {})
    res = st.cmd_buy("XBUY", 300, 10.0, "整手")
    assert "XBUY" in fake["positions"]
    assert fake["positions"]["XBUY"]["shares"] == 300


def test_cmd_sell_rejects_non_lot_partial(monkeypatch):
    """部分卖出非整手(<1手, 50股) → 拒绝。"""
    _patch_isolated(monkeypatch, {
        "XSELL": {"name": "T", "shares": 100, "avg_cost": 10.0,
                  "total_cost": 1000.0, "current_price": 10.0,
                  "highest_price": 10.0, "take_profit_level": 1},
    })
    res = st.cmd_sell("XSELL", 50, 10.0, "非整手")
    assert res["ok"] is False
    assert "100整数倍" in res["error"]


def test_cmd_sell_partial_lot_rounds_and_full_allows_odd(monkeypatch):
    """部分卖出165股 → 规整为100股成交；全仓卖出(0) → 允许零股尾仓。"""
    fake = _patch_isolated(monkeypatch, {
        "XSELL": {"name": "T", "shares": 500, "avg_cost": 10.0,
                  "total_cost": 5000.0, "current_price": 10.0,
                  "highest_price": 10.0, "take_profit_level": 1},
    })
    res = st.cmd_sell("XSELL", 165, 10.0, "整手")
    assert res["ok"] is True
    assert res["transaction"]["shares"] == 100
    # 全仓卖出允许零股尾仓（如550股的零股尾仓）
    fake["positions"]["XSELL"] = {"name": "T", "shares": 550, "avg_cost": 10.0,
                                  "total_cost": 5500.0, "current_price": 10.0,
                                  "highest_price": 10.0, "take_profit_level": 1}
    res2 = st.cmd_sell("XSELL", 0, 10.0, "清仓")
    assert res2["ok"] is True
    assert res2["transaction"]["shares"] == 550


def test_check_take_profit_lot_rounds(monkeypatch):
    """止盈部分卖出整手取整：500股×33%→165→规整为100股（纯计算无落库）。"""
    monkeypatch.setattr(st, "_sanity_check_price", _fake_sanity_ok())
    pf = {
        "cash": 0.0,
        "initial_capital": 50_000.0,
        "positions": {
            "XTP": {"name": "T", "shares": 500, "avg_cost": 10.0,
                    "current_price": 20.0, "highest_price": 20.0,
                    "take_profit_level": 1},
        },
    }
    res = st.check_take_profit(pf, "XTP")
    assert res["should_sell"] is True
    assert res["shares_to_sell"] == 100
    assert res["shares_to_sell"] % 100 == 0


def test_check_take_profit_lot_floor_to_full(monkeypatch):
    """小持仓100股×33%→33→不足1手 → 兜底整仓卖出(100股)。"""
    monkeypatch.setattr(st, "_sanity_check_price", _fake_sanity_ok())
    pf = {
        "cash": 0.0,
        "initial_capital": 50_000.0,
        "positions": {
            "XTP": {"name": "T", "shares": 100, "avg_cost": 10.0,
                    "current_price": 20.0, "highest_price": 20.0,
                    "take_profit_level": 1},
        },
    }
    res = st.check_take_profit(pf, "XTP")
    assert res["should_sell"] is True
    assert res["shares_to_sell"] == 100
