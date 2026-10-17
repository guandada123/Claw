"""PMF 慢查询落库哨兵（check_pmf_slow_query_sink）的可证伪测试（2026-10-17）。

背景：slow_queries 表 07-08 起"停更"曾被误判为采集停摆，根因是索引损坏导致
INSERT 全部失败（laravel.log `慢查询持久化失败` 360 条）。该日志错误因此成为
索引再损坏的天然哨兵 —— 本文件验证哨兵的判据本身：绿灯必须能被已知正样本击破。

隔离铁律：runner 全部注入假对象，不 touch 真实 docker / 生产日志。
"""

from __future__ import annotations

import datetime
import importlib.util
import pathlib

SRC = (
    pathlib.Path(__file__).resolve().parent.parent
    / ".workbuddy"
    / ".workbuddy"
    / "scripts"
    / "unified_ops_center.py"
)
# 兼容两种布局：tests/ 位于 Claw 根（scripts 在 Claw/.workbuddy/scripts）
if not SRC.exists():
    SRC = (
        pathlib.Path(__file__).resolve().parent.parent
        / ".workbuddy"
        / "scripts"
        / "unified_ops_center.py"
    )
_spec = importlib.util.spec_from_file_location("uoc_pmf_sink", SRC)
uoc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(uoc)

NOW = datetime.datetime(2026, 10, 17, 9, 0, 0)


def _line(ts: str) -> str:
    return (
        f"[{ts}] production.ERROR: 慢查询持久化失败 "
        '{"error":"SQLSTATE[XX002]: Index corrupted: 7 ERROR: index '
        '\\"slow_queries_captured_at_time_index\\" contains unexpected zero page at block 6"}'
    )


def _runner(stdout: str, returncode: int = 0):
    class R:
        pass

    r = R()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = ""
    return lambda: r


def test_recent_failure_breaks_the_green_light():
    """正样本击破：1 小时前的落库失败必须把绿灯打成红。"""
    log = "\n".join([_line("2026-10-17 08:00:00")] + [f"INFO something {i}" for i in range(5)])
    res = uoc.check_pmf_slow_query_sink(now=NOW, runner=_runner(log))
    assert res["ok"] is False
    assert any("落库失败" in a and "索引损坏" in a for a in res["alerts"])


def test_old_failures_outside_window_stay_green():
    """窗口语义：全部失败都在 10 天前（REINDEX 前的旧账）→ 不告警，note 说明。"""
    log = "\n".join([_line("2026-10-07 09:16:00"), _line("2026-10-07 09:20:00"), "INFO x"])
    res = uoc.check_pmf_slow_query_sink(now=NOW, runner=_runner(log))
    assert res["ok"] is True
    assert "窗口外" in (res.get("note") or "")


def test_runner_exception_is_skip_not_pass():
    """取不到证（docker/exec 异常）→ ⏭ skip，绝不能算通过（假绿灯家族）。"""

    def bad():
        raise RuntimeError("docker daemon 不可达")

    res = uoc.check_pmf_slow_query_sink(now=NOW, runner=bad)
    assert res["ok"] is None
    assert "取不到" in res["note"] or "失败" in res["note"]


def test_nonzero_exit_is_skip():
    res = uoc.check_pmf_slow_query_sink(now=NOW, runner=_runner("", returncode=1))
    assert res["ok"] is None


def test_empty_output_is_skip():
    """空输出无法区分「无错误」与「读不到日志」→ skip。"""
    res = uoc.check_pmf_slow_query_sink(now=NOW, runner=_runner(""))
    assert res["ok"] is None


def test_window_boundary_is_inclusive():
    """恰在 24h 边界上的失败计入窗口（>= cutoff）。"""
    boundary = (NOW - datetime.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    res = uoc.check_pmf_slow_query_sink(now=NOW, runner=_runner(_line(boundary)))
    assert res["ok"] is False


def test_log_without_marker_is_green():
    """日志尾部没有任何落库失败（REINDEX 后的正常态）→ 绿，note 报窗口 0 次。"""
    log = "\n".join([f"[2026-10-17 0{i}:00:00] production.INFO: 正常请求 {i}" for i in range(5)])
    res = uoc.check_pmf_slow_query_sink(now=NOW, runner=_runner(log))
    assert res["ok"] is True
    assert "0 次" in (res.get("note") or "")
