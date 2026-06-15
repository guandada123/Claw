"""
test_router.py — Claw 四层路由单元测试
========================================
覆盖：route_task / get_model / call_llm 拆分后的辅助函数
运行：cd Claw/scripts && pytest test_router.py -v
"""

from unittest.mock import patch
import sys
import os

# 确保可以导入 router（脚本目录在 sys.path 中）
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from router import (
    ModelTier,
    route_task,
    get_model,
    _select_premium_model,
    _make_error_resp,
    _resolve_api_config,
    _build_chat_messages,
    _parse_success_response,
    PREMIUM_SIGNALS,
)


# ============================================================
# route_task() — 四层路由逻辑
# ============================================================

class TestRouteTask:
    """路由匹配测试（纯逻辑，无需 mock）"""

    def test_local_heartbeat(self):
        assert route_task("heartbeat check") == ModelTier.LOCAL

    def test_local_health_check(self):
        assert route_task("健康检查") == ModelTier.LOCAL

    def test_local_inspection(self):
        assert route_task("系统巡检") == ModelTier.LOCAL

    def test_local_bool_judgment(self):
        assert route_task("判断这个服务是否在线") == ModelTier.LOCAL

    def test_flash_formatting(self):
        assert route_task("帮我格式化这段代码") == ModelTier.FLASH

    def test_flash_summary(self):
        assert route_task("总结这篇新闻的要点") == ModelTier.FLASH

    def test_flash_translation(self):
        assert route_task("翻译成英文") == ModelTier.FLASH

    def test_flash_data_cleaning(self):
        assert route_task("整理这份股票列表，只保留上涨的") == ModelTier.FLASH

    def test_flash_sector_ranking(self):
        assert route_task("今天哪个板块涨幅最大") == ModelTier.FLASH

    def test_flash_market_overview(self):
        assert route_task("用一句话概括今天的市场") == ModelTier.FLASH

    def test_flash_push_template(self):
        assert route_task("生成飞书推送消息模板") == ModelTier.FLASH

    def test_flash_glossary(self):
        assert route_task("KDJ是什么意思") == ModelTier.FLASH

    def test_pro_strategy(self):
        assert route_task("实现一个选股策略函数，要求...") == ModelTier.PRO

    def test_pro_stock_analysis(self):
        assert route_task("分析000001的技术形态，给出建议") == ModelTier.PRO

    def test_pro_backtest(self):
        assert route_task("这个股票回测结果怎么样") == ModelTier.PRO

    def test_pro_debug(self):
        assert route_task("帮我调试这个bug，错误信息是...") == ModelTier.PRO

    def test_pro_api_design(self):
        assert route_task("设计一个风控接口，需要止损逻辑") == ModelTier.PRO

    def test_premium_code_review(self):
        assert route_task("帮我审查这整个模块的代码") == ModelTier.PREMIUM

    def test_premium_architecture(self):
        assert route_task("设计交易系统的整体架构") == ModelTier.PREMIUM

    def test_premium_docs(self):
        assert route_task("写一份系统架构文档") == ModelTier.PREMIUM

    def test_premium_review(self):
        assert route_task("方案评审：这三种技术方案的对比") == ModelTier.PREMIUM

    def test_force_tier_override(self):
        """force_tier 参数应覆盖路由判断"""
        assert route_task("心跳", force_tier=ModelTier.PRO) == ModelTier.PRO
        assert route_task("审查代码", force_tier=ModelTier.FLASH) == ModelTier.FLASH

    def test_allow_local_false_skips_local(self):
        """allow_local=False 时跳过 LOCAL 层"""
        # heartbeat 正常匹配 LOCAL，但 allow_local=False 后跳到默认 PRO
        result = route_task("heartbeat check", allow_local=False)
        assert result != ModelTier.LOCAL
        assert result == ModelTier.PRO

    def test_default_tier_is_pro(self):
        """不匹配任何规则时返回 PRO（兜底保守策略）"""
        assert route_task("这是一个完全随机无意义的内容") == ModelTier.PRO

    def test_task_type_combined_with_prompt(self):
        """task_type 与 prompt 拼接后参与匹配"""
        # 单独空 prompt 不命中，但 task_type="代码审查" 触发 PREMIUM
        assert route_task("", task_type="代码审查") == ModelTier.PREMIUM
        # 普通 prompt + premium 标签
        assert route_task("看一下这段代码", task_type="代码审查") == ModelTier.PREMIUM

    def test_premium_signals_are_readonly(self):
        """PREMIUM_SIGNALS 应为 frozenset，禁止运行时修改"""
        try:
            PREMIUM_SIGNALS.add("新信号")  # type: ignore
            assert False, "frozenset 不应支持 add"
        except AttributeError:
            pass


