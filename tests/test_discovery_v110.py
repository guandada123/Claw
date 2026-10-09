"""v1.10 发现回路：新闸门与度量的自测（防静默回归）

覆盖：指标闸/锚点闸、四环排环、去重键、收益到期判定。
全部离线（无网络、无磁盘写入）——registry/项目路径均为临时构造。
"""

from __future__ import annotations

import datetime

import apply_doc_candidates as adc
import discovery_dedup as dd
import impact_recheck as ir
import pytest
import weekly_radar_sort as wrs
from discovery_schema import (
    apply_defaults,
    dedup_key,
    has_metric,
    is_real_project,
    real_project_share,
)

DOC_SUGGESTION = "提炼为 references/lifecycle.md 技能生命周期巡检清单（文档类 auto_apply）"


def _cand(**kw) -> dict:
    base = {
        "id": "disc-20261009-99",
        "skill": "majiu-management",
        "url": "https://example.com/a",
        "value": "high",
        "risk": "low",
        "suggestion": DOC_SUGGESTION,
        "status": "pending",
    }
    base.update(kw)
    return apply_defaults(base)


@pytest.fixture()
def skills_dir(tmp_path):
    (tmp_path / "majiu-management").mkdir()
    return tmp_path


# --------------------------------------------------------------- 闸门（v1.10 核心）
def test_gate_blocks_when_no_target_project(skills_dir):
    c = _cand(target_project="none", success_metric="重复项归零")
    ok, why, code = adc.quality_gate(c, adc.DEFAULT_CFG, skills_dir)
    assert ok is False
    assert code == "target_project"
    assert "优化谁" in why


def test_gate_blocks_when_no_success_metric(skills_dir):
    c = _cand(target_project="meta", success_metric=None, check_cmd=None)
    ok, why, code = adc.quality_gate(c, adc.DEFAULT_CFG, skills_dir)
    assert ok is False
    assert code == "success_metric"
    assert "无法证真" in why


def test_gate_passes_compliant_candidate(skills_dir):
    c = _cand(target_project="meta", success_metric="references/lifecycle.md 新增小节且被 SKILL.md 引用")
    ok, why, code = adc.quality_gate(c, adc.DEFAULT_CFG, skills_dir)
    assert (ok, code) == (True, "pass")


def test_gate_passes_when_only_check_cmd(skills_dir):
    """check_cmd 等价于 success_metric（退出码 0=achieved 的确定性命令）。"""
    c = _cand(target_project="Claw", success_metric=None, check_cmd="pytest -q tests/test_x.py")
    assert has_metric(c) is True
    ok, _, code = adc.quality_gate(c, adc.DEFAULT_CFG, skills_dir)
    assert (ok, code) == (True, "pass")


def test_gate_still_enforces_legacy_filters(skills_dir):
    """旧闸不许被新闸盖掉：status/value/risk/host/doc_target 仍生效。"""
    assert adc.quality_gate(_cand(status="applied", target_project="meta", success_metric="x"),
                            adc.DEFAULT_CFG, skills_dir)[2] == "status"
    assert adc.quality_gate(_cand(value="low", target_project="meta", success_metric="x"),
                            adc.DEFAULT_CFG, skills_dir)[2] == "value"
    assert adc.quality_gate(_cand(risk="medium", target_project="meta", success_metric="x"),
                            adc.DEFAULT_CFG, skills_dir)[2] == "risk"
    assert adc.quality_gate(_cand(skill="no-such-skill", target_project="meta", success_metric="x"),
                            adc.DEFAULT_CFG, skills_dir)[2] == "host"
    assert adc.quality_gate(_cand(suggestion="没有目标文件", target_project="meta", success_metric="x"),
                            adc.DEFAULT_CFG, skills_dir)[2] == "doc_target"


def test_select_reports_blocked_metric(skills_dir):
    """被指标闸拦下必须能单独计数（否则回路静默卡死无人知）。"""
    reg = {
        "discovery_candidates": [
            _cand(id="a", target_project="meta", success_metric=None),
            _cand(id="b", target_project="meta", success_metric=None),
            _cand(id="c", target_project="meta", success_metric="有指标"),
        ]
    }
    sel, dropped = adc.select(reg, adc.DEFAULT_CFG, skills_dir)
    assert [c["id"] for c in sel] == ["c"]
    assert sum(1 for d in dropped if d["code"] == "success_metric") == 2


