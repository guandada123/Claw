"""test_contract.py — Claw↔QTS 跨项目契约（qts_daily_signals.json）的校验单测。

覆盖：
  - 字段完整性（report_date / generated_at 缺失）
  - 时效性（STALE：generated_at 过期）
  - 格式合法性（generated_at 非法）
  - 时区统一（Asia/Shanghai 归一化 + 边界）
  - 消费者 _load_qts_signals 在 STALE 时置 note
  - 数据本体时效（report_date 断更 → report_stale，2026-09-03 新增）

⚠️ report_date 一律用 _fresh_date() 动态生成，禁止硬编码日期字面量。
   原因（2026-09-03 实证）：本文件曾把 "2026-07-16" 当无意义填充值写死，
   在 report_date 纳入校验后这批「测 generated_at 的用例」集体误红 9 例 ——
   硬编码日期是随时间失效的定时炸弹。测哪个维度，就只固定那个维度。
"""

import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import signal_consensus as sc

_TZ = ZoneInfo("Asia/Shanghai")


def _fresh_date(days_ago: int = 0) -> str:
    """当日（或 N 天前）日期串，用于隔离 report_date 维度的干扰。"""
    return (date.today() - timedelta(days=days_ago)).isoformat()


def _base(now_iso=None):
    return {
        "report_date": _fresh_date(),
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
    v = sc.validate_qts_signals({"report_date": _fresh_date(), "signals": []})
    assert v["ok"] is False
    assert v["generated_at"] is None


def test_stale_contract_flagged():
    old = (datetime.now() - timedelta(hours=48)).isoformat()
    v = sc.validate_qts_signals(
        {"report_date": _fresh_date(), "generated_at": old, "signals": []}
    )
    assert v["ok"] is False
    assert v["stale"] is True
    assert "STALE" in v["msg"]


def test_bad_generated_at_format():
    v = sc.validate_qts_signals(
        {"report_date": _fresh_date(), "generated_at": "not-a-date", "signals": []}
    )
    assert v["ok"] is False


def test_non_dict_contract():
    v = sc.validate_qts_signals([])
    assert v["ok"] is False


def test_generated_at_with_z_suffix():
    # 兼容 ISO 8601 的 Z 时区后缀
    old = (datetime.now() - timedelta(hours=48)).isoformat().replace("+00:00", "Z")
    v = sc.validate_qts_signals(
        {"report_date": _fresh_date(), "generated_at": old, "signals": []}
    )
    assert v["stale"] is True


def test_load_qts_signals_stale_note(tmp_path, monkeypatch):
    old = (datetime.now() - timedelta(hours=48)).isoformat()
    p = tmp_path / "qts_daily_signals.json"
    p.write_text(
        json.dumps({"report_date": _fresh_date(), "generated_at": old, "signals": []}),
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
        "report_date": _fresh_date(),
        "generated_at": gen,
        "signals": [],
    })
    assert v["ok"] is True
    assert v["stale"] is False


def test_boundary_8h_minus_1min_fresh():
    """8h-1min 前 → 新鲜"""
    gen = (datetime.now(_TZ) - timedelta(hours=8, minutes=-1)).isoformat()
    v = sc.validate_qts_signals({
        "report_date": _fresh_date(),
        "generated_at": gen,
        "signals": [],
    })
    assert v["ok"] is True
    assert v["stale"] is False


def test_boundary_8h_plus_1min_stale():
    """8h+1min 前 → 过期"""
    gen = (datetime.now(_TZ) - timedelta(hours=8, minutes=1)).isoformat()
    v = sc.validate_qts_signals({
        "report_date": _fresh_date(),
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
        "report_date": _fresh_date(),
        "generated_at": gen_naive,
        "signals": [],
    })
    assert v["ok"] is True
    assert v["stale"] is False


def test_utc_generated_at_converted_to_cn():
    """带 UTC 时区的 generated_at → 转换为 Asia/Shanghai 后比较"""
    gen_utc = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    v = sc.validate_qts_signals({
        "report_date": _fresh_date(),
        "generated_at": gen_utc,
        "signals": [],
    })
    assert v["ok"] is True
    assert v["stale"] is False


def test_future_generated_at_handled():
    """未来时间 → 负数 age，应判为新鲜（不抛异常）"""
    gen_future = (datetime.now(_TZ) + timedelta(hours=1)).isoformat()
    v = sc.validate_qts_signals({
        "report_date": _fresh_date(),
        "generated_at": gen_future,
        "signals": [],
    })
    # 负数 age → 不判断为 stale（防御性：不卡未来时间）
    assert v["ok"] is True
    assert v["stale"] is False


