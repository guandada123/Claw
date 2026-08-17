"""
router.py 路由与统一 API 调用层 — P1-2 mock 测试
==================================================
覆盖：route_task / get_model / call_llm / call_with_fallback
     _resolve_api_config / _build_chat_messages / _make_error_resp
     _select_premium_model / _parse_success_response / _try_log_call
     run_routing_test
"""

import json
import sys
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

# ================================================================
# Mock 注入（必须在 import router 之前）
# router.py 在模块级执行 sys.path.insert + from cost_tracker/local_model/secrets import
# ================================================================
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

# --- mock cost_tracker ---
mock_cost_tracker = MagicMock()
mock_cost_tracker.MODEL_PRICES = {
    "deepseek-v4-flash": {"input": 0.25, "output": 0.5},
    "deepseek-v4-pro": {"input": 2.0, "output": 4.0},
    "gpt-5": {"input": 10.8, "output": 21.6},
    "claude-sonnet-4-20250514": {"input": 10.8, "output": 21.6},
    "claude-opus-4-20250514": {"input": 18.0, "output": 36.0},
    "gpt-4.1": {"input": 9.0, "output": 18.0},
}
mock_cost_tracker._match_model = lambda m: m
mock_cost_tracker.log_call = MagicMock()
sys.modules["cost_tracker"] = mock_cost_tracker

# --- mock local_model ---
mock_local_model = MagicMock()
mock_local_model.call = MagicMock(
    return_value={
        "success": True,
        "response": "本地模型回复",
        "prompt_tokens": 10,
        "response_tokens": 5,
    }
)
mock_local_model.is_available = MagicMock(return_value=False)
sys.modules["local_model"] = mock_local_model

# --- mock secrets ---
mock_secrets = MagicMock()
mock_secrets.CATROUTER_API_KEY = "test-catrouter-key"
mock_secrets.CATROUTER_BASE_URL = "https://api.catrouter.net/v1"
mock_secrets.DEEPSEEK_API_KEY = "test-deepseek-key"
mock_secrets.DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
mock_secrets.randbelow = lambda n: 0  # 确定性 jitter，避免 MagicMock < int 比较错误
sys.modules["secrets"] = mock_secrets

# --- mock legacy_secrets ---
# 🔴 根因修复：router.py 优先 `from legacy_secrets import ...`，而原测试只 mock 了
# `secrets`，对 `legacy_secrets` 无影响 → CI/本地都拿不到测试假值 → 断言失败。
# 必须把 `legacy_secrets` 也 mock 成同一套测试假值，routers 导入时才能拿到 test-*-key。
mock_legacy_secrets = MagicMock()
mock_legacy_secrets.CATROUTER_API_KEY = "test-catrouter-key"
mock_legacy_secrets.CATROUTER_BASE_URL = "https://api.catrouter.net/v1"
mock_legacy_secrets.DEEPSEEK_API_KEY = "test-deepseek-key"
mock_legacy_secrets.DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
sys.modules["legacy_secrets"] = mock_legacy_secrets

# 现在安全导入 router
from router import (  # noqa: E402 — mock 注入必须在 import 之前
    FALLBACK_CHAIN,
    ROUTING_RULES,
    ModelTier,
    _build_chat_messages,
    _make_error_resp,
    _parse_success_response,
    _resolve_api_config,
    _select_premium_model,
    _try_log_call,
    call_llm,
    call_with_fallback,
    get_model,
    route_task,
    run_routing_test,
)

# ================================================================
# 测试隔离：清理进程级 sys.modules mock 注入
# 本文件在模块顶层把 cost_tracker/local_model/secrets/legacy_secrets 替换成 MagicMock
# （router.py 在模块级 from 这些包 import，必须在 import 前注入）。
# 但若本文件先于其他测试（如 test_local_model）执行，这些 sys.modules 替换会残留，
# 导致后续 import local_model 拿到 MagicMock 而非真实模块 → 跨测试污染。
# 用 autouse fixture 在每个用例后还原（del sys.modules），让后续测试重新加载真实模块。
# 注：router 自身用例已在模块加载时绑定了引用，del sys.modules 不影响本文件内用例。
import pytest


@pytest.fixture(autouse=True, scope="function")
def _restore_sys_modules():
    yield
    for mod in ("cost_tracker", "local_model", "secrets", "legacy_secrets"):
        sys.modules.pop(mod, None)


# ================================================================
# P1: route_task — 核心路由
# ================================================================


