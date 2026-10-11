"""check_disposal_triggers 测试 — 处置触发器（2026-10-11 方案配套）。

钉住五件事：
  ① T1/T2/T3 三条触发器的判定口径（含边界）；
  ② 「结构变化 → 基线重置、计时归零」（否则减仓后还会被追着报"无动作"）；
  ③ 达成 ≤50% → 明确提示「触发器可退休」（机制要能自己退场，不然会变噪音）；
  ④ **T1 独立于处置目标**：集中度已达标也不能吞掉深度亏损告警；
  ⑤ 日历口径：不可用即自然日降级并**如实标注**；且**用例不得依赖外部日历文件**
     （2026-10-11 事故：该文件当时未入库 → CI 里降级为自然日 → 本文件在 CI 红）。
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

# ══════════════════════════════════════════════════════
# 纯函数：evaluate
# ══════════════════════════════════════════════════════


def _portfolio(holdings, total=100000.0):
    return {"holdings": holdings, "summary": {"total_assets": total}}


def test_evaluate_deep_loss_and_concentration():
    pf = _portfolio(
        [
            {
                "code": "600584",
                "name": "长电",
                "shares": 100,
                "avg_cost": 100.0,
                "current_price": 60.0,
            },
            {
                "code": "002185",
                "name": "华天",
                "shares": 100,
                "avg_cost": 100.0,
                "current_price": 80.0,
            },
        ]
    )
    cache = {"600584": "半导体", "002185": "半导体"}
    m = cdt.evaluate(pf, cache, "半导体", [])
    assert [h["code"] for h in m["deep_loss"]] == ["600584"]  # -40% 触发；-20% 不触发
    assert m["sector_pct"] == pytest.approx((6000 + 8000) / 100000 * 100, abs=0.1)


def test_evaluate_ignores_missing_cost():
    """缺成本价 → 不参与盈亏判定（宁缺勿报错）。"""
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


# ══════════════════════════════════════════════════════
# 纯函数：trading_days_between
# ══════════════════════════════════════════════════════


def test_trading_days_without_calendar_degrades_and_labels():
    """日历不可用 → 自然日降级，且**必须标注**口径（不得假装是交易日口径）。"""
    n, note = cdt.trading_days_between(date(2026, 10, 9), date(2026, 10, 12), None, cal_ok=False)
    assert note.startswith("自然日")
    assert n == 3


def test_trading_days_with_calendar_excludes_holiday():
    """注入的休市日必须被排除。"""
    n, note = cdt.trading_days_between(
        date(2026, 10, 8), date(2026, 10, 12), {"2026-10-09"}, cal_ok=True
    )
    assert note == "交易日"
    assert n == 1  # 10-09 休市 / 10-10,11 周末 / 10-12 周一为唯一交易日


def test_holidays_normalized_from_date_objects():
    """回归：传 date 对象集合也必须正确排除。

    事故形态：`is_trading_day` 内部是 `d.isoformat() not in dates` —— **只认字符串**；
    传 date 对象会恒不命中 → **静默把节假日当交易日**（计数偏大且不报错）。
    """
    n, _ = cdt.trading_days_between(
        date(2026, 10, 8), date(2026, 10, 12), {date(2026, 10, 9)}, cal_ok=True
    )
    assert n == 1


def test_trading_days_none_holidays_is_safe():
    """holidays=None（异常输入）不得抛异常 —— 退化为「只按周末」计数。"""
    n, note = cdt.trading_days_between(date(2026, 10, 8), date(2026, 10, 12), None, cal_ok=True)
    assert note == "交易日"
    assert n == 2  # 10-09(五) 与 10-12(一)


# ══════════════════════════════════════════════════════
# 集成：main 的路径（日历**固定注入** → 不依赖 astock_holidays.json）
# ══════════════════════════════════════════════════════


def _fixed_calendar(monkeypatch, holidays=None, ok=True):
    """固定日历状态。

    🔴 2026-10-11 事故：`astock_holidays.json` 当时未入库（被 .gitignore 挡住）→
    CI 里 load_calendar() 失败 → 自然日降级 → 「10 天前」被当成 ≥5 个交易日 →
    T3 误触发、本文件在 CI 红。用例必须与生产同一日历口径，且不依赖外部文件。
    """
    monkeypatch.setattr(cdt, "load_calendar", lambda: (holidays or set(), ok))


def _seed(
    tmp_path,
    monkeypatch,
    holdings,
    *,
    sector_map=None,
    created_days_ago=10,
    baseline=True,
    rps=None,
):
    """布置快照/板块/基线三份数据并指到 tmp。"""
    portfolio = {"holdings": holdings, "summary": {"total_assets": 100000.0}}
    strength = {"report_date": date.today().isoformat(), "sectors": []}
    if rps is not None:
        strength["sectors"] = [{"sector": "半导体", "rps": rps}]
    files = {
        "USER_PORTFOLIO": portfolio,
        "SECTOR_CACHE": sector_map
        if sector_map is not None
        else {h["code"]: "半导体" for h in holdings},
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
                }
            ),
            encoding="utf-8",
        )
    return bpath


def _run(monkeypatch, argv=()):
    monkeypatch.setattr(sys, "argv", ["check_disposal_triggers.py", *argv])
    return cdt.main()


# 单一持仓：-40%（触发 T1）；市值 6,000/100,000 = 6%（不触发 T3）
_DEEP = [
    {"code": "600584", "name": "长电", "shares": 100, "avg_cost": 100.0, "current_price": 60.0}
]
# 单一持仓：-30%（**不**触发 T1）；市值 70,000/100,000 = 70%（可触发 T3）
_CONCENTRATED = [
    {"code": "600584", "name": "长电", "shares": 1000, "avg_cost": 100.0, "current_price": 70.0}
]


def test_main_first_run_creates_baseline(tmp_path, monkeypatch):
    _fixed_calendar(monkeypatch)
    bpath = _seed(tmp_path, monkeypatch, _DEEP, baseline=False)
    assert _run(monkeypatch) == 0
    assert json.loads(bpath.read_text(encoding="utf-8"))["holdings"] == {"600584": 100}


def test_main_t1_deep_loss_fires(tmp_path, monkeypatch):
    """T1 独立生效（集中度仅 6% 也应报）。"""
    _fixed_calendar(monkeypatch)
    _seed(tmp_path, monkeypatch, _DEEP, created_days_ago=1)
    assert _run(monkeypatch) == 1


def test_main_t1_not_swallowed_when_concentration_ok(tmp_path, monkeypatch):
    """回归：T1 与「处置目标是否达成」互相独立。

    原实现把「达标即退休」判定放在触发之前 → 集中度低时 **-40% 的深度亏损被静默吞掉**。
    """
    _fixed_calendar(monkeypatch)
    holdings = [
        {"code": "600584", "name": "长电", "shares": 100, "avg_cost": 100.0, "current_price": 60.0},
        {"code": "000001", "name": "平安", "shares": 1000, "avg_cost": 10.0, "current_price": 10.0},
    ]
    _seed(
        tmp_path,
        monkeypatch,
        holdings,
        sector_map={"600584": "半导体", "000001": "银行"},  # 半导体占比仅 6%
        created_days_ago=1,
    )
    assert _run(monkeypatch) == 1, "集中度达标不得吞掉深度亏损告警"


def test_main_t3_fires_when_idle_and_concentrated(tmp_path, monkeypatch):
    """T3 单独生效：集中度 70%（>65）+ 10 天未动作（≥5 交易日）+ 股数未变。

    构造 -30%（而非 ≤-35%），确保**不是 T1 在起作用**，否则测不到 T3。
    """
    _fixed_calendar(monkeypatch)
    _seed(tmp_path, monkeypatch, _CONCENTRATED, created_days_ago=10)
    assert _run(monkeypatch) == 1


def test_main_t3_silent_when_not_idle_enough(tmp_path, monkeypatch):
    """基线仅 1 天前（刚处理过）→ 不该报「无动作」。"""
    _fixed_calendar(monkeypatch)
    _seed(tmp_path, monkeypatch, _CONCENTRATED, created_days_ago=1)
    assert _run(monkeypatch) == 0


def test_main_resets_baseline_after_position_change(tmp_path, monkeypatch):
    """减仓后必须重置计时，否则会一直报「无动作」。"""
    _fixed_calendar(monkeypatch)
    bpath = _seed(tmp_path, monkeypatch, _CONCENTRATED, created_days_ago=10)
    base = json.loads(bpath.read_text(encoding="utf-8"))
    base["holdings"] = {"600584": 5000}  # 基线记录的是减仓前的股数
    bpath.write_text(json.dumps(base), encoding="utf-8")
    assert _run(monkeypatch) == 0
    assert json.loads(bpath.read_text(encoding="utf-8"))["holdings"] == {"600584": 1000}


def test_main_retires_when_target_met(tmp_path, monkeypatch):
    """占比 ≤50% → 提示退休且不报警（机制要能自己退场）。"""
    _fixed_calendar(monkeypatch)
    holdings = [
        {"code": "600584", "name": "长电", "shares": 100, "avg_cost": 100.0, "current_price": 90.0},
        {"code": "000001", "name": "平安", "shares": 1000, "avg_cost": 10.0, "current_price": 10.0},
    ]
    _seed(
        tmp_path, monkeypatch, holdings, sector_map={"600584": "半导体", "000001": "银行"}
    )  # 半导体 9%
    assert _run(monkeypatch) == 0


def test_main_t2_fires_on_persistent_weakness(tmp_path, monkeypatch):
    """T2：连续 3 个 report_date RPS<20（本次写入 1 个，其余由历史文件提供）。"""
    _fixed_calendar(monkeypatch)
    _seed(tmp_path, monkeypatch, _DEEP, created_days_ago=1, rps=15.0)
    (tmp_path / "rps_history.json").write_text(
        json.dumps({"半导体": {"2026-10-08": 12.0, "2026-10-09": 18.0}}), encoding="utf-8"
    )
    assert _run(monkeypatch) == 1


def test_main_silent_when_nothing_fires(tmp_path, monkeypatch):
    """低集中度 + 无深亏 + RPS 不弱 → 不触发、不报警。"""
    _fixed_calendar(monkeypatch)
    holdings = [
        {"code": "600584", "name": "长电", "shares": 10, "avg_cost": 100.0, "current_price": 95.0},
        {"code": "000001", "name": "平安", "shares": 100, "avg_cost": 10.0, "current_price": 10.0},
    ]
    _seed(
        tmp_path,
        monkeypatch,
        holdings,
        sector_map={"600584": "半导体", "000001": "银行"},
        created_days_ago=1,
        rps=55.0,
    )
    assert _run(monkeypatch) == 0
