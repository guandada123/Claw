"""memory_headroom_check 的守卫测试。

核心回归点（2026-10-10 取证）：
用户级 MEMORY.md 实测「上限 7353B / 规则止 6994B」→ 可见余量只剩 359B，
但每日记忆维护只校验「索引段可见」、自检脚本又零自动化调用 → 两边都报通过。
本守卫把余量变成可判定数；且**解析不出结构时必须 rc=2 报错，不得静默放过**。

全部离线：真检查器被替身接管，不读真实记忆文件。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / ".workbuddy" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import memory_headroom_check as mhc  # noqa: E402


class _FakeChecker:
    """最小替身：只提供 check() 与 DEFAULT_LIMITS。"""

    DEFAULT_LIMITS: dict = {}

    def __init__(self, info: dict, problems: list | None = None):
        self._info = info
        self._problems = problems or []

    def check(self, _path: str, _limit: int):
        return list(self._problems), dict(self._info)


@pytest.fixture()
def memfile(tmp_path):
    f = tmp_path / "MEMORY.md"
    f.write_text("# x\n", encoding="utf-8")
    return f


def test_headroom_is_limit_minus_rule_offset(memfile):
    mod = _FakeChecker({"rule_off": 6994, "rules": 72, "limit": 7353})
    rows, errors = mhc.evaluate(mod, [memfile], threshold=500)
    assert errors == []
    assert rows[0]["headroom"] == 7353 - 6994 == 359
    assert rows[0]["status"] == "low"


def test_threshold_boundary_is_respected(memfile):
    mod = _FakeChecker({"rule_off": 6994, "rules": 72})
    assert mhc.evaluate(mod, [memfile], threshold=359)[0][0]["status"] == "ok"
    assert mhc.evaluate(mod, [memfile], threshold=360)[0][0]["status"] == "low"


def test_missing_rule_offset_is_an_error_not_a_pass(memfile):
    """结构变了导致解析不出 rule_off 时，绝不能当作「通过」。"""
    mod = _FakeChecker({"rule_off": "-", "rules": 72})
    rows, errors = mhc.evaluate(mod, [memfile], threshold=500)
    assert rows == []
    assert len(errors) == 1 and "解析不出 rule_off" in errors[0]


def test_checker_exception_is_collected(memfile):
    class Boom:
        DEFAULT_LIMITS: dict = {}

        def check(self, *_a, **_k):
            raise RuntimeError("坏了")

    rows, errors = mhc.evaluate(Boom(), [memfile], threshold=500)
    assert rows == []
    assert len(errors) == 1 and "RuntimeError" in errors[0]


def test_absent_file_is_skipped_not_failed(tmp_path):
    mod = _FakeChecker({"rule_off": 1, "rules": 1})
    rows, errors = mhc.evaluate(mod, [tmp_path / "nope.md"], threshold=500)
    assert errors == []
    assert rows[0]["skipped"] == "文件不存在"


def test_load_checker_returns_none_for_bad_path(tmp_path):
    assert mhc.load_checker(tmp_path / "missing.py") is None


def test_main_returns_2_when_checker_unavailable(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(mhc, "CHECKER", tmp_path / "missing.py")
    monkeypatch.setattr(sys, "argv", ["memory_headroom_check"])
    assert mhc.main() == 2
    assert "不静默放过" in capsys.readouterr().err


def test_main_returns_1_on_low_headroom(monkeypatch, memfile, capsys):
    monkeypatch.setattr(mhc, "TARGETS", [memfile])
    monkeypatch.setattr(mhc, "load_checker", lambda: _FakeChecker({"rule_off": 6900, "rules": 72}))
    monkeypatch.setattr(sys, "argv", ["memory_headroom_check", "--threshold", "500"])
    assert mhc.main() == 1
    assert "可见余量" in capsys.readouterr().out


def test_main_json_mode_reports_rows(monkeypatch, memfile, capsys):
    import json

    monkeypatch.setattr(mhc, "TARGETS", [memfile])
    monkeypatch.setattr(mhc, "load_checker", lambda: _FakeChecker({"rule_off": 100, "rules": 1}))
    monkeypatch.setattr(sys, "argv", ["memory_headroom_check", "--json"])
    assert mhc.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["rows"][0]["headroom"] == mhc.DEFAULT_LIMIT - 100
    assert payload["low_count"] == 0


# ── 接线自检（「已配置 ≠ 被读取」）───────────────────────────────


def _mk_db(tmp_path, rows):
    import sqlite3

    db = tmp_path / "wb.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE automations (name TEXT, prompt TEXT, status TEXT, deleted_at TEXT)")
    con.executemany("INSERT INTO automations VALUES (?,?,?,?)", rows)
    con.commit()
    con.close()
    return db


def test_check_wired_true_when_active_automation_references_script(tmp_path):
    db = _mk_db(tmp_path, [("记忆维护", "run memory_headroom_check.py --threshold 500", "ACTIVE", None)])
    ok, detail = mhc.check_wired(db=db)
    assert ok is True and "记忆维护" in detail


def test_check_wired_false_when_no_reference(tmp_path):
    db = _mk_db(tmp_path, [("别的", "echo hi", "ACTIVE", None)])
    ok, detail = mhc.check_wired(db=db)
    assert ok is False and "没接线" in detail


def test_check_wired_ignores_deleted_and_paused(tmp_path):
    """软删除 / 暂停的引用不算「被读取」——只看 ACTIVE 且未删除。"""
    db = _mk_db(
        tmp_path,
        [
            ("已删", "memory_headroom_check.py", "ACTIVE", "1780000000000"),
            ("已暂停", "memory_headroom_check.py", "PAUSED", None),
        ],
    )
    ok, _ = mhc.check_wired(db=db)
    assert ok is False


def test_check_wired_missing_db_is_not_a_pass(tmp_path):
    ok, detail = mhc.check_wired(db=tmp_path / "nope.db")
    assert ok is False and "不存在" in detail


def test_wired_failure_makes_main_return_2(monkeypatch, memfile, capsys):
    """接线检查失败必须 rc=2（不是 0/1）——判不了就说判不了。"""
    monkeypatch.setattr(mhc, "TARGETS", [memfile])
    monkeypatch.setattr(mhc, "load_checker", lambda: _FakeChecker({"rule_off": 1, "rules": 1}))
    monkeypatch.setattr(mhc, "check_wired", lambda *a, **k: (False, "没接线"))
    monkeypatch.setattr(sys, "argv", ["memory_headroom_check", "--wired", "--threshold", "1"])
    assert mhc.main() == 2
    assert "接线自检: 没接线" in capsys.readouterr().out
