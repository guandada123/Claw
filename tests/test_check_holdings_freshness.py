"""check_holdings_freshness 的守卫测试（2026-10-11 审计 P0-2 配套）。

钉住的教训：
  · 实盘 portfolio 曾断供 27 天而**全链路无提示**（价格实时、持仓陈旧）→
    「无法判定」必须与「新鲜」区分（unknown ≠ fresh），绝不静默当正常。
  · 判据按**交易日**计（周五更新 → 周一早晨 lag=1 属正常，不应误报）。
  · 全部离线：现造合成 portfolio 文件，不触碰生产数据。
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

import check_holdings_freshness as chf  # noqa: E402


def _write(path: Path, obj) -> Path:
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return path


# ── parse_updated ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-09-14", date(2026, 9, 14)),
        ("2026-10-09 22:50:54", date(2026, 10, 9)),
        ("2026-10-11T08:00:00", date(2026, 10, 11)),
        ("", None),
        (None, None),
        (12345, None),
        ("not-a-date", None),
    ],
)
def test_parse_updated_formats(raw, expected):
    assert chf.parse_updated(raw) == expected


# ── missed_days（交易日口径） ───────────────────────────────


def test_missed_days_natural_when_calendar_unavailable():
    """日历不可用 → 退化自然日，并如实标注口径。"""
    n, cal = chf.missed_days(date(2026, 10, 1), date(2026, 10, 11), None, False)
    assert n == 10
    assert "自然日" in cal


def test_missed_days_same_day_is_zero():
    n, _ = chf.missed_days(date(2026, 10, 11), date(2026, 10, 11), None, False)
    assert n == 0


class _FakeCalendar:
    """把周一~周五视为交易日的极简日历（节假日单独给出）。"""

    def __init__(self, holidays=None):
        self.holidays = holidays or set()

    def is_trading_day(self, d, _):
        return d.weekday() < 5 and d not in self.holidays


def test_missed_days_counts_trading_days_only(monkeypatch):
    """周五更新 → 周一早晨应为 1（**不得**按自然日算成 3）。"""
    fake = _FakeCalendar()
    monkeypatch.setattr(chf, "is_trading_day", None, raising=False)
    monkeypatch.setitem(sys.modules, "is_trading_day", fake)

    fri, mon = date(2026, 10, 9), date(2026, 10, 12)
    n, cal = chf.missed_days(fri, mon, fake.holidays, True)
    assert n == 1, "周五→周一应只算 1 个交易日"
    assert cal == "交易日"


# ── inspect（三态：fresh / stale / unknown） ────────────────


def test_inspect_missing_file_is_unknown(tmp_path):
    row = chf.inspect(tmp_path / "nope.json", ("updated",), date(2026, 10, 11), None, False)
    assert row["status"] == "unknown"
    assert "不存在" in row["reason"]


def test_inspect_broken_json_is_unknown(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    row = chf.inspect(p, ("updated",), date(2026, 10, 11), None, False)
    assert row["status"] == "unknown"
    assert "解析失败" in row["reason"]


def test_inspect_stale_live_portfolio(tmp_path):
    """复现事故现场：updated=09-14、今天 10-11 → stale。"""
    p = _write(tmp_path / "user.json", {"updated": "2026-09-14", "holdings": []})
    row = chf.inspect(p, ("updated",), date(2026, 10, 11), None, False)
    assert row["status"] == "stale"
    assert row["missed"] == 27  # 自然日降级口径
    assert row["updated_source"].startswith("字段")


def test_inspect_fresh_sim_portfolio(tmp_path):
    p = _write(tmp_path / "sim.json", {"config": {"updated_at": "2026-10-09 22:50:54"}})
    row = chf.inspect(p, ("config.updated_at", "updated"), date(2026, 10, 11), None, False)
    assert row["status"] == "fresh"
    assert row["updated"] == "2026-10-09"


def test_inspect_falls_back_to_mtime_and_labels_it(tmp_path):
    """字段缺失 → mtime 兜底，且必须写明来源（不让读者以为是语义时间）。"""
    p = _write(tmp_path / "no_ts.json", {"positions": {}})
    row = chf.inspect(p, ("updated",), date(2026, 10, 11), None, False)
    assert "mtime" in row["updated_source"]
    assert row["status"] in ("fresh", "stale", "unknown")


def test_inspect_threshold_natural_day_fallback(tmp_path):
    """日历不可用 → 阈值放宽到 4 自然日（避免「周五更新、周日检查」恒真误报）。"""
    today = date(2026, 10, 11)
    for lag, expected in [(1, "fresh"), (2, "fresh"), (3, "fresh"), (4, "stale"), (27, "stale")]:
        f = _write(
            tmp_path / f"lag{lag}.json", {"updated": (today - timedelta(days=lag)).isoformat()}
        )
        row = chf.inspect(f, ("updated",), today, None, False)
        assert row["status"] == expected, f"lag={lag} 期望 {expected} 实际 {row['status']}"


def test_inspect_threshold_trading_day_calendar(tmp_path, monkeypatch):
    """日历可用 → 阈值收紧到 2 交易日。"""
    fake = _FakeCalendar()
    monkeypatch.setitem(sys.modules, "is_trading_day", fake)
    today = date(2026, 10, 13)  # 周二
    # 周一为交易日 → 用「周一」作为更新日，lag=1（周二当日）→ fresh
    f1 = _write(tmp_path / "t1.json", {"updated": date(2026, 10, 12).isoformat()})
    assert chf.inspect(f1, ("updated",), today, fake.holidays, True)["status"] == "fresh"
    # 上周五更新 → 周一、周二两个交易日 → stale
    f2 = _write(tmp_path / "t2.json", {"updated": date(2026, 10, 9).isoformat()})
    row2 = chf.inspect(f2, ("updated",), today, fake.holidays, True)
    assert row2["status"] == "stale" and row2["limit"] == 2
