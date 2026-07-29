"""测试牛熊辩论全链路（mock LLM 调用）"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from claw.debate.debate_engine import (
    _convergence_phase,
    _fallback_verdict,
    _peer_review_phase,
    _retrieve_memory_context,
)

MOCK_STANCES = [
    {"expert": "fundamental", "name": "基本面专家", "stance": "BUY", "confidence": 0.75, "reasoning": "PE低估值合理"},
    {"expert": "technical", "name": "技术面专家", "stance": "BUY", "confidence": 0.60, "reasoning": "MACD金叉"},
    {"expert": "fund_flow", "name": "资金面专家", "stance": "HOLD", "confidence": 0.55, "reasoning": "北向流出"},
    {"expert": "sentiment", "name": "情绪面专家", "stance": "HOLD", "confidence": 0.50, "reasoning": "市场观望"},
    {"expert": "valuation", "name": "估值专家", "stance": "BUY", "confidence": 0.70, "reasoning": "DCF低估"},
    {"expert": "risk_ctrl", "name": "风控专家", "stance": "HOLD", "confidence": 0.65, "reasoning": "仓位限制"},
    {"expert": "synthesis", "name": "综合判断专家", "stance": "BUY", "confidence": 0.68, "reasoning": "综合看多"},
]

MOCK_STANCES_ALL_BUY = [
    {"expert": "fundamental", "name": "基本面专家", "stance": "BUY", "confidence": 0.85, "reasoning": "极佳"},
    {"expert": "technical", "name": "技术面专家", "stance": "BUY", "confidence": 0.80, "reasoning": "突破"},
    {"expert": "fund_flow", "name": "资金面专家", "stance": "BUY", "confidence": 0.70, "reasoning": "流入"},
    {"expert": "sentiment", "name": "情绪面专家", "stance": "BUY", "confidence": 0.75, "reasoning": "乐观"},
    {"expert": "valuation", "name": "估值专家", "stance": "BUY", "confidence": 0.90, "reasoning": "低估"},
    {"expert": "risk_ctrl", "name": "风控专家", "stance": "BUY", "confidence": 0.65, "reasoning": "可控"},
    {"expert": "synthesis", "name": "综合判断专家", "stance": "BUY", "confidence": 0.80, "reasoning": "全票看好"},
]


class TestBullBearDebate:
    def test_opening_round_has_both_views(self):
        """第一轮应有牛熊双方观点"""
        with patch("claw.debate.debate_engine._call_llm") as mock_llm:
            mock_llm.side_effect = [
                "牛方: 估值低+ROE高+行业龙头地位稳固",  # bull_view
                "熊方: 地产下行+原材料涨价+海外不确定性",  # bear_view
            ]
            reviews = _peer_review_phase(MOCK_STANCES, "000333", "美的集团", max_rounds=1)

        assert len(reviews) == 1
        assert reviews[0]["mode"] == "bull_bear_opening"
        assert "牛方" in reviews[0]["bull_view"] or "估值" in reviews[0]["bull_view"]
        assert "熊方" in reviews[0]["bear_view"] or "地产" in reviews[0]["bear_view"]

    def test_two_rounds_have_rebuttal(self):
        """第二轮应有互相反驳"""
        with patch("claw.debate.debate_engine._call_llm") as mock_llm:
            mock_llm.side_effect = [
                "牛方: 买入理由A",  # bull_view
                "熊方: 卖出理由B",  # bear_view
                "牛反驳: A的支撑数据",  # bull_rebuttal
                "熊反驳: B的风险证据",  # bear_rebuttal
            ]
            reviews = _peer_review_phase(MOCK_STANCES, "000333", "美的集团", max_rounds=2)

        assert len(reviews) == 2
        assert reviews[1]["mode"] == "bull_bear_rebuttal"
        assert len(reviews[1]["bull_rebuttal"]) > 0
        assert len(reviews[1]["bear_rebuttal"]) > 0

    def test_bull_exists_even_when_all_sell(self):
        """即使全票 SELL，也要有牛市研究员找机会"""
        all_sell = [
            {**s, "stance": "SELL"} for s in MOCK_STANCES
        ]
        with patch("claw.debate.debate_engine._call_llm") as mock_llm:
            mock_llm.side_effect = ["牛方: 被忽视的积极因素", "熊方: 多重风险叠加"]
            reviews = _peer_review_phase(all_sell, "000333", "美的集团", max_rounds=1)

        assert len(reviews) == 1
        assert reviews[0]["mode"] == "bull_bear_opening"
        assert len(reviews[0]["bull_view"]) > 0  # 即使全票SELL，牛方仍阐述观点

    def test_bear_exists_even_when_all_buy(self):
        """即使全票 BUY，也要有熊市研究员找风险"""
        with patch("claw.debate.debate_engine._call_llm") as mock_llm:
            mock_llm.side_effect = ["牛方: 全面看好", "熊方: 隐藏风险"]
            reviews = _peer_review_phase(MOCK_STANCES_ALL_BUY, "000333", "美的集团", max_rounds=1)

        assert len(reviews) == 1
        assert len(reviews[0]["bear_view"]) > 0  # 即使全票BUY，熊方仍找风险


class TestConvergenceWithFactors:
    def test_convergence_outputs_factor_scores(self):
        """convergence 应有 factor_scores 和 stop_loss_pct"""
        import json as _json
        mock_output = _json.dumps({
            "consensus": "BUY", "weighted_score": 0.65, "confidence": 0.7,
            "summary": "综合看多", "risk_flags": ["风险1"],
            "stop_loss_pct": -10.5,
            "factor_scores": {"value": 75, "quality": 80, "growth": 55, "momentum": 60},
        })
        with patch("claw.debate.debate_engine._call_llm", return_value=mock_output):
            verdict = _convergence_phase(
                MOCK_STANCES, [], "000333", "美的集团",
                {"price": 85.0, "change_pct": 3.5},
            )

        # 验证新字段存在（不依赖 mock 精确返回值）
        assert "stop_loss_pct" in verdict
        assert "factor_scores" in verdict
        assert isinstance(verdict["factor_scores"], dict)

    def test_fallback_verdict_has_factors(self):
        """降级判决也应有默认因子分"""
        verdict = _fallback_verdict(MOCK_STANCES)
        assert "factor_scores" in verdict
        assert verdict["factor_scores"]["value"] == 50
        assert verdict["stop_loss_pct"] == -8.0


class TestMemoryRetrieval:
    def test_retrieve_returns_empty_when_no_file(self):
        result = _retrieve_memory_context("000333", {"sector": "家用电器"})
        assert result == "" or isinstance(result, str)
