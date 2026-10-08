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


def _log_line_start(ts_ms: int, run_id: str) -> str:
    return f"2026-10-08T00:00:00.000Z [INFO] run start: id={run_id}, name=X, nextRunAt=0, startedAt={ts_ms}\n"


def _log_line_finish(ts_ms: int, run_id: str) -> str:
    return (
        "2026-10-08T00:00:00.000Z [INFO] run finished: "
        f"id={run_id}, name=X, success=True, finishedAt={ts_ms}\n"
    )


def test_scheduler_inflight_ignores_historical_unmatched_starts(monkeypatch, tmp_path):
    """「在飞」不许把历史上未配对的 start 一直累加（2026-10-08 口径修正的回归测试）。

    病根：对**整个文件**做 +1/-1 扫描线，而某些 run 的 `finished` 不会落（中断/取消/轮转）→
    未配对 start 永久累加，「在飞」只增不减（实测爬到 456 而真实在飞是 0），告警每轮必报。
    要求：只在回看窗口内统计；窗口外的未配对 start 单独计数（stale_unmatched），不掩盖也不冒充在飞。
    """
    import datetime

    mod = _load()
    now_ms = int(datetime.datetime.now().timestamp() * 1000)
    hour = 3600 * 1000

    lines = ["2026-10-08T00:00:00.000Z [INFO] [LocalAutomationScheduler] started x concurrency=3\n"]
    # 窗口外：5 条 start 全部没有对应 finished（模拟历史残留）
    for i in range(5):
        lines.append(
            _log_line_start(
                now_ms - (mod.SCHED_INFLIGHT_WINDOW_H + 100) * hour - i * 1000, f"old{i}"
            )
        )
    # 窗口内：4 条 start / 4 条 finished，峰值并发 2
    base = now_ms - 2 * hour
    for i in range(2):
        lines.append(_log_line_start(base + i * 60000, f"new{i}"))
    lines.append(_log_line_finish(base + 30 * 60000, "new0"))
    lines.append(_log_line_finish(base + 90 * 60000, "new1"))
    for i in range(2):
        lines.append(_log_line_start(base + (2 + i) * hour, f"new{2 + i}"))
        lines.append(_log_line_finish(base + (2 + i) * hour + 60000, f"new{2 + i}"))

    log = tmp_path / "automation.log"
    log.write_text("".join(lines), encoding="utf-8")
    monkeypatch.setattr(mod, "SCHED_LOG", log)

    sig = mod._scheduler_log_signals()
    assert sig is not None
    assert sig["current"] == 0, f"窗口内收支平衡 → 在飞应为 0，实际 {sig['current']}"
    assert sig["peak"] == 2, f"窗口内真实峰值并发应为 2，实际 {sig['peak']}"
    assert sig["stale_unmatched"] == 5, (
        f"窗口外未配对 start 应计 5 条，实际 {sig['stale_unmatched']}"
    )
    assert sig["declared"] == 3
    # 关键：不许把 5 条历史残留算进在飞 —— 全文件净差 = 9 start − 4 finished = 5，
    # 旧实现会得 5（已逼近阈值 3×2=6），新实现必须是 0。
    assert sig["current"] != 5, "仍在用全文件净差当在飞（回归）"
