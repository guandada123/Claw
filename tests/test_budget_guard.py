"""test_budget_guard.py — 预算守护的层级判定 + fail-closed 守卫（v3.0 积分口径）。

注入点（都不碰真实平台库）：
  - `budget_guard.total_for_month`（积分读取单源在 credit_meter，此处整体替换）
  - `budget_guard.monthly_budget_credits`（月额度，生产环境由 `WB_CREDIT_BUDGET` 决定）

注入统一走 `patch.multiple`：一次 `with` 同时替换两者，
既避免嵌套 `with`（SIM117），也避免「括号多上下文」写法在 Python 3.9 上的兼容风险。
"""

from unittest.mock import Mock, patch

import budget_guard as bg
import pytest
from credit_meter import CreditReadError


@pytest.fixture(autouse=True)
def _clear_cache(monkeypatch):
    bg._budget_cache = None
    bg._budget_cache_time = 0
    monkeypatch.delenv(bg.BUDGET_ENV_VAR, raising=False)
    yield
    bg._budget_cache = None


def _ctx(spent, budget=6000.0):
    """组合注入：本月积分 = spent，月额度 = budget。"""
    return patch.multiple(
        "budget_guard",
        total_for_month=Mock(return_value=spent),
        monthly_budget_credits=Mock(return_value=budget),
    )


def _status(spent, budget=6000.0):
    with _ctx(spent, budget):
        return bg.check_budget_status()


def test_full_when_low():
    s = _status(1000.0)
    assert s["tier"] == "full"
    assert s["remaining"] == pytest.approx(5000.0)
    assert s["pct"] == pytest.approx(1000 / 6000)
    assert s["unit"] == "credits"


def test_normal_at_half():
    # >=50% → normal
    assert _status(3300.0)["tier"] == "normal"


def test_flash_preferred_at_70pct():
    # >=70% → flash_preferred
    assert _status(4400.0)["tier"] == "flash_preferred"


def test_flash_only_at_875pct():
    # >=87.5% → flash_only
    assert _status(5400.0)["tier"] == "flash_only"


def test_flash_only_at_exact_threshold():
    # 恰好 87.5% → flash_only
    assert _status(5250.0)["tier"] == "flash_only"


def test_default_budget_used_when_env_absent():
    """未设任何环境变量 → 用「套餐月发 + 签到×当月天数」组成式额度。"""
    import calendar
    import datetime

    with patch("budget_guard.total_for_month", return_value=1000.0):
        s = bg.check_budget_status()
    days = calendar.monthrange(datetime.date.today().year, datetime.date.today().month)[1]
    expect = bg.DEFAULT_MONTHLY_GRANT + bg.DEFAULT_CHECKIN_DAILY * days
    assert s["remaining"] == pytest.approx(expect - 1000.0)


def test_budget_override_env_wins(monkeypatch):
    """WB_CREDIT_BUDGET 一把覆盖组成式。"""
    monkeypatch.setenv(bg.BUDGET_ENV_VAR, "800")
    with patch("budget_guard.total_for_month", return_value=100.0):
        s = bg.check_budget_status()
    assert s["remaining"] == pytest.approx(700.0)


def test_grant_and_checkin_env_compose(monkeypatch):
    """套餐发放与签到日产可分别覆盖（¥58 档基础 2000，赠送取消时改 grant 即可）。"""
    import calendar
    import datetime

    monkeypatch.setenv(bg.GRANT_ENV_VAR, "2000")
    monkeypatch.setenv(bg.CHECKIN_ENV_VAR, "100")
    days = calendar.monthrange(datetime.date.today().year, datetime.date.today().month)[1]
    assert bg.monthly_budget_credits() == pytest.approx(2000 + 100 * days)


def test_bad_grant_env_falls_back_to_default(monkeypatch):
    """发放额写错 → 回退默认（不静默变 0 把额度抹掉）。"""
    monkeypatch.setenv(bg.GRANT_ENV_VAR, "abc")
    assert bg.monthly_budget_credits() >= bg.DEFAULT_MONTHLY_GRANT


def test_env_overrides_default_budget(monkeypatch):
    monkeypatch.setenv(bg.BUDGET_ENV_VAR, "1234")
    with patch("budget_guard.total_for_month", return_value=100.0):
        s = bg.check_budget_status()
    assert s["remaining"] == pytest.approx(1134.0)


