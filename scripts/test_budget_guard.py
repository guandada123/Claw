"""
test_budget_guard.py — 预算守护单元测试
==========================================
覆盖：check_budget_status / budget_summary / get_allowed_model / verify_call_cost
运行：cd Claw/scripts && pytest test_budget_guard.py -v
"""

import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from unittest.mock import patch

from budget_guard import (
    budget_summary,
    check_budget_status,
    get_allowed_model,
    verify_call_cost,
)

# ============================================================
# 辅助：重置 budget_guard 内部缓存
# ============================================================


def _clear_cache():
    """重置 check_budget_status 的 60s TTL 缓存"""
    import budget_guard

    budget_guard._budget_cache = None
    budget_guard._budget_cache_time = 0


# ============================================================
# check_budget_status() — 预算状态检查
# ============================================================


class TestCheckBudgetStatus:
    """check_budget_status 在不同消费场景下的表现
    注意：需 patch budget_guard 层（模块级导入）
    """

    @patch("budget_guard.get_monthly_spent", return_value=0.0)
    @patch("budget_guard.MONTHLY_BUDGET", 400.0)
    def test_zero_spending_returns_full(self, mock_spent):
        """零消费 → tier='full'"""
        _clear_cache()
        status = check_budget_status()
        assert status["tier"] == "full"
        assert status["spent"] == 0.0
        assert status["remaining"] == 400.0
        assert status["pct"] == 0.0

    @patch("budget_guard.get_monthly_spent", return_value=200.0)
    @patch("budget_guard.MONTHLY_BUDGET", 400.0)
    def test_half_spending_returns_normal(self, mock_spent):
        """消费 ¥200/¥400 → tier='normal'"""
        _clear_cache()
        status = check_budget_status()
        assert status["tier"] == "normal"
        assert status["pct"] == 0.5

    @patch("budget_guard.get_monthly_spent", return_value=300.0)
    @patch("budget_guard.MONTHLY_BUDGET", 400.0)
    def test_300_spending_returns_flash_preferred(self, mock_spent):
        """消费 ¥300/¥400 → tier='flash_preferred'"""
        _clear_cache()
        status = check_budget_status()
        assert status["tier"] == "flash_preferred"
        assert status["pct"] == 0.75

    @patch("budget_guard.get_monthly_spent", return_value=350.0)
    @patch("budget_guard.MONTHLY_BUDGET", 400.0)
    def test_350_triggers_flash_only(self, mock_spent):
        """消费 ¥350/¥400 → tier='flash_only'"""
        _clear_cache()
        status = check_budget_status()
        assert status["tier"] == "flash_only"
        assert status["pct"] == 0.875

    @patch("budget_guard.get_monthly_spent", return_value=400.0)
    @patch("budget_guard.MONTHLY_BUDGET", 400.0)
    def test_exhausted_budget_flash_only(self, mock_spent):
        """预算耗尽 → tier='flash_only'"""
        _clear_cache()
        status = check_budget_status()
        assert status["tier"] == "flash_only"
        assert status["remaining"] == 0.0

    @patch("budget_guard.get_monthly_spent", return_value=0.0)
    @patch("budget_guard.MONTHLY_BUDGET", 400.0)
    def test_cache_hits_on_second_call(self, mock_spent):
        """第二次调用应使用缓存"""
        _clear_cache()
        with patch("budget_guard.get_monthly_spent", return_value=0.0) as mock:
            status1 = check_budget_status()
            # 第二次调用应命中缓存，不再调用 get_monthly_spent
            status2 = check_budget_status()
        assert status2["spent"] == status1["spent"]


# ============================================================
# budget_summary() — 预算摘要
# ============================================================


