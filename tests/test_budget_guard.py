"""test_budget_guard.py — 预算守护的层级判定 + fail-closed 守卫。

用 unittest.mock 替换 cost_tracker 的真实读取，使预算状态完全可注入。
"""

from unittest.mock import patch

import budget_guard as bg
import pytest


@pytest.fixture(autouse=True)
def _clear_cache():
    bg._budget_cache = None
    bg._budget_cache_time = 0
    yield
    bg._budget_cache = None


def _status(spent: float, budget: float = 400.0) -> dict:
    with patch("cost_tracker.get_monthly_spent", return_value=spent), \
         patch("cost_tracker.MONTHLY_BUDGET_CNY", budget):
        return bg.check_budget_status()


def test_full_when_low():
    s = _status(100.0)
    assert s["tier"] == "full"
    assert s["remaining"] == pytest.approx(300.0)
    assert s["pct"] == pytest.approx(0.25)


def test_normal_at_half():
    # >=50% → normal
    assert _status(210.0)["tier"] == "normal"


def test_flash_preferred_at_70pct():
    # >=70% → flash_preferred
    assert _status(290.0)["tier"] == "flash_preferred"


def test_flash_only_at_875pct():
    # >=87.5% → flash_only (>=350/400)
    assert _status(360.0)["tier"] == "flash_only"


def test_flash_only_at_exact_threshold():
    # 恰好 350/400 = 87.5% → flash_only
    assert _status(350.0)["tier"] == "flash_only"


def test_budget_zero_fail_closed():
    # MONTHLY_BUDGET=0 → 配置异常，fail-closed 锁定 Flash，绝不放行
    s = _status(999.0, budget=0.0)
    assert s["tier"] == "flash_only"
    assert s["pct"] == 1.0
    assert "⛔" in s["msg"]


def test_budget_negative_fail_closed():
    # MONTHLY_BUDGET 被误改为负数 → 同样 fail-closed
    s = _status(999.0, budget=-10.0)
    assert s["tier"] == "flash_only"
    assert "⛔" in s["msg"]


def test_get_allowed_model_flash_only_downgrades():
    with patch("cost_tracker.get_monthly_spent", return_value=360.0), \
         patch("cost_tracker.MONTHLY_BUDGET_CNY", 400.0):
        allowed = bg.get_allowed_model("gpt-5", "normal")
    assert allowed == "deepseek-v4-flash"


def test_get_allowed_model_full_keeps_intended():
    with patch("cost_tracker.get_monthly_spent", return_value=100.0), \
         patch("cost_tracker.MONTHLY_BUDGET_CNY", 400.0):
        allowed = bg.get_allowed_model("gpt-5", "normal")
    assert allowed == "gpt-5"


def test_verify_call_cost_blocks_over_limit():
    with patch("cost_tracker.MODEL_PRICES", {"gpt-5": {"input": 100.0, "output": 100.0}}), \
         patch("cost_tracker._match_model", return_value="gpt-5"):
        allowed, cost = bg.verify_call_cost(1_000_000, 1_000_000, "gpt-5")
    assert allowed is False
    assert cost > 5.0


def test_verify_call_cost_allows_under_limit():
    with patch("cost_tracker.MODEL_PRICES", {"gpt-5": {"input": 0.0, "output": 0.0}}), \
         patch("cost_tracker._match_model", return_value="gpt-5"):
        allowed, cost = bg.verify_call_cost(10, 10, "gpt-5")
    assert allowed is True
    assert cost == 0.0


# ============================================================
# PR-2: parse_budget 健壮解析
# ============================================================

def test_parse_budget_normal_int():
    assert bg.parse_budget("400") == 400


def test_parse_budget_none_returns_zero():
    assert bg.parse_budget(None) == 0


def test_parse_budget_empty_returns_zero():
    assert bg.parse_budget("") == 0
    assert bg.parse_budget("  \t  ") == 0


def test_parse_budget_float_floor():
    assert bg.parse_budget("10.5") == 10
    assert bg.parse_budget("399.9") == 399


def test_parse_budget_non_numeric_returns_zero():
    assert bg.parse_budget("abc") == 0


def test_parse_budget_negative_returns_zero():
    assert bg.parse_budget("-100") == 0
    assert bg.parse_budget("-0.01") == 0


def test_parse_budget_exceeds_cap():
    assert bg.parse_budget("2000000") == bg.MAX_BUDGET_CAP
    assert bg.parse_budget(str(bg.MAX_BUDGET_CAP + 1)) == bg.MAX_BUDGET_CAP


def test_parse_budget_at_cap():
    assert bg.parse_budget(str(bg.MAX_BUDGET_CAP)) == bg.MAX_BUDGET_CAP


# ============================================================
# PR-3: get_allowed_model 补充边界
# ============================================================


def test_get_allowed_model_flash_preferred_downgrades_flagship():
    """flash_preferred 层级下，非关键旗舰任务降为 PRO 模型"""
    with patch("cost_tracker.get_monthly_spent", return_value=290.0), \
         patch("cost_tracker.MONTHLY_BUDGET_CNY", 400.0):
        allowed = bg.get_allowed_model("gpt-5", "normal")
    assert allowed == "deepseek-v4-pro"


def test_get_allowed_model_flash_only_critical_keeps_flagship():
    """flash_only 但 critical 任务 + 旗舰模型仍然允许"""
    with patch("cost_tracker.get_monthly_spent", return_value=360.0), \
         patch("cost_tracker.MONTHLY_BUDGET_CNY", 400.0):
        allowed = bg.get_allowed_model("gpt-5", "critical")
    assert allowed == "gpt-5"


def test_get_allowed_model_flash_only_critical_non_flagship():
    """flash_only + critical 但非旗舰模型 → 直接返回原模型（不在此列表则放行）"""
    with patch("cost_tracker.get_monthly_spent", return_value=360.0), \
         patch("cost_tracker.MONTHLY_BUDGET_CNY", 400.0):
        # kimi-k2.6 不在 FLAGSHIP_MODELS 内 → 不做豁免
        allowed = bg.get_allowed_model("kimi-k2.6", "critical")
    # flash_only 对非旗舰仍降级为 FLASH_MODEL
    assert allowed == "deepseek-v4-flash"


def test_verify_call_cost_near_limit():
    """贴近但不超过 MAX_SINGLE_CALL 的单次调用应通过"""
    with patch("cost_tracker.MODEL_PRICES", {"gpt-5": {"input": 0.5, "output": 0.5}}), \
         patch("cost_tracker._match_model", return_value="gpt-5"):
        # 49999 * 0.5 + 49999 * 0.5 = 49999 → /10000 = 4.9999
        allowed, cost = bg.verify_call_cost(49999, 49999, "gpt-5")
    assert allowed is True
    assert cost < bg.MAX_SINGLE_CALL


# ============================================================
# budget_summary
# ============================================================


def test_budget_summary_returns_string():
    """budget_summary 返回非空格式化的预算摘要字符串"""
    import budget_guard as bg_module
    with patch("cost_tracker.get_monthly_spent", return_value=100.0), \
         patch("cost_tracker.MONTHLY_BUDGET_CNY", 400.0), \
         patch("cost_tracker._load_records", return_value=[]), \
         patch("cost_tracker.daily_report", return_value={"total": 0, "count": 0}):
        summary = bg_module.budget_summary()
        assert "本月预算" in summary
        assert isinstance(summary, str)
        assert len(summary) > 50
