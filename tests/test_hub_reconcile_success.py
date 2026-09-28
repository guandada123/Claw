"""test_hub_reconcile_success.py — D4'「跑了但从未成功」的回归测试。

为什么值得单独钉住（2026-09-25 实测）：
  `💓 中枢存活看门狗` 连跑 **48 次、0 次成功**（cwds 双重 JSON 编码 → 工作目录解析成不存在的路径），
  而它在 registry 里是已声明的 ACTIVE 项、`last_run` 每 2 小时都在刷新
  → **旧 D4 只看"最近跑没跑"，于是判它健康、静默通过**。

  这是「看着在跑、其实没在跑」在检查器身上的形态：**判据测错了东西**（测存活，不测产出）。

本文件按两个方向验证（只测"失败要报"会得到一条永久红灯）：
  · 全失败 → 必须报
  · 有成功 → 必须静默
  · 样本不足（只跑过 1~2 次）→ 必须静默（单次失败是常态，不刷屏）
  · 从来没跑过 → 必须静默（那是"没跑"的问题，归 D4 心跳，不归这里）
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

_HUB = Path(__file__).resolve().parent.parent / ".workbuddy" / "scripts" / "hub_reconcile.py"
_TMP = Path("/tmp/claw_d4_success_test")


def _mkdb(rows: list[tuple[bool, str | None]]) -> Path:
    """造一个只有一条自动化 A1 的调度库；rows = 最近若干次运行（新→旧）。"""
    _TMP.mkdir(exist_ok=True)
    db = _TMP / "sched.db"
    db.unlink(missing_ok=True)
    c = sqlite3.connect(db)
    c.execute("""create table automations(
        id text primary key, name text, rrule text, status text, deleted_at integer,
        created_at integer, model_id text, model_is_thinking integer, expert_id text,
        permission_mode text, push_to_wechat integer)""")
    c.execute("create table automation_runs(automation_id text, created_at integer,"
              " result_success integer, failure_code text)")
    now = int(time.time() * 1000)
    c.execute("insert into automations values(?,?,?,?,?,?,?,?,?,?,?)",
              ("A1", "[测]全失败", "FREQ=HOURLY;INTERVAL=2", "ACTIVE", None, now,
               None, None, None, None, 0))
    for k, (ok, code) in enumerate(rows):          # k=0 最新
        c.execute("insert into automation_runs values(?,?,?,?)",
                  ("A1", now - k * 3600 * 1000, 1 if ok else 0, code))
    c.commit()
    c.close()
    return db


def _registry(path: Path) -> Path:
    reg = {
        "version": "9.9.9",
        "automation_scope": {"include_name_patterns": ["^\\[测\\]"], "exclude_name_patterns": []},
        "automations": [{"id": "A1", "name": "[测]全失败",
                         "rrule": "FREQ=HOURLY;INTERVAL=2", "status": "ACTIVE"}],
        "doc_contract": {"docs": []},
        "pending_actions": [],
        "autonomy_methods": {"ladder": ["observe"]},
        "doc_apply": {"auto_switch": False},
    }
    path.write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
    return path


def _run(rows) -> list[dict]:
    db = _mkdb(rows)
    rp = _registry(_TMP / "reg.json")
    p = subprocess.run(
        [sys.executable, str(_HUB), "--json", "--no-doc",
         "--registry", str(rp), "--db", str(db),
         "--cross-state", str(_TMP / "no_such_cs.json")],
        capture_output=True, text=True)
    d = json.loads(p.stdout)
    return [x for x in d.get("D4_heartbeat_stale", []) if x.get("kind") == "never_succeeds"]


def test_all_failures_are_reported():
    got = _run([(False, "automation-workspace-unavailable")] * 5)
    assert len(got) == 1
    assert got[0]["runs"] == 3                      # 只看最近 3 次
    assert got[0]["failure_codes"] == ["automation-workspace-unavailable"]
    assert "一次都没成功" in got[0]["detail"]


def test_one_success_makes_it_silent():
    """最近 3 次里有 1 次成功 → 不算「从未成功」→ 必须静默（否则是永久红灯）。"""
    assert _run([(False, "x"), (True, None), (False, "x")]) == []


def test_two_runs_is_not_enough_evidence():
    """只跑过 2 次全失败 → 样本不足，不报（单次失败是常态）。"""
    assert _run([(False, "x"), (False, "x")]) == []


def test_no_runs_at_all_is_not_this_check():
    """从未运行 → 归 D4 心跳（该跑没跑），不归「从未成功」→ 这里必须静默。"""
    assert _run([]) == []


def test_all_success_is_silent():
    assert _run([(True, None)] * 6) == []
