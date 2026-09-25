"""test_hub_reconcile_slot.py — D9 同组槽位配置一致性的回归测试。

为什么值得单独钉住：
  铁律「单 RRULE 禁多 BYHOUR」要求多时刻**拆成多条独立自动化**，而 `automation_update`
  **改不了** model_id / model_is_thinking / expert_id / permission_mode / push_to_wechat
  → 拆槽时新建的那几条只会拿到平台默认值（实测知识库精读三槽：槽1 = hy3+思考+专家，
  槽2/3 = 默认 flash+无专家）。**这是拆分动作的系统性副作用，拆一次复发一次** ——
  所以它必须有机器守卫，而守卫本身也必须有测试（否则守卫静默失效时没人知道）。

本文件按本项目自己的铁律设计用例：
  「错误 ≠ 通过；缺键 ≠ 空集」→ 所以不止测"不一致要报"，还测"退化形态不许静默通过"，
  以及"一致时必须静默"（只测前者会得到一条永远报警、很快被无视的检查）。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_HUB = Path(__file__).resolve().parent.parent / ".workbuddy" / "scripts" / "hub_reconcile.py"


def _load_hub():
    """按文件路径加载（**cwd 无关**）—— 本项目铁律：声明里的路径不许依赖 cwd。"""
    spec = importlib.util.spec_from_file_location("_hub_reconcile_under_test", _HUB)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hub = _load_hub()

GROUP = {
    "id": "g1",
    "ids": ["A1", "A2", "A3"],
    "keys": ["model_id", "model_is_thinking", "expert_id"],
}


def row(model_id="hy3", thinking=1, expert_id="Expert"):
    return {"model_id": model_id, "model_is_thinking": thinking, "expert_id": expert_id}


OK = {"A1": row(), "A2": row(), "A3": row()}


def kinds(findings):
    return [f["kind"] for f in findings]


def test_consistent_group_is_silent():
    """一致 → 必须静默。只测"不一致要报"的检查会退化成永久红灯，很快被无视。"""
    assert hub.check_slot_consistency({"consistency_groups": [GROUP]}, OK, {}) == []


def test_mismatch_reports_one_finding_per_key():
    """三个键各不一致 → **恰好 3 条**（每键一条，差异成员并列）。

    反例：逐成员各报一条会是 6 条噪音 —— 报告粒度要对齐人的决策粒度。
    """
    live = {
        "A1": row(),
        "A2": row(model_id="flash", thinking=0, expert_id=None),
        "A3": row(),
    }
    got = hub.check_slot_consistency({"consistency_groups": [GROUP]}, live, {})
    assert kinds(got) == ["slot_config_mismatch"] * 3
    keys = sorted(f["key"] for f in got)
    assert keys == [
        "g1.expert_id",
        "g1.model_id",
        "g1.model_is_thinking",
    ]
    # 同一条 finding 里应能看到差异成员（而不是把成员拆成多条）
    assert "A2" in got[0]["detail"]


def test_single_key_mismatch_reports_exactly_one():
    live = {"A1": row(), "A2": row(model_id="flash"), "A3": row()}
    got = hub.check_slot_consistency({"consistency_groups": [GROUP]}, live, {})
    assert len(got) == 1
    assert got[0]["key"] == "g1.model_id"


def test_declared_intentional_difference_is_silent():
    """**有意差异也必须声明**：声明之后不再报（同 D5 K3 排除规则那条纪律）。"""
    g = dict(GROUP, keys_intentionally_differing=["model_id", "model_is_thinking", "expert_id"])
    live = {
        "A1": row(),
        "A2": row(model_id="flash", thinking=0, expert_id=None),
        "A3": row(),
    }
    assert hub.check_slot_consistency({"consistency_groups": [g]}, live, {}) == []


@pytest.mark.parametrize(
    ("group", "live", "expect_kind"),
    [
        # 成员 ID 打错 / 已删 → 组不完整，必须报出来（不许当成"少查一个"）
        (
            dict(GROUP, ids=["A1", "A2", "A3", "TYPO"]),
            OK,
            "group_member_missing",
        ),
        # 存活成员 <2 → 谈不上"一致"，报空转（静默返回就是死守卫）
        (dict(GROUP, ids=["A1"]), OK, "group_too_small"),
        # 一个键都没声明 → 这条组永远不会查出东西
        ({"id": "g1", "ids": ["A1", "A2", "A3"], "keys": []}, OK, "group_no_keys"),
    ],
)
def test_degenerate_inputs_must_not_pass_silently(group, live, expect_kind):
    """退化形态不许静默通过 —— 空声明与零漂移在输出上一模一样。"""
    got = hub.check_slot_consistency({"consistency_groups": [group]}, live, {})
    assert expect_kind in kinds(got), f"期望 {expect_kind}，实际 {kinds(got)}"


def test_no_groups_declared_is_noop():
    """没声明任何组 → D9 不产生噪音（未纳管的项目不该被凭空报错）。"""
    assert hub.check_slot_consistency({}, OK, {}) == []
    assert hub.check_slot_consistency({"consistency_groups": []}, OK, {}) == []


def test_short_id_stays_readable_for_both_id_shapes():
    """简报里的 id 必须可区分：`automation-<ts>` 与 UUID 两种形态。

    直接切前 8 位会把 `automation-...` 切成 `automati`（全组同前缀 = 等于没区分）。
    """
    assert hub.short_id("automation-1782137216020") == "auto-178213"
    assert hub.short_id("11a78567-823d-4d13-970b-42ed417e9b5f") == "11a78567"
