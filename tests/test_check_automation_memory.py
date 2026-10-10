"""check_automation_memory 的守卫测试。

这个检查器的判据被自己的真实数据打回过 **两次**（见脚本 docstring 的迭代记录），
所以测试要钉住的正是那两次教训：

  · 高频档位体积大但「在自身稳态之内」→ **不得报警**（否则就是旧的 30KB 恒真噪音）
  · 休眠档位体积大但不再增长 → **不得报警**（体积是历史遗留，不是问题）
  · 只有「超出归档稳态 1.5 倍」**且**「近窗日增 > 2KB」才算异常
  · 时间戳解析不出 → 显式列出，**不静默当成正常**

全部离线：现造合成 memory 文件。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / ".workbuddy" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import check_automation_memory as cam  # noqa: E402


def _mk(tmp_path: Path, name: str, lines: list[str]) -> Path:
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    f = d / "memory.md"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f


def _entry(days_ago: float, payload_bytes: int = 0, now: datetime | None = None) -> str:
    # 必须相对**真实当下**构造：检查器现在按「现在 - 7 天」分桶，
    # 写死一个过去的 now 会让所有条目都落进基线窗口（测试与实现口径不一致）。
    now = now or datetime.now()
    ts = now - timedelta(days=days_ago)
    return f"{ts.strftime('%Y-%m-%dT%H:%M')} " + "x" * payload_bytes


# ── 三类「不得报警」──────────────────────────────────────────────


def test_high_frequency_slot_within_steady_state_is_not_anomaly(tmp_path):
    """核心回归：高频档位每天涨、体积大，但在自身稳态之内 → 只算高位，不报警。"""
    lines = [_entry(d, 6000) for d in range(20, 0, -1)]  # 20 天，每天 ~6KB 均匀增长
    f = _mk(tmp_path, "slot-hf", lines)
    r = cam.analyze(f)
    assert r["size"] > cam.ABS_FLOOR_BYTES, "前提：体积确实 >30KB"
    assert r["status"] != "anomaly", "在自身稳态之内不该报警"


def test_dormant_slot_is_not_anomaly(tmp_path):
    """休眠档位：体积大但近 7 天没写 → 历史遗留，不是问题。"""
    lines = [_entry(d, 15000) for d in (40, 35, 30, 25, 22)]  # 全在近窗之外，且总量 >30KB
    f = _mk(tmp_path, "slot-dormant", lines)
    r = cam.analyze(f)
    assert r["status"] == "dormant"


def test_small_file_is_ok(tmp_path):
    r = cam.analyze(_mk(tmp_path, "slot-small", [_entry(1, 100), _entry(3, 100)]))
    assert r["status"] == "ok"


# ── 真异常必须被抓住 ──────────────────────────────────────────────


def test_runaway_growth_is_anomaly(tmp_path):
    """体积远超自身稳态（归档压不住）且仍在写 → 必须报警。

    判据本质是「归档窗口有没有在工作」：文件里还留着远超 14 天窗口的旧条目
    （说明归档没裁掉它们），同时它还在被写入 → 会一直涨，值得人看。
    """
    lines = [_entry(60, 20000), _entry(40, 20000)]  # 远超窗口的旧条目（归档没裁）
    lines += [_entry(d, 20000) for d in (5, 4, 3, 2, 1)]  # 且仍在写
    f = _mk(tmp_path, "slot-runaway", lines)
    r = cam.analyze(f)
    assert r["span_days"] > r["span_limit"], f"构造不成立: {r}"
    assert r["status"] == "anomaly", f"应判为异常，实际 {r['status']} / {r}"


def test_anomaly_needs_both_conditions(tmp_path):
    """只满足「超稳态」但不活跃 → 不算异常（休眠分支）。"""
    lines = [_entry(d, 20000) for d in (60, 55, 50)]  # 跨度大但已停写
    f = _mk(tmp_path, "slot-bigold", lines)
    assert cam.analyze(f)["status"] != "anomaly", "停写的档位不该报警（休眠分支）"


# ── 判不了要说出来 ───────────────────────────────────────────────


def test_no_timestamps_is_unknown_not_ok(tmp_path):
    f = _mk(tmp_path, "slot-nots", ["x" * 40000])  # 大文件但无时间戳
    r = cam.analyze(f)
    assert r["status"] == "unknown"
    assert "时间戳" in r["reason"]


def test_unknown_large_file_surfaces_in_scan(tmp_path):
    _mk(tmp_path, "slot-nots", ["x" * 40000])
    rep = cam.scan(tmp_path)
    assert [Path(r["file"]).parent.name for r in rep["unknown"]] == ["slot-nots"]


def test_scan_counts_are_partitioned(tmp_path):
    _mk(tmp_path, "small", [_entry(1, 100), _entry(3, 100)])
    _mk(tmp_path, "nots", ["x" * 40000])
    rep = cam.scan(tmp_path)
    total = len(rep["rows"])
    assert total == 2
    assert len(rep["anomaly"]) + len(rep["saw_high"]) + len(rep["dormant"]) + len(rep["unknown"]) <= total


# ── main 的退出码 ────────────────────────────────────────────────


def test_main_returns_2_on_missing_dir(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "argv", ["cam", "--root", str(tmp_path / "nope")])
    assert cam.main() == 2
    assert "不静默放过" in capsys.readouterr().err


def test_main_returns_0_when_only_saw_high(tmp_path, monkeypatch, capsys):
    _mk(tmp_path, "slot-hf", [_entry(d, 6000) for d in range(20, 0, -1)])
    monkeypatch.setattr(sys, "argv", ["cam", "--root", str(tmp_path)])
    assert cam.main() == 0
    out = capsys.readouterr().out
    assert "无涨速异常" in out or "无真异常" in out or "✅" in out


def test_main_returns_1_on_anomaly(tmp_path, monkeypatch):
    lines = [_entry(60, 20000), _entry(40, 20000)] + [_entry(d, 20000) for d in (5, 4, 3, 2, 1)]
    _mk(tmp_path, "slot-runaway", lines)
    monkeypatch.setattr(sys, "argv", ["cam", "--root", str(tmp_path)])
    assert cam.main() == 1


@pytest.mark.parametrize("pattern_line", ["2026-10-10T09:30 payload", "## 2026-10-10 09:30:00 | x"])
def test_parse_both_timestamp_formats(tmp_path, pattern_line):
    f = _mk(tmp_path, "slot-fmt", [pattern_line, pattern_line])
    assert cam.analyze(f)["entries"] == 2