# ============================================================
# _select_premium_model() — PREMIUM 层模型选择
# ============================================================

class TestSelectPremiumModel:
    def test_code_review_uses_claude(self):
        result = _select_premium_model("审查我的代码", "代码审查")
        assert "claude" in result["model"].lower()
        assert result["provider"] == "premium"

    def test_architecture_uses_gpt5(self):
        result = _select_premium_model("设计系统架构", "")
        assert "gpt-5" in result["model"]
        assert result["provider"] == "premium"

    def test_other_task_uses_gpt5(self):
        result = _select_premium_model("随便一段话", "")
        assert "gpt-5" in result["model"]


# ============================================================
# get_model() — 综合路由决策（含预算约束）
# ============================================================

class TestGetModel:
    """需要 mock _local_check 避免实际 HTTP 调用"""

    @patch("router._local_check", return_value=True)
    def test_basic_flash_routing(self, mock_check):
        config = get_model("格式化数据")
        assert config["tier"] == ModelTier.FLASH
        assert config["provider"] == "deepseek"

    @patch("router._local_check", return_value=True)
    def test_local_available(self, mock_check):
        config = get_model("heartbeat check")
        assert config["tier"] == ModelTier.LOCAL
        assert config["provider"] == "ollama"
        assert config["cost_per_10k"] == 0.0

    @patch("router._local_check", return_value=True)
    def test_local_unavailable_parameter(self, mock_check):
        """local_available=False 强制跳过 LOCAL"""
        config = get_model("heartbeat check", local_available=False)
        assert config["tier"] != ModelTier.LOCAL

    @patch("router._local_check", return_value=True)
    def test_budget_flash_only_downgrades_pro(self, mock_check):
        """flash_only 预算将 PRO 降级为 FLASH"""
        budget = {"tier": "flash_only", "spent": 380, "remaining": 20}
        config = get_model("实现选股策略", budget_status=budget)
        assert config["tier"] == ModelTier.FLASH

    @patch("router._local_check", return_value=True)
    def test_budget_flash_only_downgrades_premium(self, mock_check):
        """flash_only 预算将 PREMIUM 降级为 FLASH"""
        budget = {"tier": "flash_only", "spent": 390, "remaining": 10}
        config = get_model("审查代码", budget_status=budget)
        assert config["tier"] == ModelTier.FLASH

    @patch("router._local_check", return_value=True)
    def test_budget_flash_preferred_downgrades_pro(self, mock_check):
        """flash_preferred 预算将 PRO 降级为 FLASH"""
        budget = {"tier": "flash_preferred", "spent": 300, "remaining": 100}
        config = get_model("实现选股策略", budget_status=budget)
        assert config["tier"] == ModelTier.FLASH

    @patch("router._local_check", return_value=True)
    def test_budget_flash_preferred_keeps_premium(self, mock_check):
        """flash_preferred 预算不降级 PREMIUM"""
        budget = {"tier": "flash_preferred", "spent": 300, "remaining": 100}
        config = get_model("审查代码", budget_status=budget)
        assert config["tier"] == ModelTier.PREMIUM

    @patch("router._local_check", return_value=True)
    def test_budget_flash_only_keeps_local(self, mock_check):
        """flash_only 预算不降级 LOCAL"""
        budget = {"tier": "flash_only", "spent": 380, "remaining": 20}
        config = get_model("heartbeat check", budget_status=budget)
        assert config["tier"] == ModelTier.LOCAL

    @patch("router._local_check", return_value=True)
    def test_budget_normal_does_nothing(self, mock_check):
        """normal 预算不做任何降级"""
        budget = {"tier": "normal", "spent": 100, "remaining": 300}
        config = get_model("实现选股策略", budget_status=budget)
        assert config["tier"] == ModelTier.PRO

    @patch("router._local_check", return_value=True)
    def test_no_budget_does_nothing(self, mock_check):
        """无 budget_status 参数时不做降级"""
        config = get_model("实现选股策略")
        assert config["tier"] == ModelTier.PRO

    @patch("router._local_check", return_value=True)
    def test_premium_dispatch_code_review(self, mock_check):
        """代码审查任务应选择 Claude"""
        config = get_model("审查我的代码", task_type="代码审查")
        assert "claude" in config["model"].lower()

    @patch("router._local_check", return_value=True)
    def test_premium_dispatch_architecture(self, mock_check):
        """架构设计任务应选择 GPT-5"""
        config = get_model("设计系统架构")
        assert "gpt-5" in config["model"]


