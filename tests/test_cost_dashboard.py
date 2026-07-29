"""test_cost_dashboard.py — cost_dashboard 纯数据处理函数测试。"""

from datetime import date
from unittest.mock import patch

import cost_dashboard as cd


def _sample_records():
    return [
        {"date": "2026-07-19", "model_key": "gpt-5", "cost_cny": 10.0, "project": "Claw", "task": "代码审查"},
        {"date": "2026-07-18", "model_key": "deepseek-v4-flash", "cost_cny": 0.5, "project": "Claw", "task": "日报"},
        {"date": "2026-07-19", "model_key": "gpt-5", "cost_cny": 5.0, "project": "QTS", "task": "回测"},
    ]


# ── get_daily_trend ──


def test_daily_trend_fills_missing_dates():
    """缺失日期补充为 0。"""
    recs = [{"date": "2026-07-15", "cost_cny": 1.0}]
    trend = cd.get_daily_trend(recs, days=5)
    assert len(trend) == 5
    # 只有 07-15 有值
    for d in trend:
        if d["date"] == "2026-07-15":
            assert d["cost"] == 1.0
        else:
            assert d["cost"] == 0.0


def test_daily_trend_empty_records():
    trend = cd.get_daily_trend([], days=3)
    assert len(trend) == 3
    assert all(d["cost"] == 0.0 for d in trend)
    assert trend[0]["date"] < trend[2]["date"]  # 日期递增


# ── get_model_distribution ──


def test_model_distribution():
    result = cd.get_model_distribution(_sample_records())
    names = [r["name"] for r in result]
    assert "gpt-5" in names
    assert "deepseek-v4-flash" in names


def test_model_distribution_empty():
    assert cd.get_model_distribution([]) == []


# ── get_project_distribution ──


def test_project_distribution():
    result = cd.get_project_distribution(_sample_records())
    names = [r["name"] for r in result]
    assert "Claw" in names
    assert "QTS" in names


# ── get_top_tasks ──


def test_top_tasks():
    result = cd.get_top_tasks(_sample_records(), n=5)
    assert len(result) <= 5
    task_names = [r["name"] for r in result]
    assert "代码审查" in task_names


def test_top_tasks_empty():
    assert cd.get_top_tasks([], n=3) == []


# ── get_month_summary ──


@patch("cost_dashboard.date")
def test_month_summary(mock_date):
    mock_date.today.return_value = date(2026, 7, 19)
    mock_date.side_effect = lambda *a, **kw: date(*a, **kw) if len(a) == 3 else date.today()

    recs = [
        {"date": "2026-07-01", "cost_cny": 50.0},
        {"date": "2026-07-15", "cost_cny": 30.0},
        {"date": "2026-06-30", "cost_cny": 100.0},  # 上月，不应计入
    ]
    result = cd.get_month_summary(recs)
    assert result["total"] == 80.0
    assert result["count"] == 2
    assert "projection" in result


# ── get_cost_estimates ──


def test_get_automation_estimates():
    result = cd.get_automation_estimates()
    assert len(result) > 0
    for item in result:
        assert "name" in item
        assert "cost" in item
        assert item["cost"] >= 0


# ── 常量 ──


def test_constants():
    assert cd.MONTHLY_BUDGET_CNY > 0
    assert cd.DAILY_WARNING_CNY > 0
