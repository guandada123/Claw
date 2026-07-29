"""test_is_trading_day.py — is_trading_day 纯函数 + 日历加载的单测。"""

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
    f = tmp_path / "h.json"
    f.write_text('{"all_holiday_dates": ["2026-01-01", "2026-10-01"]}', encoding="utf-8")
    assert itd.load_holidays(str(f)) == {"2026-01-01", "2026-10-01"}


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