# ============================================================
# 辅助函数测试（纯逻辑，无需 mock）
# ============================================================

class TestMakeErrorResp:
    def test_all_fields_present(self):
        resp = _make_error_resp("m1", "p1", 123, "err msg")
        assert resp["success"] is False
        assert resp["model"] == "m1"
        assert resp["provider"] == "p1"
        assert resp["duration_ms"] == 123
        assert resp["error"] == "err msg"

    def test_zero_values(self):
        resp = _make_error_resp("m", "p", 0, "")
        assert resp["input_tokens"] == 0
        assert resp["output_tokens"] == 0
        assert resp["cost_cny"] == 0.0


class TestResolveApiConfig:
    """确认 API Key / Base URL 按 provider 解析"""

    @patch("router.DEEPSEEK_API_KEY", "ds-key")
    @patch("router.DEEPSEEK_BASE_URL", "https://ds.example.com")
    @patch("router.CATROUTER_API_KEY", "cr-key")
    @patch("router.CATROUTER_BASE_URL", "https://cr.example.com")
    def test_deepseek(self):
        key, url = _resolve_api_config("deepseek", {})
        assert key == "ds-key"
        assert url == "https://ds.example.com"

    @patch("router.DEEPSEEK_API_KEY", "ds-key")
    @patch("router.DEEPSEEK_BASE_URL", "https://ds.example.com")
    @patch("router.CATROUTER_API_KEY", "cr-key")
    @patch("router.CATROUTER_BASE_URL", "https://cr.example.com")
    def test_catrouter(self):
        key, url = _resolve_api_config("catrouter", {})
        assert key == "cr-key"
        assert url == "https://cr.example.com"

    @patch("router.DEEPSEEK_API_KEY", "ds-key")
    @patch("router.DEEPSEEK_BASE_URL", "https://ds.example.com")
    @patch("router.CATROUTER_API_KEY", "cr-key")
    @patch("router.CATROUTER_BASE_URL", "https://cr.example.com")
    def test_premium(self):
        key, url = _resolve_api_config("premium", {})
        assert key == "cr-key"
        assert url == "https://cr.example.com"

    @patch("router.DEEPSEEK_API_KEY", "ds-key")
    @patch("router.DEEPSEEK_BASE_URL", "https://ds.example.com")
    @patch("router.CATROUTER_API_KEY", "cr-key")
    @patch("router.CATROUTER_BASE_URL", "https://cr.example.com")
    def test_unknown_fallback(self):
        mc = {"base_url": "https://custom.example.com"}
        key, url = _resolve_api_config("unknown", mc)
        assert key == "ds-key"  # 回退到 DeepSeek Key
        assert url == "https://custom.example.com"  # 使用 model_config 的 base_url


class TestBuildChatMessages:
    def test_with_system_prompt(self):
        msgs = _build_chat_messages("You are a helper", "Hi!")
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "You are a helper"
        assert msgs[1]["role"] == "user"
        assert msgs[1]["content"] == "Hi!"

    def test_without_system_prompt(self):
        msgs = _build_chat_messages(None, "Hello")
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "Hello"

    def test_empty_system_prompt(self):
        msgs = _build_chat_messages("", "test")
        assert len(msgs) == 1  # 空字符串被视为 falsy


class TestParseSuccessResponse:
    """_parse_success_response 解析 API 返回的 JSON body"""

    def test_basic_parsing(self):
        body = {
            "model": "deepseek-v4-flash",
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "prompt_cache_hit_tokens": 20,
                "prompt_cache_miss_tokens": 80,
            },
            "choices": [
                {"message": {"content": "Hello, world!"}}
            ],
        }
        result = _parse_success_response(body, "deepseek-v4-flash", "deepseek",
                                         0.5, "test", "Claw")
        assert result["response"] == "Hello, world!"
        assert result["input_tokens"] == 100
        assert result["output_tokens"] == 50
        assert result["prompt_cache_hit_tokens"] == 20
        assert result["prompt_cache_miss_tokens"] == 80
        assert result["model"] == "deepseek-v4-flash"

    def test_minimal_response(self):
        """最简响应（无 usage/无 choices）不崩溃"""
        body = {}
        result = _parse_success_response(body, "default-model", "provider",
                                         4.0, "t", "p")
        assert result["response"] == ""
        assert result["input_tokens"] == 0
        assert result["output_tokens"] == 0
        assert result["model"] == "default-model"