class TestRouteTask:
    """P1: route_task 四层路由准确性、force_tier、allow_local。"""

    def test_route_to_local_for_heartbeat(self):
        """心跳/健康检查 → LOCAL。"""
        assert route_task("heartbeat check") == ModelTier.LOCAL
        assert route_task("健康检查") == ModelTier.LOCAL
        assert route_task("系统巡检") == ModelTier.LOCAL

    def test_route_to_local_for_simple_boolean(self):
        """简单布尔判断 → LOCAL。"""
        assert route_task("判断这个服务是否在线") == ModelTier.LOCAL

    def test_route_to_flash_for_translation(self):
        """翻译/摘要 → FLASH。"""
        assert route_task("翻译成英文：今天天气不错") == ModelTier.FLASH
        assert route_task("总结这篇新闻的要点") == ModelTier.FLASH

    def test_route_to_flash_for_data_cleanup(self):
        """数据清洗/初筛 → FLASH。"""
        assert route_task("整理这份股票列表，只保留上涨的") == ModelTier.FLASH
        assert route_task("帮我格式化这段代码") == ModelTier.FLASH

    def test_route_to_flash_for_sector_rank(self):
        """板块排行 → FLASH。"""
        assert route_task("今天哪个板块涨幅最大") == ModelTier.FLASH

    def test_route_to_flash_for_push_template(self):
        """飞书推送模板 → FLASH。"""
        assert route_task("生成飞书推送消息模板") == ModelTier.FLASH

    def test_route_to_pro_for_stock_analysis(self):
        """个股分析 → PRO。"""
        assert route_task("分析000001的技术形态，给出建议") == ModelTier.PRO

    def test_route_to_pro_for_backtest(self):
        """回测分析 → PRO。"""
        assert route_task("这个股票回测结果怎么样") == ModelTier.PRO

    def test_route_to_pro_for_impl(self):
        """策略实现/调试 → PRO。"""
        assert route_task("实现一个选股策略函数") == ModelTier.PRO
        assert route_task("帮我调试这个bug") == ModelTier.PRO

    def test_route_to_pro_for_api_design(self):
        """接口设计 → PRO。"""
        assert route_task("设计一个风控接口，需要止损逻辑") == ModelTier.PRO

    def test_route_to_premium_for_code_review(self):
        """代码审查 → PREMIUM。"""
        assert route_task("帮我审查这整个模块的代码") == ModelTier.PREMIUM

    def test_route_to_premium_for_architecture(self):
        """架构设计 → PREMIUM。"""
        assert route_task("设计交易系统的整体架构") == ModelTier.PREMIUM
        assert route_task("方案设计：微服务还是单体") == ModelTier.PREMIUM

    def test_route_to_premium_for_security_review(self):
        """安全审查 → PREMIUM。"""
        assert route_task("审查核心策略逻辑的安全性") == ModelTier.PREMIUM

    def test_route_to_premium_for_docs(self):
        """文档撰写/技术文档 → PREMIUM。"""
        assert route_task("写一份系统架构文档") == ModelTier.PREMIUM

    def test_route_to_premium_for_review_plan(self):
        """方案评审 → PREMIUM。"""
        assert route_task("方案评审：这三种技术方案的对比") == ModelTier.PREMIUM

    def test_force_tier_overrides_routing(self):
        """force_tier 覆盖所有路由规则。"""
        assert route_task("架构设计", force_tier=ModelTier.FLASH) == ModelTier.FLASH
        assert route_task("心跳检查", force_tier=ModelTier.PRO) == ModelTier.PRO
        assert route_task("翻译", force_tier=ModelTier.PREMIUM) == ModelTier.PREMIUM

    def test_allow_local_false_skips_local(self):
        """allow_local=False 时跳过 LOCAL 层，升级到 FLASH 或更高。"""
        # "健康检查" 本应路由到 LOCAL，但 allow_local=False 跳过
        result = route_task("健康检查", allow_local=False)
        assert result != ModelTier.LOCAL
        # 应落到 FLASH（匹配"数据清洗"模式的更低层）
        assert result in (ModelTier.FLASH, ModelTier.PRO)

    def test_default_to_pro_when_no_match(self):
        """无匹配时默认 PRO（宁可保守路由）。"""
        result = route_task("xyzzy 随机无意义文本 12345")
        assert result == ModelTier.PRO

    def test_premium_signal_case_insensitive(self):
        """PREMIUM 信号词大小写不敏感（用中文变体验证）。"""
        # PREMIUM_SIGNALS 里是 "代码审查"/"代码Review"，用大小写变体测 IGNORECASE
        assert route_task("代码review") == ModelTier.PREMIUM
        assert route_task("架构设计") == ModelTier.PREMIUM

    def test_routing_with_task_type(self):
        """task_type 参数参与匹配。"""
        # prompt 本身不匹配，但 task_type 命中
        result = route_task("看看这个", task_type="代码审查")
        assert result == ModelTier.PREMIUM

    def test_all_regex_patterns_in_routing_rules_valid(self):
        """ROUTING_RULES 中所有正则均可编译。"""
        import re

        for tier, patterns in ROUTING_RULES.items():
            for p in patterns:
                re.compile(p)  # 不应抛异常

    def test_local_rules_match_known_inputs(self):
        """LOCAL 层规则覆盖已知场景。"""
        local_cases = [
            "heartbeat",
            "ping",
            "is alive",
            "健康检查",
            "服务状态",
            "是否在线",
            "存活检查",
            "巡检",
            "daily check",
            "status check",
            "系统检查",
            "环境检查",
            "JSON格式化",
            "json format",
            "格式美化",
            "pretty print",
            "是否",
            "是不是",
            "true or false",
            "yes or no",
            "提取数字",
            "提取日期",
            "提取字段",
            "extract name from",
        ]
        for case in local_cases:
            assert route_task(case) == ModelTier.LOCAL, f"'{case}' 应路由到 LOCAL"


