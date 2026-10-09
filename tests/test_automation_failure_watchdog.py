"""automation_failure_watchdog：派发对账接入的回归守卫。

守住 2026-10-09 实测踩到的一个 bug：
`not rows`（近窗口无失败）分支里写成 `if d_findings: ... elif d_findings: ...`，
`elif` 是不可达死代码，且 `push()` 没有 `--dry-run` 保护 ——
**dry-run 会真推送**，同时 SUMMARY 自报 `pushed: bool(d_new and not dry_run)` = False。
即「报告说没推、实际推了」，正是最坏的假成功形态。

全部离线：DB 为临时空库，push/状态文件全被替身接管，不发任何网络请求。
"""

from __future__ import annotations

import importlib
import json
import sqlite3

import pytest


def _make_empty_db(path):
    """造一个含 automation_runs/automations 的空库 → 让 watchdog 走 `not rows` 分支。"""
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE automation_runs ("
        " automation_id TEXT, created_at INTEGER, thread_title TEXT, result_success INTEGER)"
    )
    con.execute("CREATE TABLE automations (id TEXT PRIMARY KEY, name TEXT)")
    con.commit()
    con.close()


ZOMBIE = {
    "id": "auto-1786000000000",
    "name": "（已停用的僵尸自动化）",
    "reason": "next_run_at 落在过去，永不派发",
    "last_dispatch": None,
}


@pytest.fixture()
def wd(monkeypatch, tmp_path):
    mod = importlib.import_module("automation_failure_watchdog")
    importlib.reload(mod)

    db = tmp_path / "empty.db"
    _make_empty_db(db)

    monkeypatch.setattr(mod, "DB", db)
    monkeypatch.setattr(mod, "STATE", tmp_path / "alerted.json")
    monkeypatch.setattr(mod, "load_alerted", lambda: set())
    monkeypatch.setattr(mod, "save_alerted", lambda _s: None)

    calls: list[tuple[str, str]] = []

    def fake_push(title, content):
        calls.append((title, content))
        return True

    monkeypatch.setattr(mod, "push", fake_push)
    mod._push_calls = calls  # type: ignore[attr-defined]
    return mod


def _summary(capsys) -> dict:
    out = capsys.readouterr().out
    line = [ln for ln in out.splitlines() if ln.startswith("SUMMARY: ")][-1]
    return json.loads(line[len("SUMMARY: ") :])


def _run(mod, monkeypatch, argv):
    monkeypatch.setattr("sys.argv", ["automation_failure_watchdog"] + argv)
    return mod.main()


def test_no_findings_is_silent(wd, monkeypatch, capsys):
    monkeypatch.setattr(wd, "dispatch_audit", lambda _h: {"zombies": [], "once_uncleaned": []})
    rc = _run(wd, monkeypatch, ["--dry-run"])
    s = _summary(capsys)
    assert rc == 0
    assert wd._push_calls == []
    assert s["pushed"] is False
    assert s["dispatch_findings"] == 0


def test_dry_run_does_not_push_even_with_findings(wd, monkeypatch, capsys):
    """核心回归点：dry-run 必须零推送（原实现会真推送）。"""
    monkeypatch.setattr(
        wd, "dispatch_audit", lambda _h: {"zombies": [dict(ZOMBIE)], "once_uncleaned": []}
    )
    rc = _run(wd, monkeypatch, ["--dry-run"])
    s = _summary(capsys)
    assert rc == 0
    assert wd._push_calls == [], "dry-run 不得调用 push"
    assert s["pushed"] is False
    assert s["dispatch_findings"] == 1
    assert s["dispatch_new"] == 1


def test_live_run_pushes_once_when_findings_present(wd, monkeypatch, capsys):
    monkeypatch.setattr(
        wd, "dispatch_audit", lambda _h: {"zombies": [dict(ZOMBIE)], "once_uncleaned": []}
    )
    rc = _run(wd, monkeypatch, [])
    s = _summary(capsys)
    assert rc == 0
    assert len(wd._push_calls) == 1, "有发现且非 dry-run 应恰好推送一次"
    assert s["pushed"] is True
    assert s["dispatch_findings"] == 1


def test_already_alerted_zombie_is_not_repushed(wd, monkeypatch, capsys):
    """已告警过的同一条不应重复推送（节流靠 alerted 集合）。"""
    monkeypatch.setattr(wd, "load_alerted", lambda: {f"dispatch:{ZOMBIE['id']}"})
    monkeypatch.setattr(
        wd, "dispatch_audit", lambda _h: {"zombies": [dict(ZOMBIE)], "once_uncleaned": []}
    )
    rc = _run(wd, monkeypatch, [])
    s = _summary(capsys)
    assert rc == 0
    assert wd._push_calls == []
    assert s["dispatch_new"] == 0


def test_summary_pushed_never_claims_true_without_actual_push(wd, monkeypatch, capsys):
    """不变量：SUMMARY.pushed 为 True ⟺ 真的调用过 push。"""
    for argv in (["--dry-run"], []):
        monkeypatch.setattr(
            wd, "dispatch_audit", lambda _h: {"zombies": [dict(ZOMBIE)], "once_uncleaned": []}
        )
        wd._push_calls.clear()
        _run(wd, monkeypatch, argv)
        s = _summary(capsys)
        assert s["pushed"] is bool(wd._push_calls), f"argv={argv} 报告与事实不一致"


def test_dispatch_audit_never_raises_on_missing_script(wd, monkeypatch):
    """补位检查无能力拖垮主 watchdog：脚本缺失时返回 {}，不抛。"""
    monkeypatch.setattr(wd, "DISPATCH_AUDIT", wd.ROOT / "nonexistent_dispatch_audit.py")
    assert wd.dispatch_audit(24) == {}
