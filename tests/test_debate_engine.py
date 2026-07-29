"""测试 src/claw/debate/debate_engine.py（无 LLM 依赖部分）"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from claw.debate.debate_engine import _fallback_verdict, _parse_json_response


class TestParseJsonResponse:
    def test_clean_json(self):
        raw = '{"stance":"BUY","confidence":0.8,"reasoning":"理由","risk_flags":["风险1"]}'
        result = _parse_json_response(raw)
        assert result["stance"] == "BUY"
        assert result["confidence"] == 0.8
        assert len(result["risk_flags"]) == 1

    def test_markdown_fenced(self):
        raw = '```json\n{"stance":"HOLD","confidence":0.5}\n```'
        result = _parse_json_response(raw)
        assert result["stance"] == "HOLD"

    def test_embedded_in_text(self):
        raw = '分析完成后，输出：{"stance":"SELL","confidence":0.3}。以上就是我的判断。'
        result = _parse_json_response(raw)
        assert result["stance"] == "SELL"

    def test_empty_string(self):
        assert _parse_json_response("") == {}
        assert _parse_json_response("not json at all") == {}

    def test_partial_json(self):
        raw = '{"stance":"BUY","reasoning":"好的'
        assert _parse_json_response(raw) == {}


class TestFallbackVerdict:
    def test_majority_buy(self):
        stances = [
            {"stance": "BUY"}, {"stance": "BUY"}, {"stance": "BUY"},
            {"stance": "HOLD"}, {"stance": "HOLD"},
            {"stance": "SELL"}, {"stance": "SELL"},
        ]
        verdict = _fallback_verdict(stances)
        assert verdict["consensus"] == "BUY"
        assert verdict["weighted_score"] == 3 / 7

    def test_majority_sell(self):
        stances = [
            {"stance": "SELL"}, {"stance": "SELL"}, {"stance": "SELL"},
            {"stance": "HOLD"}, {"stance": "BUY"}, {"stance": "BUY"},
            {"stance": "HOLD"},
        ]
        verdict = _fallback_verdict(stances)
        assert verdict["consensus"] == "SELL"

    def test_tie_goes_to_hold(self):
        stances = [
            {"stance": "BUY"}, {"stance": "BUY"},
            {"stance": "SELL"}, {"stance": "SELL"},
            {"stance": "HOLD"}, {"stance": "HOLD"}, {"stance": "HOLD"},
        ]
        verdict = _fallback_verdict(stances)
        assert verdict["consensus"] == "HOLD"

    def test_empty_stances(self):
        assert _fallback_verdict([])["consensus"] == "HOLD"

    def test_all_hold(self):
        stances = [{"stance": "HOLD"}] * 7
        verdict = _fallback_verdict(stances)
        assert verdict["consensus"] == "HOLD"
        assert verdict["weighted_score"] == 1.0