# ================================================================
# P1: get_model — 预算感知路由
# ================================================================


class TestGetModel:
    """P1: get_model 模型选择、预算降级、local_available 控制。"""

    def test_returns_local_config(self):
        """心跳 prompt → LOCAL 配置。"""
        # 让 local_model 可用
        mock_local_model.is_available.return_value = True
        config = get_model("heartbeat check", local_available=True)
        assert config["tier"] == ModelTier.LOCAL
        assert config["provider"] == "ollama"
        assert config["model"] == "qwen2.5:7b"
        assert config["cost_per_10k"] == 0.0

    def test_returns_flash_config(self):
        """翻译 prompt → FLASH 配置。"""
        config = get_model("翻译成英文")
        assert config["tier"] == ModelTier.FLASH
        assert config["model"] == "deepseek-v4-flash"
        assert config["provider"] == "deepseek"
        assert config["cost_per_10k"] == 0.5

    def test_returns_pro_config(self):
        """个股分析 → PRO 配置。"""
        config = get_model("分析平安银行技术形态")
        assert config["tier"] == ModelTier.PRO
        assert config["model"] == "deepseek-v4-pro"
        assert config["cost_per_10k"] == 4.0

    def test_returns_premium_config(self):
        """架构设计 → PREMIUM 配置。"""
        config = get_model("设计交易系统架构")
        assert config["tier"] == ModelTier.PREMIUM
        # PREMIUM 使用 _select_premium_model，架构=GPT-5
        assert config["model"] == "gpt-5"

    def test_budget_flash_only_downgrades_pro(self):
        """flash_only 预算 → PRO 降级为 FLASH。"""
        config = get_model(
            "分析平安银行技术形态",
            budget_status={"tier": "flash_only", "spent": 370, "remaining": 30},
        )
        assert config["tier"] == ModelTier.FLASH

    def test_budget_flash_only_downgrades_premium(self):
        """flash_only 预算 → PREMIUM 降级为 FLASH。"""
        config = get_model(
            "设计交易系统架构",
            budget_status={"tier": "flash_only", "spent": 380, "remaining": 20},
        )
        assert config["tier"] == ModelTier.FLASH

    def test_budget_flash_only_keeps_flash(self):
        """flash_only 预算 → FLASH 不降级。"""
        config = get_model(
            "翻译成英文",
            budget_status={"tier": "flash_only", "spent": 370, "remaining": 30},
        )
        assert config["tier"] == ModelTier.FLASH

    def test_budget_flash_only_keeps_local(self):
        """flash_only 预算 → LOCAL 不受影响。"""
        mock_local_model.is_available.return_value = True
        config = get_model(
            "heartbeat",
            budget_status={"tier": "flash_only", "spent": 370, "remaining": 30},
            local_available=True,
        )
        assert config["tier"] == ModelTier.LOCAL

    def test_budget_flash_preferred_downgrades_pro(self):
        """flash_preferred 预算 → PRO 降级为 FLASH。"""
        config = get_model(
            "分析平安银行技术形态",
            budget_status={"tier": "flash_preferred", "spent": 300, "remaining": 100},
        )
        assert config["tier"] == ModelTier.FLASH

    def test_budget_flash_preferred_keeps_premium(self):
        """flash_preferred 预算 → PREMIUM 不降级（仅降 PRO）。"""
        config = get_model(
            "设计交易系统架构",
            budget_status={"tier": "flash_preferred", "spent": 300, "remaining": 100},
        )
        assert config["tier"] == ModelTier.PREMIUM

    def test_budget_normal_no_change(self):
        """normal 预算 → 无变化。"""
        config = get_model(
            "分析平安银行技术形态",
            budget_status={"tier": "normal", "spent": 100, "remaining": 300},
        )
        assert config["tier"] == ModelTier.PRO

    def test_no_budget_status(self):
        """无 budget_status → 正常路由。"""
        config = get_model("分析平安银行技术形态")
        assert config["tier"] == ModelTier.PRO

    def test_local_available_false_disables_local(self):
        """local_available=False → 即使匹配 LOCAL 规则也升级。"""
        mock_local_model.is_available.return_value = False
        config = get_model("heartbeat check", local_available=False)
        assert config["tier"] != ModelTier.LOCAL

    def test_local_available_auto_detection(self):
        """local_available=None → 自动调用 _local_check()。"""
        mock_local_model.is_available.return_value = False
        config = get_model("heartbeat check", local_available=None)
        assert config["tier"] != ModelTier.LOCAL  # 本地不可用，升级

    def test_result_contains_all_required_keys(self):
        """get_model 返回 dict 包含所有必要字段。"""
        config = get_model("翻译")
        required_keys = {"model", "provider", "tier", "base_url", "cost_per_10k", "note"}
        assert required_keys.issubset(config.keys())


