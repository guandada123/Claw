"""test_cost_monitor.py — cost_monitor 测试。"""

from unittest.mock import patch

import cost_monitor as cm


def test_run_cost_tracker_returns_string():
    with patch("cost_monitor.subprocess.run") as mock_run:
        mock_run.return_value.stdout = "test output"
        mock_run.return_value.returncode = 0
        result = cm.run_cost_tracker("daily")
        assert isinstance(result, str)


def test_run_budget_guard_returns_string():
    with patch("cost_monitor.subprocess.run") as mock_run:
        mock_run.return_value.stdout = "budget output"
        mock_run.return_value.returncode = 0
        result = cm.run_budget_guard("status")
        assert isinstance(result, str)
