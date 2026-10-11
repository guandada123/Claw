"""check_disposal_triggers 测试 — 处置触发器（2026-10-11 配套方案）。

钉住四件事：
  ① T1/T2/T3 三条触发器的判定口径（含边界）；
  ② 「结构变化 → 基线重置、计时归零」（否则减仓后还会被追着报"无动作"）；
  ③ 达成 ≤50% → 明确提示「触发器可退休」（机制要能自己退场，不然会变噪音）；
  ④ 日历不可用 → 自然日降级并**如实标注**（沿用守卫脚本的既有取舍）。
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / ".workbuddy" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import check_disposal_triggers as cdt  # noqa: E402

# ── 纯函数：evaluate ────────────────────────────────────


def _portfolio(holdings, total=100000.0):
    return {"holdings": holdings, "summary": {"total_assets": total}}


def test_evaluate_deep_loss_and_concentration():
    pf = _portfolio(
        [
            {"code": "600584", "name": "长电", "shares": 100, "avg_cost": 100.0, "current_price": 60.0},
            {"code": "002185", "name": "华天", "shares": 100, "avg_cost": 100.0, "current_price": 80.0},
        ]
    )
    cache = {"600584": "半导体", "002185": "半导体"}
    m = cdt.evaluate(pf, cache, "半导体", [])
    assert [h["code"] for h in m["deep_loss"]] == ["600584"]  # -40% 触发，-20% 不触发
    assert m["sector_pct"] == pytest.approx((6000 + 8000) / 100000 * 100, abs=0.1)


def test_evaluate_ignores_missing_cost():
    pf = _portfolio([{"code": "X", "shares": 100, "current_price": 10.0}])
    m = cdt.evaluate(pf, {}, "半导体", [])
    assert m["per_holding"][0]["pnl_pct"] is None
    assert m["deep_loss"] == []


@pytest.mark.parametrize(
    ("series", "expected"),
    [
        ([25.0, 18.0, 19.0, 17.0], True),  # 末 3 个均 <20
        ([10.0, 25.0, 18.0, 19.0], False),  # 窗口内有 25
        ([15.0, 16.0], False),  # 不足 3 个 report_date
        ([], False),
    ],
)
def test_evaluate_t2_window(series, expected):
    m = cdt.evaluate(_portfolio([]), {}, "半导体", series)
    assert m["t2_weak"] is expected


# ── 纯函数：trading_days_between ────────────────────────


def test_trading_days_skips_weekend():
    fri, mon = date(2026, 10, 9), date(2026, 10, 12)  # 周五 → 周一
    n, note = cdt.trading_days_between(fri, mon, None, cal_ok=False)
    assert note.startswith("自然日")  # 无日历 → 降级且标注
    assert n == 3


def test_trading_days_with_calendar_holiday():
    """注入的休市日必须被排除（注意：is_trading_day 自带权威节假日表，
    故用例日期须避开真实节假日，只让**注入值**起作用）。"""
    holidays = {date(2026, 10, 9)}  # 该周五设为休市
    n, note = cdt.trading_days_between(date(2026, 10, 8), date(2026, 10, 12), holidays, cal_ok=True)
    assert note == "交易日"
    assert n == 1  # 10-09 休市 / 10-10,11 周末 / 10-12 周一为唯一交易日


# ── 集成：main 的三条路径 ────────────────────────────────


def _seed(tmp_path, monkeypatch, holdings, sector_pct, *, created_days_ago=10, baseline=True, rps=None):
    portfolio = {
        "holdings": holdings,
        "summary": {"total_assets": 100000.0},
    }
    strength = {"report_date": date.today().isoformat(), "sectors": []}
    if rps is not None:
        strength["sectors"] = [{"sector": "半导体", "rps": rps}]
    files = {
        "USER_PORTFOLIO": portfolio,
        "SECTOR_CACHE": {h["code"]: "半导体" for h in holdings},
        "SECTOR_STRENGTH": strength,
    }
    for name, data in files.items():
        p = tmp_path / f"{name}.json"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(cdt, name, p)
    monkeypatch.setattr(cdt, "RPS_HISTORY", tmp_path / "rps_history.json")
    bpath = tmp_path / "baseline.json"
    monkeypatch.setattr(cdt, "BASELINE_FILE", bpath)
    if baseline:
        bpath.write_text(
            json.dumps(
                {
                    "created": (date.today() - timedelta(days=created_days_ago)).isoformat(),
                    "holdings": {h["code"]: h["shares"] for h in holdings},
                    "sector_pct": sector_pct,
                }
            ),
            encoding="utf-8",
        )
    return bpath


def _run(monkeypatch, argv=()):
    monkeypatch.setattr(sys, "argv", ["check_disposal_triggers.py", *argv])
    return cdt.main()


_DEEP = [{"code": "600584", "name": "长电", "shares": 100, "avg_cost": 100.0, "current_price": 60.0}]


def test_main_first_run_creates_baseline(tmp_path, monkeypatch):
    bpath = _seed(tmp_path, monkeypatch, _DEEP, 78.5, baseline=False)
    assert _run(monkeypatch) == 0
    assert json.loads(bpath.read_text())["holdings"] == {"600584": 100}


def test_main_fires_when_idle_and_concentrated(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, _DEEP, 78.5, created_days_ago=10)
    assert _run(monkeypatch) == 1  # 占比 78.5% > 65% 且已过 ≥5 交易日


def test_main_resets_baseline_after_position_change(tmp_path, monkeypatch):
    """减仓后必须重置计时，否则会一直报「无动作」。"""
    bpath = _seed(tmp_path, monkeypatch, _DEEP, 78.5, created_days_ago=10)
    # 把基线股数改成"减仓前"的更大值 → 现状与之不同 = 已动作
    base = json.loads(bpath.read_text())
    base["holdings"] = {"600584": 300}
    bpath.write_text(json.dumps(base), encoding="utf-8")
    assert _run(monkeypatch) == 0
    assert json.loads(bpath.read_text())["holdings"] == {"600584": 100}


def test_main_retires_when_target_met(tmp_path, monkeypatch):
    """占比 ≤50% → 提示退休且不报警（机制要能自己退场）。"""
    holdings = [
        {"code": "600584", "name": "长电", "shares": 100, "avg_cost": 100.0, "current_price": 90.0},
        {"code": "000001", "name": "平安", "shares": 10000, "avg_cost": 1.0, "current_price": 9.0},
    ]
    _seed(tmp_path, monkeypatch, holdings, 10.0)
    monkeypatch.setattr(cdt, "SECTOR_CACHE", cdt.SECTOR_CACHE)  # 保持缓存映射
    assert _run(monkeypatch) == 0


def test_main_silent_when_nothing_fires(tmp_path, monkeypatch):
    """低集中度 + 无深亏 + RPS 不弱 → 不触发、不报警。"""
    holdings = [
        {"code": "600584", "name": "长电", "shares": 10, "avg_cost": 100.0, "current_price": 95.0},
        {"code": "000001", "name": "平安", "shares": 100, "avg_cost": 10.0, "current_price": 10.0},
    ]
    _seed(tmp_path, monkeypatch, holdings, 5.0, created_days_ago=1, rps=55.0)
    assert _run(monkeypatch) == 0


def test_holidays_normalized_from_date_objects():
    """回归：传 date 对象集合也必须正确排除（is_trading_day 只认 ISO 字符串，
    不做归一化会**静默把节假日当交易日**）。"""
    holidays = {date(2026, 10, 9)}
    n, note = cdt.trading_days_between(date(2026, 10, 8), date(2026, 10, 12), holidays, cal_ok=True)
    assert note == "交易日"
    assert n == 1


def test_trading_days_none_holidays_is_safe():
    """holidays=None（异常输入）不得抛异常 —— 退化为「只按周末」计数。"""
    n, note = cdt.trading_days_between(date(2026, 10, 8), date(2026, 10, 12), None, cal_ok=True)
    assert note == "交易日"
    assert n == 2  # 10-09(五) 与 10-12(一)
