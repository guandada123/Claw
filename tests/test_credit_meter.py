"""test_credit_meter.py — 积分读取单源（credit_meter）的解析、归属与失败语义。

背景：`credit_meter` 是「积分」这一唯一真实消耗单位的读取单源，被 `budget_guard` /
`credit_vs_token` / `unified_ops_center` 三处依赖。此前上游测试把 `total_for_month`
整体 mock 掉，导致 `load_rows` 的解析逻辑（从 workbuddy.db 读 credit_json 求和）
**从未被执行**（实测覆盖率 22%，函数体全部空跑）。本文件补上这层护栏。

隔离：全部用例用 `tmp_path` 造临时 sqlite，**绝不触碰真实 `~/.workbuddy/workbuddy.db`**。
"""

import json
import sqlite3
from datetime import datetime

import pytest
from credit_meter import (
    CREDITS_PER_MILLION_TOKENS,
    CreditReadError,
    _key_of,
    load_rows,
    total_for_day,
    total_for_month,
    totals_by_month,
)


def _make_db(path, rows):
    """造 session_usage 表（真实 schema 的所需子集）。"""
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE session_usage (session_id TEXT, credit_json TEXT, updated_at INTEGER)"
    )
    con.executemany("INSERT INTO session_usage VALUES (?,?,?)", rows)
    con.commit()
    con.close()


def _seed(path, *pairs):
    """pairs: (session_id, credit_json 字符串或对象, ts_ms)"""
    rows = [
        (sid, cj if isinstance(cj, str) else json.dumps(cj), ts) for sid, cj, ts in pairs
    ]
    _make_db(path, rows)


def _ts(y, m, d, hh=12, mm=0):
    """本地时区的毫秒时间戳（与 _key_of 的 fromtimestamp 同口径，避免时区漂移）。"""
    return int(datetime(y, m, d, hh, mm).timestamp() * 1000)


# ============================================================
# load_rows — credit_json 解析
# ============================================================


def test_load_rows_sums_numeric_members(tmp_path):
    db = tmp_path / "wb.db"
    _seed(db, ("s1", {"a": 1.5, "b": 2.5}, _ts(2026, 10, 5)))

    rows = load_rows(db)

    assert len(rows) == 1
    assert rows[0]["session_id"] == "s1"
    assert rows[0]["credits"] == pytest.approx(4.0)
    assert rows[0]["calls"] == 2  # key 即请求 id 的个数


def test_load_rows_ignores_non_numeric_members(tmp_path):
    db = tmp_path / "wb.db"
    _seed(db, ("s1", {"a": 1.5, "b": "x", "c": None, "d": {"nested": 1}}, _ts(2026, 10, 5)))

    rows = load_rows(db)

    assert rows[0]["credits"] == pytest.approx(1.5)  # 只有 a 计入
    assert rows[0]["calls"] == 4  # 但 calls 数的是成员个数，不是数值成员数


def test_load_rows_bool_counted_as_number_quirk(tmp_path):
    """⚠️ 固化现状（characterization）：`isinstance(True, int)` 为真 → JSON true/false 被计入积分。

    真实 `credit_json` 是 `{请求id: 积分数}`，不会出现 bool；此用例只防止该语义被无声改动。
    若将来决定改为「严格只用 int/float 且排除 bool」，必须同步更新本用例。
    """
    db = tmp_path / "wb.db"
    _seed(db, ("s1", {"a": 1.0, "b": True, "c": False}, _ts(2026, 10, 5)))

    assert load_rows(db)[0]["credits"] == pytest.approx(2.0)  # 1.0 + 1.0 + 0.0


def test_load_rows_skips_malformed_and_non_dict_json(tmp_path):
    db = tmp_path / "wb.db"
    _seed(
        db,
        ("bad", "{not json", _ts(2026, 10, 5)),
        ("list", [1, 2, 3], _ts(2026, 10, 5)),
        ("num", "5", _ts(2026, 10, 5)),
        ("str", '"hello"', _ts(2026, 10, 5)),
        ("ok", {"a": 1.0}, _ts(2026, 10, 5)),
    )

    rows = load_rows(db)

    assert [r["session_id"] for r in rows] == ["ok"]


