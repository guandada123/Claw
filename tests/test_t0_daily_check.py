"""test_t0_daily_check.py — 做T盯盘自检脚本(t0_daily_check)核心逻辑测试。

覆盖 2026-08-19 新增功能（无 pytest 覆盖缺口）：
  - market_gate 大盘闸（上证≤-1% 停手 / -1%~-0.5% 减半 / 正常 / 数据不可用放行）
  - _is_stop_loss_hit 实盘止损线（主板-8% / 创业板-15% / 无成本不判）
  - build_eval_row 三态分支（止损清仓优先 / 大盘停手 / 正常评估）
  - fmt_row 极简模板输出（清仓 / 停手 / 正T / 反T / 不动）
"""

import pytest
import t0_daily_check as t0


class _FakeStrategy:
    """极简假策略：evaluate 返回调用方预设结果，避免依赖 t0_strategy 全链路。"""

    def __init__(self, result=None):
        self._result = result or {}

    def evaluate(self, *args, **kwargs):
        return dict(self._result)


def _holding(**kw):
    h = {"code": "600584", "name": "长电科技", "avg_cost": 10.0, "current_price": 10.0}
    h.update(kw)
    return h


# ── market_gate 大盘闸 ──


def test_market_gate_crash_stops_all():
    """上证 ≤-1% → 当日暂停做T（一票否决）。"""
    g = t0.market_gate({"change_pct": -1.2})
    assert g["pass"] is False
    assert "暂停" in g["note"]


def test_market_gate_weak_halves_position():
    """上证 -1%~-0.5% → 放行但 T 仓减半。"""
    g = t0.market_gate({"change_pct": -0.7})
    assert g["pass"] is True
    assert "减半" in g["note"]


def test_market_gate_normal_ok():
    g = t0.market_gate({"change_pct": 0.3})
    assert g["pass"] is True
    assert "正常" in g["note"]


def test_market_gate_no_data_default_pass():
    """数据不可用 → 默认放行不阻断。"""
    assert t0.market_gate(None)["pass"] is True


# ── _is_stop_loss_hit 实盘止损线 ──


@pytest.mark.parametrize(
    ("code", "price", "cost", "expected"),
    [
        ("600584", 9.0, 10.0, True),   # 主板 -10% > -8% 破线
        ("600584", 9.6, 10.0, False),  # 主板 -4% 未破
        ("600584", 9.2, 10.0, True),   # 主板 -8% 边界（等于即破）
        ("300123", 8.4, 10.0, True),   # 创业板 -16% > -15% 破线
        ("300123", 9.0, 10.0, False),  # 创业板 -10% 未破15%
        ("301234", 8.5, 10.0, True),   # 301 也算创业板 -15% 线
        ("000001", 8.4, 10.0, True),   # 深主板也走 -8%
    ],
)
def test_stop_loss_threshold(code, price, cost, expected):
    assert t0._is_stop_loss_hit(_holding(code=code, avg_cost=cost, current_price=price)) is expected


def test_stop_loss_no_cost_not_hit():
    """avg_cost 为 0/缺失 → 不判破线（避免误清仓）。"""
    assert t0._is_stop_loss_hit({"code": "600584", "avg_cost": 0, "current_price": 9.0}) is False
    assert t0._is_stop_loss_hit({"code": "600584", "current_price": 9.0}) is False


# ── build_eval_row 三态分支 ──


def test_build_row_stop_loss_clears_first():
    """破止损线 → 清仓优先，跳过策略评估（独立于大盘闸）。"""
    r = t0.build_eval_row(_FakeStrategy(), _holding(current_price=8.5), gate_pass=False)
    assert r["direction"] == "不动(清仓优先)"
    assert r["stop_loss_hit"] is True
    assert r["blocked"] is True
    assert "清仓" in r["summary"]
    assert r["t_shares"] is None


def test_build_row_gate_blocked_stops():
    """未破止损但大盘闸不通过 → 统一停手。"""
    r = t0.build_eval_row(_FakeStrategy(), _holding(current_price=10.0), gate_pass=False)
    assert r["direction"] == "不动(大盘停手)"
    assert r["stop_loss_hit"] is False
    assert r["blocked"] is True
    assert "暂停" in r["summary"]


def test_build_row_normal_eval():
    """正常路径 → 透传策略结果 + 新增字段。"""
    fake = _FakeStrategy(
        {
            "direction": "正T",
            "t_position_shares": 200,
            "t_position_value": 2200,
            "t_lot_cost": 2100,
            "vwap_note": "VWAP上移",
            "stop": "-1.5%",
            "pivot": {"S1": 10.5, "P": 10.8, "R1": 11.2},
            "plan": {"entry_rule": "≤成本-2%", "exit_rule": "反弹R1"},
            "summary": "趋势向上",
            "buy_below": 10.6,
            "flags": [],
        }
    )
    r = t0.build_eval_row(fake, _holding(current_price=11.0), gate_pass=True)
    assert r["direction"] == "正T"
    assert r["t_shares"] == 200
    assert r["buy_below"] == 10.6
    assert r["stop_loss_hit"] is False
    assert r["s1"] == 10.5 and r["r1"] == 11.2


# ── fmt_row 极简模板 ──


def _row(**kw):
    base = {
        "code": "600584", "name": "长电科技", "direction": "不动",
        "stop_loss_hit": False, "pnl_pct": None,
        "t_shares": None, "t_value": None, "t_cost": None,
        "vwap_note": "", "stop": "", "s1": None, "p": None, "r1": None,
        "buy_below": None, "entry_rule": "", "exit_rule": "",
        "summary": "", "blocked": False, "t0": True,
    }
    base.update(kw)
    return base


def test_fmt_clear_position_template():
    out = t0.fmt_row(_row(direction="不动(清仓优先)", stop_loss_hit=True, pnl_pct=-15.0))
    assert "清仓" in out and "不做T" in out and "今日不买不卖" in out
    assert "已破-8%止损线(-15.0%)" in out


def test_fmt_gate_blocked_template():
    """大盘停手行（真实数据流 build_eval_row 产出，summary 带保本文案）。"""
    out = t0.fmt_row(
        _row(direction="不动(大盘停手)", blocked=True, summary="大盘单边大跌，当日暂停做T，保本优先")
    )
    assert "停手" in out and "保本" in out


def test_fmt_gate_blocked_summary_fallback():
    """summary 缺失时兜底文案，避免输出空理由。"""
    out = t0.fmt_row(_row(direction="不动(大盘停手)", blocked=True))
    assert "当前信号不足" in out


def test_fmt_long_t_template():
    """正T：买(≤成本-2%) / 卖(R1高抛) / T仓 / 止损 四项齐全。"""
    out = t0.fmt_row(
        _row(direction="正T", buy_below=10.6, r1=11.2, t_shares=200)
    )
    assert "✅ 买: ¥10.60" in out
    assert "✅ 卖: ¥11.20" in out
    assert "T仓: 200股" in out
    assert "单次亏≥1.5%立即平T仓" in out


def test_fmt_short_t_template():
    """反T：卖(R1) / 买回(S1) / T仓=卖出量 / 认错收手。"""
    out = t0.fmt_row(
        _row(direction="反T", s1=10.5, r1=11.2, t_shares=200)
    )
    assert "✅ 卖: ¥11.20" in out
    assert "✅ 买回: ¥10.50" in out
    assert "T仓: 200股" in out
    assert "认错收手" in out


def test_fmt_hold_template():
    out = t0.fmt_row(_row(direction="不动", summary="当前信号不足"))
    assert "当前信号不足" in out
    assert "等下一时段" in out
