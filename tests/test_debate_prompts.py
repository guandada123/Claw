"""测试 src/claw/debate/expert_prompts.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from claw.debate.expert_prompts import (
    EXPERT_DEFINITIONS,
    build_system_prompt,
    build_user_prompt,
)


class TestExpertDefinitions:
    def test_all_7_experts_defined(self):
        assert len(EXPERT_DEFINITIONS) == 7
        expected = [
            "fundamental", "technical", "fund_flow",
            "sentiment", "valuation", "risk_ctrl", "synthesis",
        ]
        for key in expected:
            assert key in EXPERT_DEFINITIONS
            assert "name" in EXPERT_DEFINITIONS[key]
            assert "role" in EXPERT_DEFINITIONS[key]
            assert "framework" in EXPERT_DEFINITIONS[key]
            assert len(EXPERT_DEFINITIONS[key]["framework"]) >= 3

    def test_each_expert_has_unique_name(self):
        names = [e["name"] for e in EXPERT_DEFINITIONS.values()]
        assert len(names) == len(set(names))


class TestBuildSystemPrompt:
    def test_output_contains_stance_json_format(self):
        prompt = build_system_prompt("fundamental")
        assert "stance" in prompt
        assert "confidence" in prompt
        assert "reasoning" in prompt
        assert "JSON" in prompt
        assert "risk_flags" in prompt

    def test_output_for_all_experts(self):
        for key in EXPERT_DEFINITIONS:
            prompt = build_system_prompt(key)
            assert len(prompt) > 100
            assert "框架" in prompt or "framework" in prompt.lower() or "分析" in prompt

    def test_invalid_key_raises(self):
        import pytest
        with pytest.raises(KeyError):
            build_system_prompt("nonexistent")


class TestBuildUserPrompt:
    def test_minimal_data(self):
        prompt = build_user_prompt("000333", "美的集团", {"price": 85.0})
        assert "000333" in prompt
        assert "美的集团" in prompt
        assert "85.0" in prompt

    def test_full_data(self):
        data = {
            "price": 87.01,
            "change_pct": 1.92,
            "sector": "家用电器",
            "market_cap": "5988亿",
            "technical": {"rsi": 62, "macd": "bullish"},
            "fundamental": {"pe": 15, "pb": 3.2, "roe": "22%"},
            "fund_flow": {"main_net_inflow": "+1.2亿"},
            "sentiment": {"news_sentiment": "正面"},
        }
        prompt = build_user_prompt("000333", "美的集团", data)
        assert "技术指标" in prompt or "RSI" in prompt
        assert "财务数据" in prompt or "PE" in prompt
        assert "资金面" in prompt or "主力" in prompt
        assert "情绪面" in prompt or "新闻" in prompt

    def test_empty_data(self):
        prompt = build_user_prompt("000001", "平安银行", {})
        assert "000001" in prompt
        assert "技术指标" not in prompt