# --------------------------------------------------------------- 四环排环（v1.10-B）
def test_ring_adopt_requires_real_project_and_metric():
    c = _cand(target_project="Claw", success_metric="回测全样本耗时 < 30s", track="project_signal")
    ring, _ = wrs.assign_ring(c)
    assert ring == "Adopt"


def test_ring_meta_with_metric_is_trial():
    c = _cand(target_project="meta", success_metric="references/x.md 新增小节")
    ring, _ = wrs.assign_ring(c)
    assert ring == "Trial"


def test_ring_assess_when_metric_missing():
    c = _cand(target_project="meta", success_metric=None)
    ring, why = wrs.assign_ring(c)
    assert ring == "Assess"
    assert "无可判定指标" in why


def test_ring_caution_on_risk_or_missing_source():
    assert wrs.assign_ring(_cand(risk="medium", success_metric="x"))[0] == "Caution"
    assert wrs.assign_ring(_cand(url="", success_metric="x"))[0] == "Caution"


def test_ring_movement_direction():
    assert wrs.movement(None, "Assess") == "new"
    assert wrs.movement("Assess", "Adopt") == "promote"
    assert wrs.movement("Adopt", "Caution") == "demote"
    assert wrs.movement("Trial", "Trial") == "flat"


# --------------------------------------------------------------- 去重（v1.10-C）
def test_dedup_key_normalizes_scheme_and_query():
    a = _cand(url="https://Example.com/a?utm=1", suggestion="同一件事")
    b = _cand(url="http://example.com/a", suggestion="同一 件事")
    assert dedup_key(a) == dedup_key(b)


def test_dedup_key_distinguishes_target_project():
    a = _cand(target_project="Claw", suggestion="同一件事")
    b = _cand(target_project="QTS", suggestion="同一件事")
    assert dedup_key(a) != dedup_key(b)


def test_build_index_records_repeat_as_also_seen():
    c1 = _cand(id="d1", status="applied", target_project="meta", suggestion="重复落地的那条")
    c2 = _cand(id="d2", status="applied", target_project="meta", suggestion="重复落地的那条")
    idx = dd.build_index([c1, c2])
    assert len(idx["keys"]) == 1
    ent = next(iter(idx["keys"].values()))
    assert len(ent["also_seen"]) == 1


# --------------------------------------------------------------- 收益回查（v1.10-A）
def _landed(days_ago: int, **kw) -> dict:
    d = (datetime.date.today() - datetime.timedelta(days=days_ago)).isoformat()
    base = {"status": "applied", "impact": None, "landed_at": d, "recheck_after_days": 7}
    base.update(kw)
    return apply_defaults(base)


def test_impact_due_when_past_recheck_window():
    today = datetime.date.today().isoformat()
    due = ir.due_candidates([_landed(8)], today)
    assert len(due) == 1


def test_impact_not_due_before_window_or_when_legacy():
    today = datetime.date.today().isoformat()
    assert ir.due_candidates([_landed(3)], today) == []
    # legacy 未度量（recheck_after_days=None）不制造回查洪峰
    assert ir.due_candidates([_landed(100, impact="unmeasured", recheck_after_days=None)], today) == []
    # 已裁决过的不再到期
    assert ir.due_candidates([_landed(100, impact="achieved")], today) == []


def test_impact_run_check_cmd_semantics():
    """退出码 0 = achieved；非 0 = none；超时 = partial（不许算通过）。"""
    assert ir.run_check_cmd("exit 0", 10)[0] == "achieved"
    assert ir.run_check_cmd("exit 3", 10)[0] == "none"
    assert ir.run_check_cmd("sleep 5", 1)[0] == "partial"


# --------------------------------------------------------------- 口径工具
def test_real_project_share_counts_and_legacy_is_empty():
    cands = [
        _cand(target_project="Claw"),
        _cand(target_project="QTS"),
        _cand(target_project="meta"),
        _cand(target_project="meta"),
    ]
    sh = real_project_share(cands)
    assert (sh["real_project"], sh["meta"], sh["real_project_share"]) == (2, 2, 0.5)
    assert is_real_project("StockInsight") and not is_real_project("meta")