def test_budget_zero_fail_closed(monkeypatch):
    # WB_CREDIT_BUDGET 显式设为 0 → 配置异常，fail-closed 锁定 Flash，绝不放行
    monkeypatch.setenv(bg.BUDGET_ENV_VAR, "0")
    s = _status(9999.0, budget=0.0)
    assert s["tier"] == "flash_only"
    assert s["pct"] == 1.0
    assert "⛔" in s["msg"]


def test_budget_negative_fail_closed():
    s = _status(9999.0, budget=-10.0)
    assert s["tier"] == "flash_only"
    assert "⛔" in s["msg"]


def test_credit_read_failure_fail_closed():
    """读数失败 ≠ 消耗为 0：必须 fail-closed，且文案要能区分「故障」与「超支」。"""
    with patch.multiple(
        "budget_guard",
        total_for_month=Mock(side_effect=CreditReadError("库不可读")),
        monthly_budget_credits=Mock(return_value=6000.0),
    ):
        s = bg.check_budget_status()
    assert s["tier"] == "flash_only"
    assert "读数失败" in s["msg"]
    assert "超" not in s["msg"]


def test_get_allowed_model_flash_only_downgrades():
    with _ctx(5400.0):
        allowed = bg.get_allowed_model("gpt-5", "normal")
    assert allowed == "deepseek-v4-flash"


def test_get_allowed_model_full_keeps_intended():
    with _ctx(1000.0):
        allowed = bg.get_allowed_model("gpt-5", "normal")
    assert allowed == "gpt-5"


def test_verify_call_cost_uses_measured_rate():
    """单次调用按实测倍率折积分（不再依赖任何 ¥ 价目表）。"""
    allowed, credits = bg.verify_call_cost(1_000_000, 0, "deepseek-v4-pro")
    assert allowed is True
    assert credits == pytest.approx(bg.credit_meter.CREDITS_PER_MILLION_TOKENS)


def test_verify_call_cost_blocks_over_limit():
    # 2000 万 token × 2.68/百万 = 53.6 积分 > 30 → 拦截
    allowed, credits = bg.verify_call_cost(10_000_000, 10_000_000, "deepseek-v4-pro")
    assert allowed is False
    assert credits > bg.MAX_SINGLE_CALL_CREDITS


def test_verify_call_cost_allows_typical_call():
    allowed, credits = bg.verify_call_cost(4000, 1200, "deepseek-v4-flash")
    assert allowed is True
    assert credits == pytest.approx(5200 / 1_000_000 * bg.credit_meter.CREDITS_PER_MILLION_TOKENS)


# ============================================================
# parse_budget 健壮解析（单位：积分）
# ============================================================


def test_parse_budget_normal_int():
    assert bg.parse_budget("6000") == 6000


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
# get_allowed_model 补充边界
# ============================================================


def test_get_allowed_model_flash_preferred_downgrades_flagship():
    """flash_preferred 层级下，非关键旗舰任务降为 PRO 模型"""
    with _ctx(4400.0):
        allowed = bg.get_allowed_model("gpt-5", "normal")
    assert allowed == "deepseek-v4-pro"


def test_get_allowed_model_flash_only_critical_keeps_flagship():
    """flash_only 但 critical 任务 + 旗舰模型仍然允许"""
    with _ctx(5400.0):
        allowed = bg.get_allowed_model("gpt-5", "critical")
    assert allowed == "gpt-5"


def test_get_allowed_model_flash_only_critical_non_flagship():
    """flash_only + critical 但非旗舰模型 → 仍降级为 Flash"""
    with _ctx(5400.0):
        allowed = bg.get_allowed_model("kimi-k2.6", "critical")
    assert allowed == "deepseek-v4-flash"


# ============================================================
# budget_summary
# ============================================================


def test_budget_summary_returns_string():
    with _ctx(1000.0):
        summary = bg.budget_summary()
    assert "本月积分" in summary
    assert "积分" in summary
    assert "¥" not in summary  # v3.0 起不再有 ¥ 口径
    assert len(summary) > 50


def test_budget_summary_reports_read_failure():
    with patch.multiple(
        "budget_guard",
        total_for_month=Mock(side_effect=CreditReadError("库被占用")),
        monthly_budget_credits=Mock(return_value=6000.0),
    ):
        summary = bg.budget_summary()
    assert "读数失败" in summary
