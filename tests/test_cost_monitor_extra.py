"""test_cost_monitor_extra.py — cost_monitor 额外测试。"""

from unittest.mock import patch

import cost_monitor as cm


@patch("cost_monitor.subprocess.run")
def test_daily_report_returns_string(mock_run):
    mock_run.return_value.stdout = "📊 今日成本报告"
    mock_run.return_value.returncode = 0
    result = cm.daily_report()
    assert isinstance(result, str)


@patch("cost_monitor.subprocess.run")
def test_monthly_report_returns_string(mock_run):
    mock_run.return_value.stdout = "📅 月度汇总"
    mock_run.return_value.returncode = 0
    result = cm.monthly_report()
    assert isinstance(result, str)


@patch("cost_monitor.subprocess.run")
def test_quick_summary_returns_string(mock_run):
    mock_run.return_value.stdout = "📊 预算摘要"
    mock_run.return_value.returncode = 0
    result = cm.quick_summary()
    assert isinstance(result, str)


@patch("cost_monitor.subprocess.run")
def test_generate_dashboard_returns_string(mock_run):
    mock_run.return_value.stdout = "<html>dashboard</html>"
    mock_run.return_value.returncode = 0
    result = cm.generate_dashboard()
    assert isinstance(result, str)
