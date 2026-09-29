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

# 相对定位（不要写死本机绝对路径 —— CI runner 上没有 /Users/guan，collection 直接报错）
HR = pathlib.Path(__file__).resolve().parent.parent / ".workbuddy" / "scripts" / "hub_reconcile.py"
spec = importlib.util.spec_from_file_location("hr_k7", HR)
hr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hr)

NOW = datetime.datetime(2026, 9, 28, 12, 0)

# 下面两个路径都是**惰性占位**，不写 /tmp 字面量（S108 的意图是防可预测临时路径被利用，
# 这里根本没有临时文件）：
#   · CS_PATH 只被 check_cross_project 用于文案格式化（内部仅 str()，从不读写）；
#   · CWD 用本仓 tests/ 这个**真实存在**的目录 —— D8 有「声明 cwd 不存在」的判据(K1)，
#     换成不存在的假路径会把 K1 引进来，属于换错测试对象。
_TESTS_DIR = pathlib.Path(__file__).resolve().parent
CS_PATH = _TESTS_DIR / "cross_project_state.fixture.json"
CWD = str(_TESTS_DIR)


def run(cs):
    """只挑与 open_items 相关的判据（含静音相关的那条）。"""
    return [x for x in hr.check_cross_project(cs, CS_PATH, NOW)
            if x["kind"].startswith(("open_item", "no_reminder"))]


def proj(items):
    return {"active_projects": {"P": {"cwd": CWD, "open_items": items}}}


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
    cs = {"active_projects": {"P": {"cwd": CWD}}}   # 缺 open_items 键
    hits = run(cs)
    assert [h["kind"] for h in hits] == ["open_items_undeclared"]
    assert "从没人整理过" in hits[0]["detail"]


def test_explicit_empty_is_not_reported():
    """缺键 ≠ 空集：显式 `[]` = 「审计过、确实没有」→ 必须静默。"""
    assert run(proj([])) == []


def _handoff(items):
    return {"handoff": {"items": items}, "active_projects": {"P": {"cwd": CWD, "open_items": []}}}


def test_k5_silenced_with_reason_is_silent():
    """用户说「不用提醒」→ 条目**保留**但不再报（删掉会让账本说谎）。"""
    cs = _handoff([{"id": "H1", "what": "实盘止损", "owner": "human", "due": "2026-08-05",
                    "status": "overdue_unconfirmed", "no_reminder": True,
                    "no_reminder_reason": "用户 2026-09-28：实盘止损不用提醒"}])
    assert [x for x in hr.check_cross_project(cs, CS_PATH, NOW)
            if x["kind"].startswith(("handoff", "no_reminder"))] == []


def test_k5_silence_without_reason_does_not_silence():
    """**缺理由 = 静音不生效**（告警照发）+ 报 `no_reminder_without_reason`。

    语义刻意保守：**一个写坏的静音，绝不能把告警吞掉** —— 否则只要拼错字段名/漏填理由，
    就能无声地绕过全部检查，那这道守卫就成了后门本身。
    """
    cs = _handoff([{"id": "H1", "what": "实盘止损", "owner": "human", "due": "2026-08-05",
                    "status": "overdue_unconfirmed", "no_reminder": True}])
    kinds = [x["kind"] for x in hr.check_cross_project(cs, CS_PATH, NOW)]
    assert "no_reminder_without_reason" in kinds
    assert "handoff_overdue" in kinds, "静音没写理由 → 不该生效，告警必须照发"


def test_k7_silenced_with_reason_is_silent():
    cs = proj([{"id": "A-9", "what": "w", "owner": "human", "due": "2026-01-01",
                "evidence": "实测", "status": "open", "no_reminder": True,
                "no_reminder_reason": "用户明确不催"}])
    assert run(cs) == []


def test_k7_silence_without_reason_does_not_silence():
    """同 K5：缺理由 → 静音不生效，逾期照报 + 额外报缺理由。"""
    cs = proj([{"id": "A-9", "what": "w", "owner": "human", "due": "2026-01-01",
                "evidence": "实测", "status": "open", "no_reminder": True}])
    kinds = sorted(h["kind"] for h in run(cs))
    assert kinds == ["no_reminder_without_reason", "open_item_overdue"]


def test_done_status_skipped():
    cs = proj([{"id": "A-5", "what": "w", "owner": "human", "due": "2026-01-01",
                "evidence": "x", "status": "done"}])
    assert run(cs) == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "--no-header", "-p", "no:cacheprovider"]))
