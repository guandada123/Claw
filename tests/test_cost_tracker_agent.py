"""test_cost_tracker_agent.py — cost_tracker 的自动化 agent 用量汇总。

`collect_agent_usage` / `agent_rollup` 直读 workbuddy.db（sessions × session_usage），
是「自动化会话模型 token 体量」的唯一来源；此前无测试（cost_tracker.py 未覆盖段 706-850）。
这条 SQL 若写错（JOIN 条件、布尔求和、时间窗），会**静默产出错误体量**而不报错。

隔离：monkeypatch `AUTOMATION_DB` 到 tmp_path 造的最小 sqlite，**绝不读真实平台库**。
"""

import sqlite3
import time

import cost_tracker as ct
import pytest

_NOW_MS = int(time.time() * 1000)


def _make_db(path, sessions, usages):
    """sessions: (id, model, created_at, is_background_automation)；usages: (session_id, used)"""
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE sessions (id TEXT, model TEXT, created_at TEXT, "
        "is_background_automation INTEGER)"
    )
    con.execute("CREATE TABLE session_usage (session_id TEXT, used INTEGER)")
    con.executemany("INSERT INTO sessions VALUES (?,?,?,?)", sessions)
    con.executemany("INSERT INTO session_usage VALUES (?,?)", usages)
    con.commit()
    con.close()


@pytest.fixture
def fake_db(tmp_path, monkeypatch):
    db = tmp_path / "wb.db"
    monkeypatch.setattr(ct, "AUTOMATION_DB", db)
    return db


# ============================================================
# collect_agent_usage
# ============================================================


def test_groups_by_model_and_applies_factors(fake_db):
    _make_db(
        fake_db,
        [
            ("s1", "hy3", str(_NOW_MS), 1),
            ("s2", "hy3", str(_NOW_MS), 1),
            ("s3", "deepseek-v4-flash", str(_NOW_MS), 1),
            ("s4", "hy3", str(_NOW_MS), 0),  # 非自动化 → 排除
        ],
        [("s1", 1000), ("s2", 2000), ("s3", 500)],
    )

    out = ct.collect_agent_usage(days=1)

    assert set(out) == {"hy3", "deepseek-v4-flash"}
    assert out["hy3"]["runs"] == 2
    assert out["hy3"]["covered"] == 2
    assert out["hy3"]["context_tokens"] == 3000
    assert out["hy3"]["est_input"] == int(3000 * ct.AGENT_TOKEN_FACTOR)
    assert out["hy3"]["est_output"] == int(3000 * ct.AGENT_TOKEN_FACTOR * ct.AGENT_OUTPUT_RATIO)
    assert out["deepseek-v4-flash"]["context_tokens"] == 500


def test_session_without_usage_row_counts_zero(fake_db):
    _make_db(fake_db, [("s1", "hy3", str(_NOW_MS), 1)], [])

    out = ct.collect_agent_usage(days=1)

    assert out["hy3"]["runs"] == 1
    assert out["hy3"]["covered"] == 0
    assert out["hy3"]["context_tokens"] == 0
    assert out["hy3"]["est_input"] == 0


def test_skips_rows_without_model(fake_db):
    _make_db(
        fake_db,
        [("s1", None, str(_NOW_MS), 1), ("s2", "hy3", str(_NOW_MS), 1)],
        [("s1", 100), ("s2", 200)],
    )

    out = ct.collect_agent_usage(days=1)

    assert set(out) == {"hy3"}


def test_time_window_excludes_old_sessions(fake_db):
    old = str(_NOW_MS - 10 * 86_400_000)
    _make_db(
        fake_db,
        [("s1", "hy3", old, 1), ("s2", "hy3", str(_NOW_MS), 1)],
        [("s1", 9999), ("s2", 100)],
    )

    out = ct.collect_agent_usage(days=1)

    assert out["hy3"]["runs"] == 1
    assert out["hy3"]["context_tokens"] == 100


def test_missing_db_returns_empty_and_warns(fake_db, capsys):
    # fake_db 指向尚未创建的路径 → 早退分支
    assert ct.collect_agent_usage(days=1) == {}
    assert "未找到自动化库" in capsys.readouterr().out


# ============================================================
# agent_rollup
# ============================================================


def test_agent_rollup_returns_usage_and_prints(fake_db, capsys):
    _make_db(fake_db, [("s1", "hy3", str(_NOW_MS), 1)], [("s1", 1000)])

    out = ct.agent_rollup(days=1)

    assert set(out) == {"hy3"}
    assert "合计: 1 个会话" in capsys.readouterr().out


def test_agent_rollup_empty_returns_empty(fake_db, capsys):
    _make_db(fake_db, [], [])

    assert ct.agent_rollup(days=1) == {}
    assert "无数据" in capsys.readouterr().out
