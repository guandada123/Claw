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


# ------------------------------------------------ v1.10.2 自审修复的回归守卫
def test_doc_target_prefers_explicit_field():
    """落地目标必须以**显式字段**为准，不能只靠散文正则猜。

    病根：`references/ 增补 xxx.md`（斜杠后带空格）让旧正则匹配不到 → 候选被误判"无文档目标"。
    """
    # ① 散文里没有连续路径，但显式字段给了 → 必须认出来
    c = _cand(suggestion="在 references/ 增补 realtime-l2-tick.md 记录两个源",
              doc_target="references/realtime-l2-tick.md")
    assert adc.doc_target(c) == "references/realtime-l2-tick.md"

    # ② metric_target_file 里的绝对/家目录写法也要能归一
    c2 = _cand(suggestion="no path here",
               metric_target_file="~/.workbuddy/skills/a-stock-data/references/x.md")
    assert adc.doc_target(c2) == "references/x.md"

    # ③ 两者都没有 → 退回散文正则（保持旧兼容）
    c3 = _cand(suggestion="写 references/fallback.md")
    assert adc.doc_target(c3) == "references/fallback.md"

    # ④ 都没有且散文只有断裂路径 → None（不得臆造目标）
    c4 = _cand(suggestion="在 references/ 增补 xxx.md")
    assert adc.doc_target(c4) is None


def test_select_expands_top_n_drops_per_candidate(skills_dir):
    """过闸但被 top_n 限流的，必须**逐条**在 dropped 里，不能压成一条聚合摘要。

    病根：旧写法丢一条 `(+N 条过闸但被 top_n 限流)` → `dropped_by_code` 记「1 条」，
    读起来像"只挤掉 1 条"，实际挤掉 N 条（聚合值抹平组成变化）。
    """
    reg = {"discovery_candidates": [
        _cand(id=f"disc-20261009-1{i}", target_project="meta", success_metric="m", check_cmd="exit 0")
        for i in range(5)
    ]}
    cfg = {"top_n": 2, "quality_gate": adc.DEFAULT_CFG["quality_gate"]}
    sel, dropped = adc.select(reg, cfg, skills_dir)
    assert len(sel) == 2
    topn = [d for d in dropped if d.get("code") == "top_n"]
    assert len(topn) == 3 and all(d["id"] != "" for d in topn)
    assert not any("条过闸" in str(d.get("id")) for d in dropped)


def test_exclude_dir_parts_filters_third_party():
    """第三方/构建产物必须被排除 —— 它们占配额会把真实项目的痛挤没。

    实测病根：QTS 20 条"痛点"100% 来自 `.venv/.../_pytest`，真实项目 279 个 py 一个没扫到。
    """
    from pathlib import Path

    import project_signal_collect as psc

    third = [
        "/p/.venv/lib/python3.13/site-packages/_pytest/code.py",
        "/p/node_modules/x/y.js",
        "/p/__pycache__/a.py",
        "/p/build/lib/a.py",
        "/p/src/x.egg-info/PKG-INFO",
    ]
    for s in third:
        assert psc._is_excluded(Path(s)), s
    real = ["/p/services/live_pipeline.py", "/p/src/claw/scripts/x.py", "/p/tests/test_a.py"]
    for s in real:
        assert not psc._is_excluded(Path(s)), s


def test_collect_todos_excludes_third_party_and_counts_them(tmp_path):
    """端到端：同一棵树里真实文件与 .venv 都带 TODO，只能回收真实的那条。"""
    import project_signal_collect as psc

    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "real.py").write_text("# TODO: 真项目的痛\ndef f(): pass\n", encoding="utf-8")
    venv = tmp_path / ".venv" / "lib" / "site-packages"
    venv.mkdir(parents=True)
    for i in range(30):
        (venv / f"lib{i}.py").write_text("# XXX: 第三方噪音\ndef g(): pass\n", encoding="utf-8")

    hits, excluded = psc.collect_todos({"todo_globs": [f"{tmp_path}/**/*.py"]}, 120, 20)
    assert len(hits) == 1 and "真项目的痛" in hits[0]["title"]
    assert excluded == 30
    assert not any(".venv" in (h.get("file") or "") for h in hits)


# ── 产物型 JSON 的落盘形态（2026-10-10 补）────────────────────────────
# 背景：CI 的 pre-commit job 挂在 end-of-file-fixer 上，被它改写的正是
# .workbuddy/inspection_hub/discovery_archive/dedup_index.json ——
# 生成方 save_dedup_index 写盘不带尾换行，而 hook 强制「文件以单个换行结尾」
# → 每次生成都被 hook 改写一次（提交被拦 + 无尽 diff）。
# 同族教训已在 sync_claw_to_qts_portfolio.py 上踩过一次，故加守卫。


def test_save_dedup_index_ends_with_exactly_one_newline(tmp_path):
    import json

    from discovery_schema import DEDUP_INDEX_REL, save_dedup_index

    assert save_dedup_index(tmp_path, {"version": 1, "keys": {}}) is True
    raw = (tmp_path / DEDUP_INDEX_REL).read_bytes()
    assert raw.endswith(b"\n"), "必须带尾换行，否则 end-of-file-fixer 会持续改写"
    assert not raw.endswith(b"\n\n"), "且只能有一个"
    assert json.loads(raw.decode("utf-8"))["version"] == 1


def test_save_dedup_index_is_stable_across_repeated_saves(tmp_path):
    """重复保存不得让尾换行累积（否则 hook 依然会改写）。"""
    from discovery_schema import DEDUP_INDEX_REL, save_dedup_index

    for _ in range(3):
        assert save_dedup_index(tmp_path, {"version": 1, "keys": {}}) is True
    raw = (tmp_path / DEDUP_INDEX_REL).read_bytes()
    assert raw.endswith(b"\n") and not raw.endswith(b"\n\n")


def test_save_dedup_index_roundtrips_through_loader(tmp_path):
    from discovery_schema import load_dedup_index, save_dedup_index

    assert save_dedup_index(tmp_path, {"version": 7, "keys": {"k": "v"}}) is True
    got = load_dedup_index(tmp_path)
    assert got["version"] == 7 and got["keys"] == {"k": "v"}
    assert got["updated_at"], "updated_at 应由保存方回写"


def test_save_dedup_index_returns_false_on_unwritable_parent(tmp_path):
    """写失败必须返回 False 而非抛（调用方据此上报，不能静默当成功）。"""
    from discovery_schema import save_dedup_index

    blocker = tmp_path / ".workbuddy"
    blocker.write_text("我是文件不是目录", encoding="utf-8")  # 让 mkdir 失败
    assert save_dedup_index(tmp_path, {"version": 1, "keys": {}}) is False
