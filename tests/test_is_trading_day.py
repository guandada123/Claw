"""test_is_trading_day.py — is_trading_day 纯函数 + 日历加载的单测。"""

import json
from datetime import date
from pathlib import Path

import is_trading_day as itd
import pytest


def test_holidays_path_is_absolute_and_cwd_independent():
    # F13 修复：HOLIDAYS_FILE 应基于 __file__ 推导为绝对路径，不依赖运行 cwd
    assert Path(itd.HOLIDAYS_FILE).is_absolute()
    assert itd.HOLIDAYS_FILE.endswith("data/astock_holidays.json")


def test_weekend_saturday():
    # 2026-07-18 是周六
    assert itd.is_trading_day(date(2026, 7, 18), set()) is False


def test_weekend_sunday():
    # 2026-07-19 是周日
    assert itd.is_trading_day(date(2026, 7, 19), set()) is False


def test_weekday_not_holiday():
    # 2026-07-15 周三，非节假日
    assert itd.is_trading_day(date(2026, 7, 15), set()) is True


def test_weekday_holiday_excluded():
    holidays = {"2026-07-15"}
    assert itd.is_trading_day(date(2026, 7, 15), holidays) is False


def test_load_holidays_from_file(tmp_path):
    """契约（09-25 起）：返回 {'dates': set, 'names': {date: 假期名}}。

    旧契约是**裸 set**，09-25 升级为带名 dict（原实现只能给出笼统的"法定节假日休市"，
    中秋/国庆无法区分）。`is_trading_day` 仍兼容裸 set（见其 docstring），
    但 `load_holidays` 本身的返回值已变 —— 本用例随契约同步更新。
    """
    f = tmp_path / "h.json"
    f.write_text('{"all_holiday_dates": ["2026-01-01", "2026-10-01"]}', encoding="utf-8")
    out = itd.load_holidays(str(f))
    assert out["dates"] == {"2026-01-01", "2026-10-01"}
    assert set(out["names"]) == {"2026-01-01", "2026-10-01"}
    # 扁平列表兜底来源的名字
    assert out["names"]["2026-10-01"] == "法定节假日"


def test_load_holidays_named_sections(tmp_path):
    """新能力：`holidays{}` 分节可命名 → 休市 reason 能说清是哪一天假期。

    这是 09-25 那次修复的**目的**，必须被钉住，否则又退回"笼统法定节假日"。
    """
    f = tmp_path / "h.json"
    f.write_text(json.dumps({
        "holidays": {
            "中秋节": {"dates": ["2026-09-25"]},
            "国庆节": {"dates": ["2026-10-01", "2026-10-02"]},
        },
        "all_holiday_dates": ["2026-12-31"],
    }), encoding="utf-8")
    out = itd.load_holidays(str(f))
    assert out["names"]["2026-09-25"] == "中秋节"
    assert out["names"]["2026-10-02"] == "国庆节"
    assert out["names"]["2026-12-31"] == "法定节假日"      # 扁平兜底
    assert {"2026-09-25", "2026-10-01", "2026-10-02", "2026-12-31"} <= out["dates"]


def test_is_trading_day_accepts_both_shapes(tmp_path):
    """兼容性铁律：is_trading_day 两种入参都要认（dict 与旧裸 set）。"""
    d = date(2026, 10, 1)
    assert itd.is_trading_day(d, {"dates": {"2026-10-01"}, "names": {}}) is False
    assert itd.is_trading_day(d, {"2026-10-01"}) is False
    assert itd.is_trading_day(date(2026, 10, 9), {"dates": {"2026-10-01"}, "names": {}}) is True


def test_load_holidays_missing_file_exits(tmp_path):
    missing = tmp_path / "nope.json"
    with pytest.raises(SystemExit) as exc:
        itd.load_holidays(str(missing))
    assert exc.value.code == 2


def test_load_holidays_malformed_exits(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        itd.load_holidays(str(f))
    assert exc.value.code == 2