# ================================================================
# P1: call_llm — HTTP 错误处理
# ================================================================


class TestCallLLM:
    """P1: call_llm HTTPError / URLError / Exception 处理。"""

    def _flash_config(self):
        return {"model": "deepseek-v4-flash", "provider": "deepseek", "cost_per_10k": 0.5}

    def test_http_error_handling(self):
        """HTTP 错误（如 429）→ 返回 error_resp。"""
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.HTTPError(
                url="https://api.deepseek.com/v1/chat/completions",
                code=429,
                msg="Too Many Requests",
                hdrs={},
                fp=MagicMock(),
            )
            # HTTPError.read() 返回错误体
            mock_urlopen.side_effect.read = MagicMock(return_value=b'{"error":"rate limited"}')

            result = call_llm("测试", self._flash_config())

        assert result["success"] is False
        assert "429" in result["error"]
        assert result["model"] == "deepseek-v4-flash"
        assert result["response"] is None
        assert result["cost_cny"] == 0.0

    def test_url_error_handling(self):
        """网络连接失败 → 返回 error_resp。"""
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

            result = call_llm("测试", self._flash_config())

        assert result["success"] is False
        assert "连接失败" in result["error"]
        assert result["model"] == "deepseek-v4-flash"

    def test_general_exception_handling(self):
        """其他异常 → 返回 error_resp。"""
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = ValueError("Unexpected error")

            result = call_llm("测试", self._flash_config())

        assert result["success"] is False
        assert "Unexpected error" in result["error"]
        assert result["model"] == "deepseek-v4-flash"

    def test_successful_api_call(self):
        """正常 API 调用 → 解析响应并记录成本。"""
        fake_response = {
            "model": "deepseek-v4-flash",
            "choices": [{"message": {"content": "你好，我是DeepSeek。"}}],
            "usage": {
                "prompt_tokens": 50,
                "completion_tokens": 30,
                "prompt_cache_hit_tokens": 20,
                "prompt_cache_miss_tokens": 30,
            },
        }

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(fake_response).encode("utf-8")
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            result = call_llm("测试", self._flash_config(), task="test", project="Claw")

        assert result["success"] is True
        assert result["response"] == "你好，我是DeepSeek。"
        assert result["model"] == "deepseek-v4-flash"
        assert result["input_tokens"] == 50
        assert result["output_tokens"] == 30
        assert result["cost_cny"] > 0
        assert result["prompt_cache_hit_tokens"] == 20
        assert result["prompt_cache_miss_tokens"] == 30
        assert "duration_ms" in result
        assert result["error"] is None

    def test_success_cost_calculation(self):
        """验证成本计算公式正确。"""
        fake_response = {
            "model": "deepseek-v4-flash",
            "choices": [{"message": {"content": "OK"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 200},
        }

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(fake_response).encode("utf-8")
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            result = call_llm("测试", self._flash_config())

        # Flash: input=0.25/万, output=0.5/万
        expected = (100 * 0.25 + 200 * 0.5) / 10000
        assert result["cost_cny"] == round(expected, 6)

    def test_ollama_local_model_call(self):
        """provider=ollama → 委托 local_model.py。"""
        config = {"model": "qwen2.5:7b", "provider": "ollama", "cost_per_10k": 0.0}

        mock_local_model.call.return_value = {
            "success": True,
            "response": "本地模型回复",
            "prompt_tokens": 10,
            "response_tokens": 5,
        }

        result = call_llm("测试", config)

        assert result["success"] is True
        assert result["response"] == "本地模型回复"
        assert result["provider"] == "ollama"
        assert result["cost_cny"] == 0.0

    def test_ollama_failure(self):
        """Ollama 调用失败 → error_resp。"""
        config = {"model": "qwen2.5:7b", "provider": "ollama", "cost_per_10k": 0.0}

        mock_local_model.call.return_value = {"success": False, "error": "模型未加载"}

        result = call_llm("测试", config)

        assert result["success"] is False
        assert "模型未加载" in result["error"]


# ================================================================
# P1: call_with_fallback — Fallback 链
# ================================================================


class TestCallWithFallback:
    """P1: call_with_fallback 主路径成功、fallback 激活、全部失败、去重。"""

    def _success_call(self, *args, **kwargs):
        return {
            "success": True,
            "response": "成功回复",
            "model": kwargs.get("model_config", {}).get("model", "unknown"),
            "provider": kwargs.get("model_config", {}).get("provider", "unknown"),
            "input_tokens": 50,
            "output_tokens": 30,
            "cost_cny": 0.001,
            "prompt_cache_hit_tokens": None,
            "prompt_cache_miss_tokens": None,
            "duration_ms": 100,
            "error": None,
        }

    def _fail_call(self, *args, **kwargs):
        return {
            "success": False,
            "response": None,
            "model": kwargs.get("model_config", {}).get("model", "unknown"),
            "provider": kwargs.get("model_config", {}).get("provider", "unknown"),
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_cny": 0.0,
            "prompt_cache_hit_tokens": None,
            "prompt_cache_miss_tokens": None,
            "duration_ms": 50,
            "error": "模拟失败",
        }

    def test_primary_succeeds_no_fallback(self):
        """首选模型成功 → 不触发 fallback。"""
        with patch("router.get_model") as mock_get, patch("router.call_llm") as mock_call:
            mock_get.return_value = {
                "model": "deepseek-v4-pro",
                "provider": "deepseek",
                "tier": ModelTier.PRO,
                "base_url": "https://api.deepseek.com/v1",
                "cost_per_10k": 4.0,
            }
            mock_call.side_effect = self._success_call

            result = call_with_fallback("分析股票", verbose=False)

        assert result["success"] is True
        assert result["model"] == "deepseek-v4-pro"
        assert result["fallback_used"] is False
        assert len(result["attempts"]) == 1

    def test_fallback_activates_on_primary_failure(self):
        """首选失败 → fallback 链激活 → 第二个成功。"""
        with patch("router.get_model") as mock_get, patch("router.call_llm") as mock_call:
            mock_get.return_value = {
                "model": "deepseek-v4-pro",
                "provider": "deepseek",
                "tier": ModelTier.PRO,
                "base_url": "https://api.deepseek.com/v1",
                "cost_per_10k": 4.0,
            }
            # 第1次失败，第2次成功
            mock_call.side_effect = [self._fail_call({}), self._success_call({})]

            result = call_with_fallback("分析股票", verbose=False)

        assert result["success"] is True
        assert result["fallback_used"] is True
        assert len(result["attempts"]) == 2
        assert result["attempts"][0]["success"] is False
        assert result["attempts"][1]["success"] is True

    def test_all_candidates_fail(self):
        """全部候选失败 → 返回错误。"""
        with patch("router.get_model") as mock_get, patch("router.call_llm") as mock_call:
            mock_get.return_value = {
                "model": "deepseek-v4-pro",
                "provider": "deepseek",
                "tier": ModelTier.PRO,
                "base_url": "https://api.deepseek.com/v1",
                "cost_per_10k": 4.0,
            }
            # 全部失败（primary + 4 fallback = 5 candidates，deepseek-v4-pro 被排除了）
            failures = [self._fail_call({}) for _ in range(5)]
            mock_call.side_effect = failures

            result = call_with_fallback("分析股票", verbose=False)

        assert result["success"] is False
        assert "全部" in result.get("error", "")
        assert len(result["attempts"]) == 5
        assert result["fallback_used"] is True

    def test_candidate_deduplication(self):
        """primary 已在 FALLBACK_CHAIN 中时去重。"""
        with patch("router.get_model") as mock_get, patch("router.call_llm") as mock_call:
            # primary 设为与 FALLBACK_CHAIN[0] 相同的模型
            mock_get.return_value = {
                "model": "claude-opus-4-20250514",
                "provider": "catrouter",
                "tier": ModelTier.PREMIUM,
                "base_url": "https://api.catrouter.net/v1",
                "cost_per_10k": 36.0,
            }
            mock_call.side_effect = [self._fail_call({}), self._success_call({})]

            result = call_with_fallback("分析股票", verbose=False)

        assert result["success"] is True
        # primary 出现后，FALLBACK_CHAIN 中相同 model 应被去重
        # claude-opus-4 只应出现一次
        models_in_attempts = [a["model"] for a in result["attempts"]]
        assert models_in_attempts.count("claude-opus-4-20250514") == 1

    def test_attempts_record_all_fields(self):
        """每次 attempt 包含完整字段。"""
        with patch("router.get_model") as mock_get, patch("router.call_llm") as mock_call:
            mock_get.return_value = {
                "model": "deepseek-v4-pro",
                "provider": "deepseek",
                "tier": ModelTier.PRO,
                "base_url": "https://api.deepseek.com/v1",
                "cost_per_10k": 4.0,
            }
            mock_call.side_effect = self._success_call

            result = call_with_fallback("测试", verbose=False)

        attempt = result["attempts"][0]
        required_keys = {"model", "provider", "success", "error", "duration_ms", "cost_cny"}
        for key in required_keys:
            assert key in attempt, f"attempt 缺少字段: {key}"

    def test_tier_in_result(self):
        """返回结果包含 tier 信息。"""
        with patch("router.get_model") as mock_get, patch("router.call_llm") as mock_call:
            mock_get.return_value = {
                "model": "deepseek-v4-flash",
                "provider": "deepseek",
                "tier": ModelTier.FLASH,
                "base_url": "https://api.deepseek.com/v1",
                "cost_per_10k": 0.5,
            }
            mock_call.side_effect = self._success_call

            result = call_with_fallback("翻译", verbose=False)

        assert result["tier"] == "deepseek-v4-flash"

    def test_fallback_chain_excludes_primary(self):
        """FALLBACK_CHAIN 中与 primary 相同的模型被排除。"""
        # 验证 FALLBACK_CHAIN[3] 是 deepseek-v4-pro
        assert FALLBACK_CHAIN[3]["model"] == "deepseek-v4-pro"

        with patch("router.get_model") as mock_get, patch("router.call_llm") as mock_call:
            mock_get.return_value = {
                "model": "deepseek-v4-pro",
                "provider": "deepseek",
                "tier": ModelTier.PRO,
                "base_url": "https://api.deepseek.com/v1",
                "cost_per_10k": 4.0,
            }
            mock_call.side_effect = self._success_call

            result = call_with_fallback("测试", verbose=False)

        # deepseek-v4-pro 只应在 primary 中出现一次
        pro_count = sum(1 for a in result["attempts"] if a["model"] == "deepseek-v4-pro")
        assert pro_count == 1


# ================================================================
# P2: _resolve_api_config — API Key/URL 解析
# ================================================================


class TestResolveApiConfig:
    """P2: _resolve_api_config 根据 provider 返回正确的 Key/URL。"""

    def test_deepseek_provider(self):
        """deepseek provider → DEEPSEEK Key + Base URL。"""
        key, url = _resolve_api_config("deepseek", {})
        assert key == "test-deepseek-key"
        assert url == "https://api.deepseek.com/v1"

    def test_catrouter_provider(self):
        """catrouter provider → CATROUTER Key + Base URL。"""
        key, url = _resolve_api_config("catrouter", {})
        assert key == "test-catrouter-key"
        assert url == "https://api.catrouter.net/v1"

    def test_premium_provider(self):
        """premium provider → CATROUTER Key + Base URL（通过 catrouter 代理）。"""
        key, url = _resolve_api_config("premium", {})
        assert key == "test-catrouter-key"
        assert url == "https://api.catrouter.net/v1"

    def test_unknown_provider_falls_back_to_deepseek(self):
        """未知 provider → DEEPSEEK Key + model_config 中的 base_url。"""
        key, url = _resolve_api_config("unknown", {"base_url": "https://custom.api/v1"})
        assert key == "test-deepseek-key"
        # model_config 有 base_url 时使用之
        assert url == "https://custom.api/v1"

    def test_unknown_provider_default_url(self):
        """未知 provider 且无 base_url → DEEPSEEK Base URL。"""
        key, url = _resolve_api_config("unknown", {})
        assert key == "test-deepseek-key"
        assert url == "https://api.deepseek.com/v1"


# ================================================================
# P2: _build_chat_messages — 消息构建
# ================================================================


class TestBuildChatMessages:
    """P2: _build_chat_messages 消息列表构建。"""

    def test_with_system_prompt(self):
        """有 system prompt → 两条消息。"""
        messages = _build_chat_messages("你是一个助手", "用户输入")
        assert len(messages) == 2
        assert messages[0] == {"role": "system", "content": "你是一个助手"}
        assert messages[1] == {"role": "user", "content": "用户输入"}

    def test_without_system_prompt(self):
        """无 system prompt → 仅一条 user 消息。"""
        messages = _build_chat_messages("", "用户输入")
        assert len(messages) == 1
        assert messages[0] == {"role": "user", "content": "用户输入"}

    def test_system_none(self):
        """system=None → 仅 user 消息。"""
        messages = _build_chat_messages(None, "用户输入")
        assert len(messages) == 1

    def test_empty_prompt(self):
        """空 prompt → 仍产生 user 消息。"""
        messages = _build_chat_messages("sys", "")
        assert len(messages) == 2
        assert messages[1] == {"role": "user", "content": ""}


# ================================================================
# P2: _make_error_resp — 错误响应工厂
# ================================================================


class TestMakeErrorResp:
    """P2: _make_error_resp 错误响应结构验证。"""

    def test_structure_contains_all_keys(self):
        """错误响应包含所有定义字段。"""
        resp = _make_error_resp("m1", "p1", 123, "error msg")

        expected_keys = {
            "success",
            "response",
            "model",
            "provider",
            "input_tokens",
            "output_tokens",
            "cost_cny",
            "prompt_cache_hit_tokens",
            "prompt_cache_miss_tokens",
            "duration_ms",
            "error",
        }
        assert set(resp.keys()) == expected_keys

    def test_success_is_always_false(self):
        """错误响应 success 始终为 False。"""
        resp = _make_error_resp("any", "any", 0, "any")
        assert resp["success"] is False

    def test_response_is_none(self):
        """错误响应 response 始终为 None。"""
        resp = _make_error_resp("x", "y", 0, "z")
        assert resp["response"] is None

    def test_tokens_zero(self):
        """错误响应 Token 数均为 0。"""
        resp = _make_error_resp("m", "p", 0, "e")
        assert resp["input_tokens"] == 0
        assert resp["output_tokens"] == 0
        assert resp["cost_cny"] == 0.0

    def test_model_and_provider_preserved(self):
        """model/provider 原样保留。"""
        resp = _make_error_resp("gpt-5", "premium", 500, "超时")
        assert resp["model"] == "gpt-5"
        assert resp["provider"] == "premium"

    def test_cache_tokens_none(self):
        """错误响应缓存 Token 为 None。"""
        resp = _make_error_resp("m", "p", 0, "e")
        assert resp["prompt_cache_hit_tokens"] is None
        assert resp["prompt_cache_miss_tokens"] is None


# ================================================================
# P2: _select_premium_model — PREMIUM 模型选择
# ================================================================


class TestSelectPremiumModel:
    """P2: _select_premium_model 代码审查→Claude，其他→GPT-5。"""

    def test_code_review_selects_claude(self):
        """代码审查 → Claude Sonnet 4。"""
        config = _select_premium_model("帮我审查代码", "代码审查")
        assert config["model"] == "claude-sonnet-4-20250514"
        assert config["provider"] == "premium"
        assert config["cost_per_10k"] == 21.6

    def test_prompt_contains_code_review_selects_claude(self):
        """prompt 中含"代码审查"→ Claude。"""
        config = _select_premium_model("进行代码审查", "")
        assert config["model"] == "claude-sonnet-4-20250514"

    def test_other_selects_gpt5(self):
        """其他场景 → GPT-5。"""
        config = _select_premium_model("设计架构", "架构设计")
        assert config["model"] == "gpt-5"
        assert config["provider"] == "premium"
        assert config["cost_per_10k"] == 21.6

    def test_config_contains_note(self):
        """PREMIUM 配置包含说明。"""
        config = _select_premium_model("测试", "")
        assert "note" in config
        assert "架构" in config["note"] or "审查" in config["note"]


# ================================================================
# P2: _try_log_call — 成本日志容错
# ================================================================


class TestTryLogCall:
    """P2: _try_log_call 正常记录 + 异常吞没。"""

    def test_log_call_invoked(self):
        """正常路径 → _log_call 被调用。"""
        mock_cost_tracker.log_call.reset_mock()
        _try_log_call("deepseek-v4-flash", 100, 50, "test_task", "Claw")
        mock_cost_tracker.log_call.assert_called_once()

    def test_log_call_exception_suppressed(self, capsys):
        """_log_call 抛异常 → 被吞没，输出警告。"""
        mock_cost_tracker.log_call.side_effect = RuntimeError("DB连接失败")

        # 不应抛出异常
        _try_log_call("deepseek-v4-flash", 100, 50, "test", "Claw")

        captured = capsys.readouterr()
        assert "cost_tracker 日志失败" in captured.out

        # 恢复 mock
        mock_cost_tracker.log_call.side_effect = None

    def test_log_call_receives_cache_tokens(self):
        """cache hit/miss token 正确传递。"""
        mock_cost_tracker.log_call.reset_mock()
        _try_log_call(
            "deepseek-v4-flash", 100, 50, "task", "project", hit_tokens=30, miss_tokens=70
        )
        call_kwargs = mock_cost_tracker.log_call.call_args.kwargs
        assert call_kwargs["prompt_cache_hit_tokens"] == 30
        assert call_kwargs["prompt_cache_miss_tokens"] == 70


# ================================================================
# P2: _parse_success_response — 响应解析
# ================================================================


class TestParseSuccessResponse:
    """P2: _parse_success_response 响应解析、成本计算、usage 字段兼容。"""

    def test_standard_response_parsing(self):
        """标准响应 → 正确提取 content 和 token 数。"""
        body = {
            "model": "deepseek-v4-flash",
            "choices": [{"message": {"content": "Hello"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        }
        result = _parse_success_response(body, "deepseek-v4-flash", "deepseek", 0.5, "task", "Claw")
        assert result["response"] == "Hello"
        assert result["input_tokens"] == 10
        assert result["output_tokens"] == 20

    def test_new_usage_field_names(self):
        """兼容 input_tokens/output_tokens 字段名。"""
        body = {
            "model": "m",
            "choices": [{"message": {"content": "x"}}],
            "usage": {"input_tokens": 5, "output_tokens": 15},
        }
        result = _parse_success_response(body, "m", "p", 0.5, "t", "Claw")
        assert result["input_tokens"] == 5
        assert result["output_tokens"] == 15

    def test_missing_usage_defaults_zero(self):
        """无 usage 字段 → token 默认为 0。"""
        body = {"model": "m", "choices": [{"message": {"content": "x"}}]}
        result = _parse_success_response(body, "m", "p", 0.5, "t", "Claw")
        assert result["input_tokens"] == 0
        assert result["output_tokens"] == 0
        assert result["cost_cny"] == 0.0

    def test_cost_calculation(self):
        """成本 = (input*price_input + output*price_output) / 10000。"""
        body = {
            "model": "deepseek-v4-pro",
            "choices": [{"message": {"content": "x"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 200},
        }
        result = _parse_success_response(body, "deepseek-v4-pro", "deepseek", 4.0, "t", "Claw")
        # PRO: input=2.0, output=4.0 (from mock_cost_tracker.MODEL_PRICES)
        expected = (100 * 2.0 + 200 * 4.0) / 10000
        assert result["cost_cny"] == round(expected, 6)

    def test_cache_tokens_preserved(self):
        """cache hit/miss token 正确传递。"""
        body = {
            "choices": [{"message": {"content": "x"}}],
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "prompt_cache_hit_tokens": 10,
                "prompt_cache_miss_tokens": 5,
            },
        }
        result = _parse_success_response(body, "m", "p", 0.5, "t", "Claw")
        assert result["prompt_cache_hit_tokens"] == 10
        assert result["prompt_cache_miss_tokens"] == 5


# ================================================================
# P2: run_routing_test — 内置路由测试
# ================================================================


class TestRoutingTest:
    """P2: run_routing_test 内置22用例≥80%准确率。"""

    def test_accuracy_at_least_80(self):
        """准确率 ≥ 80%（22 用例）。"""
        accuracy = run_routing_test()
        assert accuracy >= 80.0, f"路由准确率 {accuracy:.1f}% < 80%"

    def test_returns_float(self):
        """返回值是 float。"""
        accuracy = run_routing_test()
        assert isinstance(accuracy, float)


# ================================================================
# P2: ModelTier 枚举
# ================================================================


class TestModelTier:
    """P2: ModelTier 枚举值验证。"""

    def test_tier_values(self):
        """确认四个层级的值。"""
        assert ModelTier.LOCAL.value == "ollama-local"
        assert ModelTier.FLASH.value == "deepseek-v4-flash"
        assert ModelTier.PRO.value == "deepseek-v4-pro"
        assert ModelTier.PREMIUM.value == "premium"

    def test_tier_str(self):
        """__str__ 返回 value。"""
        assert str(ModelTier.LOCAL) == "ollama-local"
        assert str(ModelTier.FLASH) == "deepseek-v4-flash"


# ================================================================
# P2: FALLBACK_CHAIN 结构验证
# ================================================================


class TestFallbackChain:
    """P2: FALLBACK_CHAIN 结构完整性。"""

    def test_chain_length(self):
        """FALLBACK_CHAIN 应有 5 个候选（从贵到便宜）。"""
        assert len(FALLBACK_CHAIN) == 5

    def test_chain_descending_cost(self):
        """Fallback 链应从贵到便宜排序。"""
        first = FALLBACK_CHAIN[0]
        last = FALLBACK_CHAIN[-1]
        # 第一个是 claude-opus-4（¥36），最后是 deepseek-v4-flash（¥0.5）
        assert first["model"] == "claude-opus-4-20250514"
        assert last["model"] == "deepseek-v4-flash"

    def test_each_entry_has_required_keys(self):
        """每个 FALLBACK_CHAIN 条目包含 provider/model/timeout。"""
        for entry in FALLBACK_CHAIN:
            for key in ("provider", "model", "timeout"):
                assert key in entry, f"FALLBACK_CHAIN 条目缺少 {key}"
