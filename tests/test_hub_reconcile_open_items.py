"""D8 K7 `open_items` 的回归测试（2026-09-28 新增）。

存在理由：审计实测「全部项目待办 ≈1320 行、仅 6% 带期限，且只有 Claw 有机器检查」——
H1 实盘止损就是这样静默逾期 54 天。K7 把「开放决策/待办」纳入每日机器检查。

覆盖 7 个方向（**双向**：既要报，也要在健康时静默）：
  ① 全部带 due/evidence 且未到期 → 静默
  ② 逾期 → 报 open_item_overdue（含逾期天数与 if_no_action）
  ③ 缺 due → 报（与 H1 同型：不会有人再来问它）
  ④ 缺 evidence → 报（核不出来就不该进表）
  ⑤ 项目**未声明** open_items（缺键）→ 报 undeclared（≠ 没有开放项）
  ⑥ 显式声明 `open_items: []` → **不报**（「审计过确实没有」与「没人整理过」必须区分）
  ⑦ status 已完成 → 跳过（不重复报已了结的事）
"""
import datetime
import importlib.util
import pathlib

import pytest

HR = pathlib.Path("/Users/guan/WorkBuddy/Claw/.workbuddy/scripts/hub_reconcile.py")
spec = importlib.util.spec_from_file_location("hr_k7", HR)
hr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hr)

NOW = datetime.datetime(2026, 9, 28, 12, 0)


def run(cs):
    return [x for x in hr.check_cross_project(cs, pathlib.Path("/tmp/x.json"), NOW)
            if x["kind"].startswith("open_item")]


def proj(items):
    return {"active_projects": {"P": {"cwd": "/tmp", "open_items": items}}}


def test_healthy_items_are_silent():
    cs = proj([{"id": "A-1", "what": "w", "owner": "human", "due": "2026-10-12",
                "if_no_action": "keep_as_is", "evidence": "实测 X=1", "status": "open"}])
    assert run(cs) == []


def test_overdue_reported_with_days():
    cs = proj([{"id": "A-2", "what": "容器 TZ", "owner": "human", "due": "2026-09-01",
                "if_no_action": "escalate", "evidence": "docker exec date = UTC", "status": "open"}])
    hits = run(cs)
    assert [h["kind"] for h in hits] == ["open_item_overdue"]
    assert "已逾期 27 天" in hits[0]["detail"]
    assert "escalate" in hits[0]["detail"]


def test_missing_due_reported():
    cs = proj([{"id": "A-3", "what": "w", "owner": "human", "evidence": "实测", "status": "open"}])
    assert [h["kind"] for h in run(cs)] == ["open_item_no_due"]


def test_missing_evidence_reported():
    cs = proj([{"id": "A-4", "what": "w", "owner": "human", "due": "2026-10-12", "status": "open"}])
    assert [h["kind"] for h in run(cs)] == ["open_item_no_evidence"]


def test_undeclared_key_is_reported():
    cs = {"active_projects": {"P": {"cwd": "/tmp"}}}   # 缺 open_items 键
    hits = run(cs)
    assert [h["kind"] for h in hits] == ["open_items_undeclared"]
    assert "从没人整理过" in hits[0]["detail"]


def test_explicit_empty_is_not_reported():
    """缺键 ≠ 空集：显式 `[]` = 「审计过、确实没有」→ 必须静默。"""
    assert run(proj([])) == []


def test_done_status_skipped():
    cs = proj([{"id": "A-5", "what": "w", "owner": "human", "due": "2026-01-01",
                "evidence": "x", "status": "done"}])
    assert run(cs) == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "--no-header", "-p", "no:cacheprovider"]))
