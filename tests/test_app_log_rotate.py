"""app_log_rotate 测试 — 日志/追踪归档的**安全边界**（P2-2，2026-10-11）。

这套脚本会真的删数据（归档后删源），所以边界必须被钉死：
  ① 只碰 `logs/<YYYY-MM-DD>/` 与 `traces/**/trace_*.json`；
  ② **绝不**碰当天/保留期内的目录、**绝不**碰 `logs/*.log`（automation.log 是调度取证真源）；
  ③ 名字解析不出来的目录一律不处理（宁少勿多）；
  ④ 归档函数自身**不删源**，只有调用方在校验通过后才删；
  ⑤ 默认 dry-run。
"""

from __future__ import annotations

import datetime as dt
import os
import sys
import tarfile
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / ".workbuddy" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import app_log_rotate as alr  # noqa: E402


@pytest.fixture
def fake_tree(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    traces = tmp_path / "traces"
    (logs / "_archive").mkdir(parents=True)
    for name in ("2026-10-01", "2026-10-10", "2026-10-11", "not-a-date"):
        (logs / name).mkdir()
        (logs / name / "app.log").write_text("x" * 10, encoding="utf-8")
    (logs / "automation.log").write_text("live", encoding="utf-8")  # 顶层日志：绝不可动
    (traces / "111").mkdir(parents=True)
    old = traces / "111" / "trace_old.json"
    old.write_text("{}", encoding="utf-8")
    new = traces / "222"
    new.mkdir()
    (new / "trace_new.json").write_text("{}", encoding="utf-8")
    old_ts = time.time() - 30 * 86400
    os.utime(old, (old_ts, old_ts))
    monkeypatch.setattr(alr, "LOGS", logs)
    monkeypatch.setattr(alr, "TRACES", traces)
    monkeypatch.setattr(alr, "ARCHIVE", logs / "_archive")
    return logs, traces


TODAY = dt.date(2026, 10, 11)


def test_expired_dirs_only_old_and_date_named(fake_tree):
    logs, _ = fake_tree
    got = {d.name for d in alr.expired_log_dirs(7, TODAY)}
    assert got == {"2026-10-01"}, f"只应归档过期且日期可解析的目录，实际 {got}"


def test_cutoff_day_is_kept(fake_tree):
    """恰好在保留期的第一天**不该**被归档（边界：< cutoff 才算过期）。"""
    logs, _ = fake_tree
    (logs / "2026-10-04").mkdir()  # TODAY-7
    got = {d.name for d in alr.expired_log_dirs(7, TODAY)}
    assert "2026-10-04" not in got


def test_archive_dir_never_treated_as_expired(fake_tree):
    logs, _ = fake_tree
    assert "_archive" not in {d.name for d in alr.expired_log_dirs(0, TODAY)}


def test_trace_files_by_mtime(fake_tree):
    _, traces = fake_tree
    got = [p.name for p in alr.expired_trace_files(7, TODAY)]
    assert got == ["trace_old.json"], f"应按 mtime 选过期 trace，实际 {got}"


def test_archive_function_does_not_delete_source(fake_tree):
    """归档函数只写归档、**不删源**（删源由调用方在校验后执行）。"""
    logs, _ = fake_tree
    dirs = alr.expired_log_dirs(7, TODAY)
    ok, n, target = alr.archive_paths(dirs, logs, "t_apply", apply=True)
    assert ok and n == 1
    with tarfile.open(target, "r:gz") as tar:
        assert tar.getmembers(), "归档应可被打开"
    assert (logs / "2026-10-01").exists(), "归档函数不得删除源目录"


def test_dry_run_writes_nothing(fake_tree):
    logs, _ = fake_tree
    ok, _, _ = alr.archive_paths(alr.expired_log_dirs(7, TODAY), logs, "t_dry", apply=False)
    assert ok
    assert not list((logs / "_archive").glob("*.tar.gz")), "dry-run 不得产生归档"


def test_top_level_log_files_never_selected(fake_tree):
    """回归：`logs/*.log`（含 automation.log）是调度取证真源，任何情况下不得被选中。"""
    logs, _ = fake_tree
    selected = {p.name for p in alr.expired_log_dirs(0, TODAY)}  # keep_days=0 最激进
    assert "automation.log" not in selected
