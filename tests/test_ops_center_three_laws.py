"""`check_three_laws` 的回归测试（2026-10-12 接入三律巡检时建立）。

三律检查靠**外部脚本**取证，故有三条必须钉死的性质（每条都对应一类真实事故）：

  ① **取不到证 ≠ 通过**：脚本缺失 / 输出非 JSON / 形状意外 → `_skip()`（ok=None + skipped），
     不许渲染成 ✅。同族：`test_ops_center_dead_checks.py`（死检查）、
     `test_no_check_claims_pass_when_every_command_fails`（全命令失败时不得报绿）。
  ② **P2 不得进告警**：本职推送 / 写盘是交付物本身，不是越界；若进 alerts，
     12 条本职推送会变成"每小时推一张卡片"（频率不匹配可处置性）。
  ③ **必须真的注册**：「已实现但没人调 = 从不执行」是本项目反复踩的坑。

隔离：不外呼、不读生产 DB（run_cmd 被替换）；不写任何状态文件。
本文件不得出现本机绝对路径字面量（见 test_ci_hermeticity.py）。
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

SRC = (
    pathlib.Path(__file__).resolve().parent.parent
    / ".workbuddy"
    / "scripts"
    / "unified_ops_center.py"
)
# 一个"确实存在"的文件，用作 THREE_LAWS_SCRIPT 的替身（让检查走到 run_cmd 那一步）
EXISTING = pathlib.Path(__file__).resolve()


def _load():
    spec = importlib.util.spec_from_file_location("uoc_three_laws", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _report(*, termination=None, ownership=None, autonomy=None) -> dict:
    return {
        "termination": termination or {"confirmed": [], "candidates": []},
        "ownership": ownership or {"candidates": []},
        "autonomy": autonomy or {"p1": [], "p2": [], "gated_disposal": 0},
    }


def _stub_run_cmd(payload: str):
    """替换 run_cmd：返回一个带 stdout 的假 CompletedProcess。"""

    class _R:
        returncode = 0
        stdout = payload
        stderr = ""

    return lambda *a, **k: _R()


def _patch(monkeypatch, mod, payload: str):
    monkeypatch.setattr(mod, "THREE_LAWS_SCRIPT", EXISTING)
    monkeypatch.setattr(mod, "run_cmd", _stub_run_cmd(payload))


# ════════════════════ A. 注册（"已实现但没人调"）════════════════════


def test_registered_in_main_flow():
    """注册了但没在主流程 checks 字典里调用 = 从不执行。"""
    src = SRC.read_text(encoding="utf-8")
    assert '"三律合规": check_three_laws()' in src


# ════════════════════ B. 取不到证 → 显式 skip ════════════════════


def test_missing_script_is_explicit_skip(monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "THREE_LAWS_SCRIPT", pathlib.Path("no-such-three-laws-audit.py"))
    r = mod.check_three_laws()
    assert r["ok"] is None, r
    assert r["skipped"] is True, r
    assert r["alerts"] == [], r


def test_non_json_output_is_explicit_skip(monkeypatch):
    mod = _load()
    _patch(monkeypatch, mod, "这不是 JSON")
    r = mod.check_three_laws()
    assert r["ok"] is None and r["skipped"] is True, r


def test_unexpected_shape_is_explicit_skip(monkeypatch):
    """合法 JSON 但不是 dict（如数组）→ 同样按取不到证，且绝不让中枢崩。"""
    mod = _load()
    _patch(monkeypatch, mod, json.dumps([1, 2, 3]))
    r = mod.check_three_laws()
    assert r["ok"] is None and r["skipped"] is True, r


def test_command_failure_is_explicit_skip(monkeypatch):
    """子进程以非 0 退出且无输出 → 取不到证，不许报绿。"""
    mod = _load()
    monkeypatch.setattr(mod, "THREE_LAWS_SCRIPT", EXISTING)
    monkeypatch.setattr(mod, "run_cmd", _stub_run_cmd(""))
    r = mod.check_three_laws()
    assert r["ok"] is None and r["skipped"] is True, r


# ════════════════════ C. 结论映射（P1 进告警 / P2 只进 note）════════════════════


def test_clean_report_is_ok_and_p2_not_alerted(monkeypatch):
    """P2（本职推送 7 项）只进 note，**不得**产生告警，否则每小时推卡片。"""
    mod = _load()
    _patch(
        monkeypatch,
        mod,
        json.dumps(
            _report(autonomy={"p1": [], "p2": [{"name": f"本职{i}"} for i in range(7)], "gated_disposal": 3})
        ),
    )
    r = mod.check_three_laws()
    assert r["ok"] is True, r
    assert r["alerts"] == [], f"P2 不得进告警: {r['alerts']}"
    assert "P2本职产出 7" in (r["note"] or ""), r
    assert "有门禁 3" in (r["note"] or ""), r


def test_p1_disposal_becomes_alert(monkeypatch):
    mod = _load()
    _patch(
        monkeypatch,
        mod,
        json.dumps(
            _report(
                autonomy={
                    "p1": [{"name": "越界清理", "detail": "自主处置动作 删除 且未见门禁声明"}],
                    "p2": [],
                    "gated_disposal": 0,
                }
            )
        ),
    )
    r = mod.check_three_laws()
    assert r["ok"] is False, r
    assert any("越界清理" in a for a in r["alerts"]), r


def test_termination_confirmed_becomes_alert(monkeypatch):
    mod = _load()
    _patch(
        monkeypatch,
        mod,
        json.dumps(
            _report(
                termination={
                    "confirmed": [
                        {"check": "运行态卡死", "id": "a1", "detail": "running=1 已 200 分钟"}
                    ],
                    "candidates": [],
                }
            )
        ),
    )
    r = mod.check_three_laws()
    assert r["ok"] is False, r
    assert any("运行态卡死" in a for a in r["alerts"]), r


def test_ownership_candidate_becomes_alert(monkeypatch):
    mod = _load()
    _patch(
        monkeypatch,
        mod,
        json.dumps(
            _report(ownership={"candidates": [{"path": "out.json", "writers": {"甲": {}, "乙": {}}}]})
        ),
    )
    r = mod.check_three_laws()
    assert r["ok"] is False, r
    assert any("out.json" in a and "2 个写入者" in a for a in r["alerts"]), r