class TestBudgetSummary:
    """budget_summary 输出格式与零消费场景"""

    @patch("budget_guard.get_monthly_spent", return_value=0.0)
    @patch("budget_guard.MONTHLY_BUDGET", 400.0)
    @patch("budget_guard.daily_report", return_value={"total": 0.0, "count": 0})
    @patch("budget_guard._load_records", return_value=[])
    def test_zero_spending_summary(self, mock_load, mock_daily, mock_spent):
        """零消费场景的摘要应正确显示 ¥0"""
        _clear_cache()
        summary = budget_summary()
        assert "¥0" in summary or "¥0.0" in summary
        assert "full" in summary
        assert "400" in summary  # 总预算 ¥400

    @patch("budget_guard.get_monthly_spent", return_value=250.0)
    @patch("budget_guard.MONTHLY_BUDGET", 400.0)
    @patch("budget_guard.daily_report", return_value={"total": 0.0, "count": 0})
    @patch("budget_guard._load_records", return_value=[])
    def test_partial_usage_summary(self, mock_load, mock_daily, mock_spent):
        """消费 ¥250 的摘要"""
        _clear_cache()
        summary = budget_summary()
        assert "250" in summary
        assert "400" in summary

    @patch("budget_guard.get_monthly_spent", return_value=380.0)
    @patch("budget_guard.MONTHLY_BUDGET", 400.0)
    @patch("budget_guard.daily_report", return_value={"total": 0.0, "count": 0})
    @patch("budget_guard._load_records", return_value=[])
    def test_flash_locked_summary_warns(self, mock_load, mock_daily, mock_spent):
        """Flash 锁定模式的摘要应包含 flash_only"""
        _clear_cache()
        summary = budget_summary()
        assert "flash_only" in summary.lower()


# ============================================================
# get_allowed_model() — 模型准入控制
# ============================================================


class TestGetAllowedModel:
    """get_allowed_model 预算敏感模型选择（纯逻辑，mock check_budget_status 即可）"""

    def test_full_budget_allows_any(self):
        """预算充足时允许任何模型"""
        with patch("budget_guard.check_budget_status") as mock_check:
            mock_check.return_value = {"tier": "full", "spent": 100, "remaining": 300}
            assert get_allowed_model("gpt-5") == "gpt-5"
            assert get_allowed_model("deepseek-v4-pro") == "deepseek-v4-pro"

    def test_flash_only_downgrades_pro(self):
        """flash_only 将 Pro 降为 Flash"""
        with patch("budget_guard.check_budget_status") as mock_check:
            mock_check.return_value = {"tier": "flash_only", "spent": 360, "remaining": 40}
            assert get_allowed_model("deepseek-v4-pro") == "deepseek-v4-flash"

    def test_flash_only_keeps_critical_flagship(self):
        """flash_only 下关键任务仍允许旗舰模型"""
        with patch("budget_guard.check_budget_status") as mock_check:
            mock_check.return_value = {"tier": "flash_only", "spent": 360, "remaining": 40}
            assert get_allowed_model("gpt-5", task_priority="critical") == "gpt-5"

    def test_flash_preferred_downgrades_normal_pro(self):
        """flash_preferred 将普通任务的 Pro 降为 Flash"""
        with patch("budget_guard.check_budget_status") as mock_check:
            mock_check.return_value = {"tier": "flash_preferred", "spent": 300, "remaining": 100}
            assert get_allowed_model("deepseek-v4-pro") == "deepseek-v4-flash"

    def test_flash_preferred_keeps_critical_pro(self):
        """flash_preferred 下关键任务的 Pro 不降级"""
        with patch("budget_guard.check_budget_status") as mock_check:
            mock_check.return_value = {"tier": "flash_preferred", "spent": 300, "remaining": 100}
            assert (
                get_allowed_model("deepseek-v4-pro", task_priority="critical") == "deepseek-v4-pro"
            )


# ============================================================
# verify_call_cost() — 调用前成本验证
# ============================================================


class TestVerifyCallCost:
    """verify_call_cost 单次调用成本验证"""

    @patch("budget_guard.MODEL_PRICES", {"deepseek-v4-flash": {"input": 0.5, "output": 1.5}})
    @patch("budget_guard._match_model", return_value="deepseek-v4-flash")
    def test_small_call_allowed(self, mock_match):
        """小 Token 数调用应允许"""
        allowed, cost = verify_call_cost(1000, 500, "deepseek-v4-flash")
        assert allowed is True
        assert cost < 5.0

    @patch("budget_guard.MODEL_PRICES", {"deepseek-v4-pro": {"input": 4.0, "output": 12.0}})
    @patch("budget_guard._match_model", return_value="deepseek-v4-pro")
    def test_large_call_blocked(self, mock_match):
        """大 Token 数调用应被拦截"""
        allowed, cost = verify_call_cost(10000, 5000, "deepseek-v4-pro")
        assert allowed is False
        assert cost > 5.0

    @patch("budget_guard.MODEL_PRICES", {})
    @patch("budget_guard._match_model", return_value="unknown")
    def test_unknown_model_zero_cost(self, mock_match):
        """未知模型按零成本计算，应允许"""
        allowed, cost = verify_call_cost(1000, 500, "unknown-model")
        assert allowed is True
        assert cost == 0.0
