"""统一巡检中枢的「死检查」门禁（2026-09-28 立）。

背景：中枢 18 项检查里有 **4 项**在 `except` 分支 `return {"ok": True}` ——
于是「检查自己坏了」与「检查通过」在输出上**完全一样**（同族：09-24「错误 ≠ 通过」、
09-06「异常分支必须 fail-safe 或显式 skip」）。另有一处「自证循环」：
`check_schedule_liveness` 把**中枢自己的锁**算进「今日调度锁」→ 恒 ≥1 → 永远不可能报「调度挂死」。

本文件做两件事：
 ① 静态扫描：任何 `check_*` 的 except 分支都不许返回 `ok=True`（钉住这一类，防复发）；
 ② 行为验证：调度活性检查必须**剔除自身**，并在"只有自己在跑"时真的报警。
"""

import ast
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / ".workbuddy" / "scripts" / "unified_ops_center.py"

OWN_LOCK_LINE = "    🔒 claw_lock_统一巡检中枢_20260928\n"
OTHER_LOCK_LINE = "    🔒 claw_lock_午间选股_20260928\n"


def _load():
    spec = importlib.util.spec_from_file_location("uoc_deadcheck", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_no_check_returns_ok_true_from_except():
    """任何 check_* 的 except 分支都不许返回 ok=True（必须 fail-safe 或显式 skip）。"""
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("check_"):
            continue
        for handler in [n for n in ast.walk(node) if isinstance(n, ast.ExceptHandler)]:
            for ret in [x for x in ast.walk(handler) if isinstance(x, ast.Return)]:
                if not isinstance(ret.value, ast.Dict):
                    continue
                for k, v in zip(ret.value.keys, ret.value.values):
                    if (
                        isinstance(k, ast.Constant)
                        and k.value == "ok"
                        and isinstance(v, ast.Constant)
                        and v.value is True
                    ):
                        bad.append(f"{node.name}:L{ret.lineno}")
    assert not bad, "这些检查的异常分支返回 ok=True（= 死检查）: " + ", ".join(bad)


def test_skip_helper_shape():
    """skip 的约定形态：ok=None（既不是通过也不是失败）+ skipped=True。"""
    mod = _load()
    r = mod._skip("取不到证")
    assert r["ok"] is None
    assert r["skipped"] is True
    assert r["alerts"] == []


def test_schedule_liveness_excludes_own_lock(monkeypatch):
    """自证循环：中枢自身那把锁不能算进「今日调度锁」。"""
    import types

    mod = _load()
    fake = types.SimpleNamespace(
        stdout="调度锁统计: 共 2 个, 今日 2 个\n  每日级锁: 2 个\n"
        + OWN_LOCK_LINE
        + OTHER_LOCK_LINE,
        returncode=0,
    )
    monkeypatch.setattr(mod, "run_cmd", lambda *a, **k: fake)
    res = mod.check_schedule_liveness()
    assert res["today_locks"] == 1, res
    assert res["own_locks"] == 1, res
    assert res["ok"] is True


def test_schedule_liveness_fires_when_only_self_ran(monkeypatch):
    """只有中枢自己在跑 = 其它自动化一个都没完成 → 必须报（旧实现永远报不出来）。"""
    import types

    mod = _load()
    fake = types.SimpleNamespace(stdout="  每日级锁: 1 个\n" + OWN_LOCK_LINE, returncode=0)
    monkeypatch.setattr(mod, "run_cmd", lambda *a, **k: fake)
    res = mod.check_schedule_liveness()
    assert res["ok"] is False, res
    assert res["today_locks"] == 0, res
    assert "调度系统可能整体挂死" in res["alerts"][0]


def test_schedule_liveness_skips_when_list_unparsable(monkeypatch):
    """清单解析不出来 = 取不到证 → skip（ok=None），**不许报成通过**。"""
    import types

    mod = _load()
    fake = types.SimpleNamespace(stdout="命令输出格式变了，这里没有任何锁行\n", returncode=0)
    monkeypatch.setattr(mod, "run_cmd", lambda *a, **k: fake)
    res = mod.check_schedule_liveness()
    assert res["ok"] is None and res["skipped"] is True, res