def test_load_rows_merges_same_session_keeps_max_ts(tmp_path):
    db = tmp_path / "wb.db"
    t1, t2 = _ts(2026, 10, 1), _ts(2026, 10, 3)
    _seed(db, ("s1", {"a": 1.0}, t1), ("s1", {"b": 2.0, "c": 3.0}, t2))

    rows = load_rows(db)

    assert len(rows) == 1
    assert rows[0]["credits"] == pytest.approx(6.0)
    assert rows[0]["calls"] == 3
    assert rows[0]["ts_ms"] == t2  # 取 max（最后一次活动）


def test_load_rows_filters_empty_and_null(tmp_path):
    db = tmp_path / "wb.db"
    _make_db(
        db,
        [
            ("empty", "", _ts(2026, 10, 5)),
            ("null", None, _ts(2026, 10, 5)),
            ("brace", "{}", _ts(2026, 10, 5)),
            ("ok", '{"a": 2.0}', _ts(2026, 10, 5)),
        ],
    )

    rows = load_rows(db)

    # 空串 / NULL 由 SQL 过滤；'{}' 解析成功但积分为 0，保留在结果里
    assert sorted(r["session_id"] for r in rows) == ["brace", "ok"]
    assert next(r for r in rows if r["session_id"] == "brace")["credits"] == 0.0


def test_load_rows_keeps_zero_ts(tmp_path):
    db = tmp_path / "wb.db"
    _seed(db, ("s1", {"a": 1.0}, 0))

    assert load_rows(db)[0]["ts_ms"] == 0


# ============================================================
# load_rows — 失败语义（读不到 ≠ 0）
# ============================================================


def test_load_rows_missing_db_raises(tmp_path):
    with pytest.raises(CreditReadError, match="平台库不存在"):
        load_rows(tmp_path / "nope.db")


def test_load_rows_missing_table_raises(tmp_path):
    db = tmp_path / "wb.db"
    sqlite3.connect(db).close()  # 空库：无 session_usage 表

    with pytest.raises(CreditReadError, match="读 session_usage 失败"):
        load_rows(db)


# ============================================================
# _key_of — 时间归属
# ============================================================


def test_key_of_zero_returns_none():
    assert _key_of(0, "%Y-%m") is None


def test_key_of_formats_local_month_and_day():
    ts = _ts(2026, 10, 5, 9, 30)

    assert _key_of(ts, "%Y-%m") == "2026-10"
    assert _key_of(ts, "%Y-%m-%d") == "2026-10-05"


# ============================================================
# 聚合（纯函数，传 rows 避免读真实库）
# ============================================================

_ROWS = [
    {"session_id": "a", "credits": 10.0, "calls": 1, "ts_ms": _ts(2026, 9, 20)},
    {"session_id": "b", "credits": 5.5, "calls": 2, "ts_ms": _ts(2026, 10, 1)},
    {"session_id": "c", "credits": 2.5, "calls": 1, "ts_ms": _ts(2026, 10, 1)},
    {"session_id": "d", "credits": 1.0, "calls": 1, "ts_ms": 0},  # 无 ts → 不归月
]


def test_totals_by_month_aggregates_and_is_sorted():
    got = totals_by_month(_ROWS)

    assert list(got) == ["2026-09", "2026-10"]  # 按 key 排序 = 时间序
    assert got["2026-09"] == pytest.approx(10.0)
    assert got["2026-10"] == pytest.approx(8.0)
    assert pytest.approx(sum(got.values())) == 18.0  # d 的 1.0 不计入任何月


def test_total_for_month_picks_month():
    assert total_for_month("2026-10", _ROWS) == pytest.approx(8.0)
    assert total_for_month("2026-09", _ROWS) == pytest.approx(10.0)
    assert total_for_month("2026-01", _ROWS) == 0.0


def test_total_for_day_only_matching_day():
    assert total_for_day("2026-10-01", _ROWS) == pytest.approx(8.0)
    assert total_for_day("2026-09-20", _ROWS) == pytest.approx(10.0)
    assert total_for_day("1999-01-01", _ROWS) == 0.0


# ============================================================
# 契约
# ============================================================


def test_credit_rate_is_single_positive_float():
    """倍率是「唯一来源」（模块 docstring 明确「勿在别处再写一个数字」）。

    此处只守类型与正负、不钉死数值——口径修订（实测倍率变化）是允许的，
    但必须仍是一个正 float。
    """
    assert isinstance(CREDITS_PER_MILLION_TOKENS, float)
    assert CREDITS_PER_MILLION_TOKENS > 0
