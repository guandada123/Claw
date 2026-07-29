"""test_signal_consensus_additional.py — 针对 signal_consensus 的追加边界测试。

覆盖：
- _load_qts_signals 在 error 字段时返回空 signals 并附 note
- compute_consensus 在空输入时返回结构化 summary，且 pairs 为空
"""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import signal_consensus as sc

_TZ = ZoneInfo("Asia/Shanghai")


def test_load_qts_signals_with_error_note(tmp_path, monkeypatch):
    p = tmp_path / "qts_daily_signals.json"
    p.write_text(json.dumps({"error": "fetch failed"}), encoding="utf-8")
    monkeypatch.setattr(sc, "_QTS_SIGNALS", p)
    data = sc._load_qts_signals()
    assert data.get("signals") == []
    assert "拉取失败" in data.get("note", "")


def test_compute_consensus_empty_outputs_summary(tmp_path, monkeypatch):
    # 空的文章信号
    monkeypatch.setattr(sc, "_ARTICLE_SIGNALS", tmp_path / "article_signals.json")
    (tmp_path / "article_signals.json").write_text("[]", encoding="utf-8")

    # 空的 QTS 信号
    monkeypatch.setattr(sc, "_QTS_SIGNALS", tmp_path / "qts_daily_signals.json")
    (tmp_path / "qts_daily_signals.json").write_text(
        json.dumps({"report_date": "2026-07-16", "generated_at": datetime.now(_TZ).isoformat(), "signals": []}),
        encoding="utf-8",
    )

    # 权重默认
    monkeypatch.setattr(sc, "_SOURCE_WEIGHTS", tmp_path / "source_weights.json")
    (tmp_path / "source_weights.json").write_text("{}", encoding="utf-8")

    # 验证：pairs 为空但 summary 结构存在
    monkeypatch.setattr(sc, "_OUTPUT", tmp_path / "signal_consensus.json")
    result = sc.compute_consensus()
    assert result["summary"]["total_pairs"] == 0
    assert result["summary"]["dual_source"] == 0
    assert result["summary"]["gzh_only"] == 0
    assert result["summary"]["qts_only"] == 0


def test_compute_consensus_wf_passed_boosts_weight(tmp_path, monkeypatch):
    """wf_passed=True 的信源权重额外 +30%"""
    monkeypatch.setattr(sc, "_ARTICLE_SIGNALS", tmp_path / "article_signals.json")
    (tmp_path / "article_signals.json").write_text("[]", encoding="utf-8")

    monkeypatch.setattr(sc, "_QTS_SIGNALS", tmp_path / "qts_daily_signals.json")
    (tmp_path / "qts_daily_signals.json").write_text(
        json.dumps({
            "report_date": "2026-07-16",
            "generated_at": datetime.now(_TZ).isoformat(),
            "signals": [{
                "ts_code": "600000.SH",
                "strategy": "COMBO",
                "sharpe": 2.5,
                "wf_stability": 0.85,
                "wf_passed": True,
            }],
        }),
        encoding="utf-8",
    )

    monkeypatch.setattr(sc, "_SOURCE_WEIGHTS", tmp_path / "source_weights.json")
    (tmp_path / "source_weights.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(sc, "_OUTPUT", tmp_path / "signal_consensus.json")
    result = sc.compute_consensus()
    assert result["summary"]["total_pairs"] == 1
    pair = result["pairs"][0]
    # wf_passed=True → QTS_BACKTEST 权重 0.5 * 1.3 = 0.65
    assert pair["qts_signal"] is not None
    assert pair["qts_signal"]["wf_passed"] is True
    assert pair["qts_signal"]["weight"] == pytest.approx(0.65)
