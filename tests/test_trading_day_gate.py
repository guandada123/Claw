"""休市闸门（PA-014）测试 — 「非交易日不写盘、不推进计数」。

背景：两个 15 点档脚本（`sector_strength_layer` / `check_disposal_triggers`）的 RRULE
只有 `BYDAY=MO-FR`，**不含 A 股休市日历** → 休市工作日也照跑：
板块层以"新的一天 + 上一交易日的旧数据"生成产物，把 T2「连续 ≥3 个不同日期」灌水。

本文件钉三件事：
  ① `today_is_trading()` 的三态语义（True / False / **None=判不了**）；
  ② 休市日：**连跑都不跑** —— rc=0 且**一个字节都不写**；
  ③ 判不了时**不跳过**（不因日历不可用而停工）—— 与"判不了就说判不了"同口径的另一半：
     判不了也不该**擅自停工**。
"""

from __future__ import annotations

import datetime
import json
import sys
from datetime import date
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / ".workbuddy" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import check_disposal_triggers as cdt  # noqa: E402
import is_trading_day as itd  # noqa: E402
import sector_strength_layer as ssl  # noqa: E402

# ── ① today_is_trading 三态 ─────────────────────────────────────────


@pytest.mark.parametrize(
    "iso, expect",
    [
        ("2026-10-01", False),  # 国庆（在日历里）
        ("2026-10-10", False),  # 周六
        ("2026-10-11", False),  # 周日
        ("2026-10-12", True),  # 周一（交易日）
    ],
)
def test_today_is_trading_known_days(iso, expect):
    d = datetime.date.fromisoformat(iso)
    assert itd.today_is_trading(d=d) is expect


def test_today_is_trading_returns_none_when_calendar_unreadable(tmp_path):
    """**判不了返回 None，且不得让进程退出**。

    关键点：`load_holidays()` 读不到文件时是 `sys.exit(2)`（SystemExit **不是** Exception）——
    直接用它当闸门，会把"判断不了"升级成"停工"。
    """
    assert itd.today_is_trading(path=str(tmp_path / "no_such.json")) is None


# ── ② 休市日：连跑都不跑 ────────────────────────────────────────────


def _force(monkeypatch, value):
    monkeypatch.setattr(itd, "today_is_trading", lambda *a, **k: value)


def _seed_min(tmp_path, monkeypatch):
    """给触发器布置最小可跑数据（复用其测试里的字段名）。"""
    holdings = [
        {"code": "600584", "name": "长电", "shares": 100, "avg_cost": 100.0, "current_price": 60.0}
    ]
    files = {
        "USER_PORTFOLIO": {"holdings": holdings, "summary": {"total_assets": 100000.0}},
        "SECTOR_CACHE": {"600584": "半导体"},
        "SECTOR_STRENGTH": {"report_date": date.today().isoformat(), "sectors": []},
    }
    for name, data in files.items():
        p = tmp_path / f"{name}.json"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(cdt, name, p)
    monkeypatch.setattr(cdt, "RPS_HISTORY", tmp_path / "rps_history.json")
    monkeypatch.setattr(cdt, "BASELINE_FILE", tmp_path / "baseline.json")
    monkeypatch.setattr(sys, "argv", ["check_disposal_triggers.py"])


def test_disposal_skips_on_holiday_and_writes_nothing(tmp_path, monkeypatch):
    _seed_min(tmp_path, monkeypatch)
    _force(monkeypatch, False)
    assert cdt.main() == 0
    assert not (tmp_path / "rps_history.json").exists(), "休市日不得写历史"
    assert not (tmp_path / "baseline.json").exists(), "休市日不得建/改基线"


def test_disposal_does_not_skip_when_calendar_unknown(tmp_path, monkeypatch, capsys):
    """判不了（None）**不跳过** —— 否则日历一丢，整条链路就静默停工了。"""
    _seed_min(tmp_path, monkeypatch)
    _force(monkeypatch, None)
    cdt.main()
    out = capsys.readouterr().out
    assert "无法判定是否休市" in out, f"判不了必须说出来，实际输出={out[:200]}"


def test_sector_layer_skips_on_holiday(tmp_path, monkeypatch, capsys):
    _force(monkeypatch, False)
    monkeypatch.setattr(sys, "argv", ["sector_strength_layer.py"])
    called = {"n": 0}
    monkeypatch.setattr(ssl, "compute", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    assert ssl.main() == 0
    assert called["n"] == 0, "休市日不该去取数/算"
    assert "休市" in capsys.readouterr().out


def test_sector_layer_explicit_date_bypasses_gate(monkeypatch, capsys):
    """`--date` 指定（补跑历史）时**不拦** —— 那是人为指定，不是"今天跑"。"""
    _force(monkeypatch, False)
    monkeypatch.setattr(
        sys, "argv", ["sector_strength_layer.py", "--date", "2026-10-01", "--no-write"]
    )
    monkeypatch.setattr(ssl, "compute", lambda *a, **k: None)  # 让它走到 compute 就够
    ssl.main()
    assert "休市" not in capsys.readouterr().out, "--date 不该被闸门拦"
