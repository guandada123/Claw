"""test_contract.py — Claw↔QTS 跨项目契约（qts_daily_signals.json）的校验单测。

覆盖：
  - 字段完整性（report_date / generated_at 缺失）
  - 时效性（STALE：generated_at 过期）
  - 格式合法性（generated_at 非法）
  - 时区统一（Asia/Shanghai 归一化 + 边界）
  - 消费者 _load_qts_signals 在 STALE 时置 note
"""

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import signal_consensus as sc

_TZ = ZoneInfo("Asia/Shanghai")


def _base(now_iso=None):
    return {
        "report_date": "2026-07-16",
        "generated_at": now_iso or datetime.now(_TZ).isoformat(),
        "signals": [{"ts_code": "600000.SH"}],
    }


# ============================================================
# 原有用例
# ============================================================


def test_valid_fresh_contract():
    v = sc.validate_qts_signals(_base())
    assert v["ok"] is True
    assert v["stale"] is False
    assert v["signals"] == 1
    assert "✅" in v["msg"]


def test_missing_report_date():
    v = sc.validate_qts_signals({"generated_at": datetime.now().isoformat(), "signals": []})
    assert v["ok"] is False
    assert "report_date" in v["msg"]


def test_missing_generated_at():
    v = sc.validate_qts_signals({"report_date": "2026-07-16", "signals": []})
    assert v["ok"] is False
    assert v["generated_at"] is None


def test_stale_contract_flagged():
    old = (datetime.now() - timedelta(hours=48)).isoformat()
    v = sc.validate_qts_signals(
        {"report_date": "2026-07-14", "generated_at": old, "signals": []}
    )
    assert v["ok"] is False
    assert v["stale"] is True
    assert "STALE" in v["msg"]


def test_bad_generated_at_format():
    v = sc.validate_qts_signals(
        {"report_date": "2026-07-16", "generated_at": "not-a-date", "signals": []}
    )
    assert v["ok"] is False


def test_non_dict_contract():
    v = sc.validate_qts_signals([])
    assert v["ok"] is False


def test_generated_at_with_z_suffix():
    # 兼容 ISO 8601 的 Z 时区后缀
    old = (datetime.now() - timedelta(hours=48)).isoformat().replace("+00:00", "Z")
    v = sc.validate_qts_signals(
        {"report_date": "2026-07-14", "generated_at": old, "signals": []}
    )
    assert v["stale"] is True


def test_load_qts_signals_stale_note(tmp_path, monkeypatch):
    old = (datetime.now() - timedelta(hours=48)).isoformat()
    p = tmp_path / "qts_daily_signals.json"
    p.write_text(
        json.dumps({"report_date": "2026-07-14", "generated_at": old, "signals": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr(sc, "_QTS_SIGNALS", p)
    data = sc._load_qts_signals()
    assert data.get("note") and "STALE" in data["note"]


def test_load_qts_signals_fresh_no_note(tmp_path, monkeypatch):
    p = tmp_path / "qts_daily_signals.json"
    p.write_text(json.dumps(_base()), encoding="utf-8")
    monkeypatch.setattr(sc, "_QTS_SIGNALS", p)
    data = sc._load_qts_signals()
    assert "note" not in data


# ============================================================
# 时区统一（PR-1）新增用例
# ============================================================

def test_boundary_exactly_8h_fresh():
    """恰好 8h 前 → >8h 才 stale，故仍然新鲜
    注意：用 7h59m 避免 `datetime.now(_TZ)` 两次调用间的微秒偏差导致恰好 8h+epsilon。
    """
    gen = (datetime.now(_TZ) - timedelta(hours=7, minutes=59)).isoformat()
    v = sc.validate_qts_signals({
        "report_date": "2026-07-16",
        "generated_at": gen,
        "signals": [],
    })
    assert v["ok"] is True
    assert v["stale"] is False


def test_boundary_8h_minus_1min_fresh():
    """8h-1min 前 → 新鲜"""
    gen = (datetime.now(_TZ) - timedelta(hours=8, minutes=-1)).isoformat()
    v = sc.validate_qts_signals({
        "report_date": "2026-07-16",
        "generated_at": gen,
        "signals": [],
    })
    assert v["ok"] is True
    assert v["stale"] is False


def test_boundary_8h_plus_1min_stale():
    """8h+1min 前 → 过期"""
    gen = (datetime.now(_TZ) - timedelta(hours=8, minutes=1)).isoformat()
    v = sc.validate_qts_signals({
        "report_date": "2026-07-16",
        "generated_at": gen,
        "signals": [],
    })
    assert v["ok"] is False
    assert v["stale"] is True


def test_naive_generated_at_treated_as_cn():
    """无时区的 generated_at 按 Asia/Shanghai 补齐，结果正确"""
    # 模拟 QTS 不带时区信息的输出（纯 iso）
    gen_naive = (datetime.now(_TZ) - timedelta(hours=3)).replace(tzinfo=None).isoformat()
    v = sc.validate_qts_signals({
        "report_date": "2026-07-16",
        "generated_at": gen_naive,
        "signals": [],
    })
    assert v["ok"] is True
    assert v["stale"] is False


def test_utc_generated_at_converted_to_cn():
    """带 UTC 时区的 generated_at → 转换为 Asia/Shanghai 后比较"""
    gen_utc = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    v = sc.validate_qts_signals({
        "report_date": "2026-07-16",
        "generated_at": gen_utc,
        "signals": [],
    })
    assert v["ok"] is True
    assert v["stale"] is False


def test_future_generated_at_handled():
    """未来时间 → 负数 age，应判为新鲜（不抛异常）"""
    gen_future = (datetime.now(_TZ) + timedelta(hours=1)).isoformat()
    v = sc.validate_qts_signals({
        "report_date": "2026-07-16",
        "generated_at": gen_future,
        "signals": [],
    })
    # 负数 age → 不判断为 stale（防御性：不卡未来时间）
    assert v["ok"] is True
    assert v["stale"] is False