# ============================================================
# 数据本体时效 report_date（2026-09-03 新增）
# ============================================================
# 背景：QTS backtest_reports 自 2026-07-16 断更 47 天，pull_qts_signals.py 取
# `ORDER BY created_at DESC LIMIT 1`（永远有行、不报错）→ 09-01 15:05 拉到 07-16
# 的日报、generated_at 全新 → 原校验判「✅ 契约有效」放行。
# report_date 此前只被读进返回值、从不参与判定（装饰性字段）。

def test_report_date_stale_blocks():
    """数据本体过期（远超阈值）→ report_stale=True 且 ok=False"""
    v = sc.validate_qts_signals({
        "report_date": _fresh_date(49),
        "generated_at": datetime.now(_TZ).isoformat(),
        "signals": [{"ts_code": "600584.SH"}],
    })
    assert v["ok"] is False
    assert v["report_stale"] is True
    assert v["report_age_days"] == 49
    assert "断更" in v["msg"]


def test_report_date_boundary_10d_ok():
    """恰好 10 天（阈值内，容忍长假断档）→ 不告警"""
    v = sc.validate_qts_signals({
        "report_date": _fresh_date(10),
        "generated_at": datetime.now(_TZ).isoformat(),
        "signals": [],
    })
    assert v["report_stale"] is False
    assert v["ok"] is True


def test_report_date_boundary_11d_stale():
    """11 天 → 越界告警"""
    v = sc.validate_qts_signals({
        "report_date": _fresh_date(11),
        "generated_at": datetime.now(_TZ).isoformat(),
        "signals": [],
    })
    assert v["report_stale"] is True
    assert v["ok"] is False


def test_report_date_long_holiday_not_false_alarm():
    """春节/国庆 9 日历日断档 → 必须静默，否则复市首日必误报"""
    v = sc.validate_qts_signals({
        "report_date": _fresh_date(9),
        "generated_at": datetime.now(_TZ).isoformat(),
        "signals": [],
    })
    assert v["report_stale"] is False
    assert v["ok"] is True


def test_report_date_in_future_rejected():
    """report_date 落在未来（时钟漂移/写错）→ 同样不可信"""
    future = (date.today() + timedelta(days=3)).isoformat()
    v = sc.validate_qts_signals({
        "report_date": future,
        "generated_at": datetime.now(_TZ).isoformat(),
        "signals": [],
    })
    assert v["report_stale"] is True
    assert v["ok"] is False


def test_report_date_bad_format_fails_loud():
    """report_date 非法格式 → fail-loud，不静默放行"""
    v = sc.validate_qts_signals({
        "report_date": "not-a-date",
        "generated_at": datetime.now(_TZ).isoformat(),
        "signals": [],
    })
    assert v["ok"] is False
    assert v["report_age_days"] is None
    assert "report_date" in v["msg"]


def test_report_stale_and_generated_stale_are_independent():
    """两个时间维度互不替代：generated_at 新鲜 ≠ 数据本体新鲜"""
    fresh_gen = datetime.now(_TZ).isoformat()
    v_body_stale = sc.validate_qts_signals({
        "report_date": _fresh_date(49), "generated_at": fresh_gen, "signals": []})
    old_gen = (datetime.now(_TZ) - timedelta(hours=48)).isoformat()
    v_pull_stale = sc.validate_qts_signals({
        "report_date": _fresh_date(), "generated_at": old_gen, "signals": []})
    # 本体旧、拉取新
    assert v_body_stale["report_stale"] is True and v_body_stale["stale"] is False
    # 本体新、拉取旧
    assert v_pull_stale["report_stale"] is False and v_pull_stale["stale"] is True


def test_load_qts_signals_rejects_stale_report(tmp_path, monkeypatch):
    """端到端：未隔离 + WF通过个股 + 陈旧日报 → 信号必须被拒收，不进共识"""
    p = tmp_path / "qts_daily_signals.json"
    p.write_text(json.dumps({
        "report_date": _fresh_date(49),
        "generated_at": datetime.now(_TZ).isoformat(),
        "quarantine": False,
        "signals": [{"ts_code": "600584.SH", "strategy": "COMBO", "wf_passed": True}],
    }), encoding="utf-8")
    monkeypatch.setattr(sc, "_QTS_SIGNALS", p)
    data = sc._load_qts_signals()
    assert data["signals"] == []
    assert "拒收" in data["note"]


def test_load_qts_signals_fresh_report_passes(tmp_path, monkeypatch):
    """对照组：日报新鲜 → 信号正常通行（证明拦截非恒真）"""
    p = tmp_path / "qts_daily_signals.json"
    p.write_text(json.dumps({
        "report_date": _fresh_date(),
        "generated_at": datetime.now(_TZ).isoformat(),
        "quarantine": False,
        "signals": [{"ts_code": "600584.SH", "strategy": "COMBO", "wf_passed": True}],
    }), encoding="utf-8")
    monkeypatch.setattr(sc, "_QTS_SIGNALS", p)
    data = sc._load_qts_signals()
    assert len(data["signals"]) == 1
    assert "note" not in data
