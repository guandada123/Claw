"""test_pull_qts_signals_contract.py — 生产侧契约守卫单测。

背景（实证故障，2026-09-04 修订）:
  QTS `backtest_reports` 把 top_strategies 落进结构化列 `strategies_covered`
  （jsonb，始终 JSON；report_scheduler.py:545 把 :covered 参数写进该列），
  detail_content 是 Markdown 正文（models.py:254 注释「报告正文 Markdown」）。
  原 pull_qts_signals 只从 detail_content(json.loads) 取 top_strategies →
  Markdown 解析失败 → 守卫生效、陈旧文件(2026-07-16)被永久保留(63h+ 陈旧告警)。

  2026-09-04 修复后契约:
    top_strategies 主源 = `strategies_covered` 列（经 qts_client.get_daily_report 透出）；
    wf_validated 自 09-02 起随 Markdown 化丢失 → 缺失时如实标记 wf_stability=None
    （下游质量闸门 quarantine：未经过 WF 验证），绝不静默造假。
    detail 仅作极偶然的 JSON 回灌（含 wf_validated）时的兜底来源。

守卫要求:
  1. strategies_covered 无 top_strategies → 返回结构化 error，不抛异常、不崩溃
  2. error 分支**不得覆盖** data/qts_daily_signals.json —— 用新鲜空产物覆盖
     会让「数据新鲜度」巡检转绿，反而掩盖上游故障
  3. 合法 top_strategies（无 wf）→ 仍产出 FRESH 文件，但 honest quarantine
     （wf_stability=None），不静默当成有效信号
  4. 合法 top_strategies + wf_validated（罕见 JSON 回灌）→ 正常产出带 WF 信号
"""

import json
from datetime import date

import pull_qts_signals as pqs
import pytest


def _fake_report(top_strategies=None, detail=None, report_date="2026-09-03"):
    r = {"report_type": "daily", "report_date": report_date, "detail": detail}
    if top_strategies is not None:
        r["top_strategies"] = top_strategies
    return r


@pytest.fixture
def patched(monkeypatch, tmp_path):
    """把输出文件重定向到 tmp，避免污染真实 data/。"""
    out = tmp_path / "qts_daily_signals.json"
    monkeypatch.setattr(pqs, "_OUTPUT", out)
    return out


def _patch_report(monkeypatch, report):
    import qts_client

    monkeypatch.setattr(qts_client, "get_daily_report", lambda: report)


# ============================================================
# 1. strategies_covered 无 top_strategies → 结构化 error，不崩溃
# ============================================================

def test_markdown_detail_no_top_strategies_returns_error(monkeypatch, patched):
    """Markdown 正文（str）+ 无 covered → 返回 error，绝不 AttributeError"""
    md = "# 🔬 QuantTradingSystem 日报\n**生成时间**: 2026-09-03 08:12\n"
    _patch_report(monkeypatch, _fake_report(detail=md))
    result = pqs.pull()
    assert result["error"] == "report_missing_top_strategies"
    assert result["report_date"] == "2026-09-03"
    assert "covered" in result["hint"]


def test_list_detail_no_top_strategies_guarded(monkeypatch, patched):
    """detail 为 list 且无 covered → 同样走守卫（类型守卫不只针对 str）"""
    _patch_report(monkeypatch, _fake_report(detail=[1, 2, 3]))
    result = pqs.pull()
    assert result["error"] == "report_missing_top_strategies"


def test_error_branch_does_not_overwrite_output(monkeypatch, patched):
    """error 分支不得写产物 —— 否则新鲜空文件会让新鲜度巡检误转绿"""
    patched.write_text(json.dumps({"report_date": "2026-07-16"}), encoding="utf-8")
    before = patched.read_text(encoding="utf-8")
    _patch_report(monkeypatch, _fake_report(detail="# Markdown 正文"))
    pqs.pull()
    assert patched.read_text(encoding="utf-8") == before


# ============================================================
# 2. 合法 top_strategies（无 wf）→ 产出 FRESH 文件但 honest quarantine
# ============================================================

def test_top_strategies_no_wf_produces_fresh_quarantined(monkeypatch, patched):
    """covered 有 top_strategies、无 wf_validated → 文件新鲜但 quarantine
    （wf_stability=None 标记为未经过 WF 验证，下游 signal_consensus 不消费）"""
    tops = [
        {"ts_code": "003032.SZ", "strategy": "kdj", "sharpe": 30.19,
         "total_return": 105.58, "win_rate": 100.0},
    ]
    _patch_report(monkeypatch, _fake_report(top_strategies=tops))
    result = pqs.pull()
    assert "error" not in result
    assert result["report_date"] == "2026-09-03"
    assert result["total_signals"] == 1
    # 无 WF → 全部未通过 → 隔离，不静默当成有效信号
    assert result["quarantine"] is True
    assert result["wf_passed_signals"] == 0
    assert result["signals"] == []
    written = json.loads(patched.read_text(encoding="utf-8"))
    # 新鲜度 = 产物生成于当天（原写死 2026-09-04 修复日，跨日后必失败，改为动态判据）
    assert written["generated_at"].startswith(date.today().strftime("%Y-%m-%d"))  # 新鲜


# ============================================================
# 3. 反向验证：合法 JSON 契约（含 wf）仍能正常产出（守卫非恒真）
# ============================================================

def test_valid_json_contract_still_produces_signals(monkeypatch, patched):
    """合法 top_strategies + wf_validated（罕见 JSON 回灌）→ 正常产出信号"""
    tops = [
        {"ts_code": "600000.SH", "strategy": "kdj", "sharpe": 1.8,
         "total_return": 12.0, "win_rate": 55.0},
    ]
    detail = {
        "top_strategies": tops,
        "wf_validated": {"600000.SH": {"stability": 72.0, "overfit_ratio": 1.1}},
    }
    _patch_report(monkeypatch, _fake_report(top_strategies=tops, detail=detail))
    result = pqs.pull()
    assert "error" not in result
    assert result["quarantine"] is False
    assert result["wf_passed_signals"] == 1
    assert result["signals"][0]["ts_code"] == "600000.SH"
    assert patched.exists()


def test_valid_json_all_wf_failed_quarantines(monkeypatch, patched):
    """合法 top_strategies + wf 全不过 → 沿用既有隔离闸门"""
    tops = [
        {"ts_code": "600000.SH", "strategy": "kdj", "sharpe": 1.0,
         "total_return": 3.0, "win_rate": 40.0},
    ]
    detail = {
        "top_strategies": tops,
        "wf_validated": {"600000.SH": {"stability": 12.0}},
    }
    _patch_report(monkeypatch, _fake_report(top_strategies=tops, detail=detail))
    result = pqs.pull()
    assert result["quarantine"] is True
    assert result["signals"] == []


def test_no_report_in_db_still_handled(monkeypatch, patched):
    """表为空 → 沿用原 no_report_in_db 分支（未被新守卫破坏）"""
    _patch_report(monkeypatch, None)
    result = pqs.pull()
    assert result["error"] == "no_report_in_db"
