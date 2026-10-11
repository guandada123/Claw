"""`qts_pmf_health_guard.scan_ci` 的分支判定回归测试（2026-10-11 建立）。

背景（实测的恒真噪音）：该函数按 headBranch 取每分支最近一次 run，非默认分支只有在
**PR 已 MERGED/CLOSED** 时才豁免。但**无 PR 的一次性实验/验证分支**删掉之后，
`gh run list` 里那条 failure 记录**不会随之消失** → 「GitHub CI 红灯 1 个」常驻、
且永久无人能处置（实测来源：锁门禁负向验证分支 `test/lock-gate-negative-20261011`）。

修法：非默认分支 + 无 PR + **分支已从远端删除** → 判为孤儿红灯，不报。
判不了（gh 查询失败）→ **维持原判**，不擅自放过。

全部离线：替身 `run()` 按子命令返回预置输出，不真跑 gh。
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

SRC = (
    pathlib.Path(__file__).resolve().parent.parent
    / ".workbuddy"
    / "scripts"
    / "qts_pmf_health_guard.py"
)
_spec = importlib.util.spec_from_file_location("qts_guard", SRC)
g = importlib.util.module_from_spec(_spec)
sys.modules["qts_guard"] = g
_spec.loader.exec_module(g)


def _runs(*rows: dict) -> str:
    return json.dumps(
        [
            {
                "status": "completed",
                "conclusion": r.get("conclusion", "success"),
                "workflowName": r.get("wf", "CI"),
                "url": "https://example/run/1",
                "createdAt": "2026-10-11T00:00:00Z",
                "headBranch": r["branch"],
            }
            for r in rows
        ]
    )


def _patch(monkeypatch, runs_json: str, pr_state="UNKNOWN", branch_exists=True):
    """替身 run：按子命令分派。branch_exists=None 表示 gh 查询失败（判不了）。"""

    def _run(cmd, timeout=60):
        if "run" in cmd and "list" in cmd:
            return 0, runs_json, ""
        if "pr" in cmd and "list" in cmd:
            if pr_state == "NO_PR":
                return 0, "[]", ""
            return 0, json.dumps([{"state": pr_state}]), ""
        if "api" in cmd and any("branches" in str(c) for c in cmd):
            if branch_exists is None:
                return 1, "", "gh: network error"
            # True = 被查的分支仍在远端（列出全部测试用分支名）；False = 只剩 main（= 已删）
            names = "main\n"
            if branch_exists:
                names += "feat/x\nfeat/wip\ntest/lock-gate-negative-20261011\n"
            return 0, names, ""
        return 0, "", ""

    monkeypatch.setattr(g, "run", _run)


def test_default_branch_failure_is_reported(monkeypatch):
    _patch(monkeypatch, _runs({"branch": "main", "conclusion": "failure"}))
    reds, _ = g.scan_ci("QuantTradingSystem")
    assert len(reds) == 1 and reds[0]["branch"] == "main"


def test_merged_pr_branch_red_is_skipped(monkeypatch):
    """既有行为不许被新逻辑破坏：PR 已合并的分支红灯是历史。"""
    _patch(
        monkeypatch,
        _runs({"branch": "feat/x", "conclusion": "failure"}),
        pr_state="MERGED",
        branch_exists=True,
    )
    assert g.scan_ci("QuantTradingSystem")[0] == []


def test_orphan_branch_red_is_skipped(monkeypatch):
    """本次修复：无 PR + 分支已从远端删除 → 孤儿红灯，不报。"""
    _patch(
        monkeypatch,
        _runs({"branch": "test/lock-gate-negative-20261011", "conclusion": "failure"}),
        pr_state="NO_PR",
        branch_exists=False,
    )
    assert g.scan_ci("QuantTradingSystem")[0] == []


def test_existing_non_pr_branch_red_is_still_reported(monkeypatch):
    """反向对照：分支还在、只是没开 PR → 仍须报（不许被新逻辑一并放过）。"""
    _patch(
        monkeypatch,
        _runs({"branch": "feat/wip", "conclusion": "failure"}),
        pr_state="NO_PR",
        branch_exists=True,
    )
    assert len(g.scan_ci("QuantTradingSystem")[0]) == 1


def test_branch_query_failure_keeps_reporting(monkeypatch):
    """判不了就不放行：gh 查询失败（None）→ 维持原判。"""
    _patch(
        monkeypatch,
        _runs({"branch": "feat/wip", "conclusion": "failure"}),
        pr_state="NO_PR",
        branch_exists=None,
    )
    assert len(g.scan_ci("QuantTradingSystem")[0]) == 1


def test_later_success_supersedes_earlier_failure(monkeypatch):
    """每分支只取最近一次；后续 success 覆盖历史 failure。"""
    _patch(
        monkeypatch,
        _runs(
            {"branch": "main", "conclusion": "success"},
            {"branch": "main", "conclusion": "failure"},
        ),
    )
    assert g.scan_ci("QuantTradingSystem")[0] == []


@pytest.mark.parametrize("concl", ["failure", "cancelled", "timed_out", "stale"])
def test_failure_like_conclusions_are_all_red(monkeypatch, concl):
    _patch(monkeypatch, _runs({"branch": "main", "conclusion": concl}))
    assert len(g.scan_ci("QuantTradingSystem")[0]) == 1
