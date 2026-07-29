"""test_context_compressor.py — context_compressor 纯函数全覆盖测试。

覆盖：compress_conversation_history、_summarize_history、compress_market_data、
      _compress_number、chunk_large_task、estimate_chunk_count、_extract_keywords。
"""

import context_compressor as cc

# ── compress_conversation_history ──


def test_compress_empty_history():
    assert cc.compress_conversation_history([]) == []


def test_compress_short_history_no_change():
    """少于 MAX_HISTORY_ROUNDS*2=4 条时原样返回"""
    history = [
        {"role": "user", "content": "问题1"},
        {"role": "assistant", "content": "回答1"},
    ]
    result = cc.compress_conversation_history(history)
    assert len(result) == 2
    assert result == history


def test_compress_long_history():
    """超过 4 条时压缩旧对话"""
    history = [
        {"role": "user", "content": "旧问题1"},
        {"role": "assistant", "content": "旧回答1 建议买入"},
        {"role": "user", "content": "旧问题2"},
        {"role": "assistant", "content": "旧回答2 结论：持有"},
        {"role": "user", "content": "新问题1"},
        {"role": "assistant", "content": "新回答1"},
        {"role": "user", "content": "新问题2"},
        {"role": "assistant", "content": "新回答2"},
    ]
    result = cc.compress_conversation_history(history)
    # 压缩后：摘要 system + 最近4条
    assert len(result) == 5
    assert result[0]["role"] == "system"
    assert "摘要" in result[0]["content"]


# ── _summarize_history ──


def test_summarize_empty():
    assert cc._summarize_history([]) == ""


def test_summarize_extracts_keywords():
    messages = [
        {"role": "assistant", "content": "建议买入，目标价15.0元"},
        {"role": "user", "content": "好的"},
        {"role": "assistant", "content": "止损设在10.5，结论：短期看多"},
    ]
    result = cc._summarize_history(messages)
    assert "建议买入" in result or "止损设在" in result or "目标价" in result


# ── compress_market_data ──


def test_compress_market_data_quick_overview():
    raw = {"price": 12.3456, "change_pct": 3.256, "volume": 152000000, "pe": 8.5, "market_cap": 5432000000}
    result = cc.compress_market_data(raw, "quick_overview")
    assert "12.35" in result or "12.34" in result  # rounded


def test_compress_market_data_trend():
    raw = {"close": 10.5, "ma5": 10.2, "ma10": 10.0, "ma20": 9.8, "volume": 5000000, "macd": 0.3, "rsi": 55}
    result = cc.compress_market_data(raw, "trend_analysis")
    # 应包含均线相关字段
    assert "ma5" in result.lower() or "10.2" in result


def test_compress_market_data_unknown_focus_defaults():
    raw = {"price": 10.0, "change_pct": 1.5, "volume": 100000, "pe": 12, "market_cap": 50000000}
    result = cc.compress_market_data(raw, "nonexistent_focus")
    # 回退到 quick_overview
    assert len(result) > 0


def test_compress_market_data_truncates_long():
    """超过 MAX_DATA_CHARS 时截断"""
    raw = {f"field_{i}": i * 1000 for i in range(50)}
    result = cc.compress_market_data(raw, "quick_overview")
    assert len(result) <= cc.MAX_DATA_CHARS + 3  # +3 for "..."


# ── _compress_number ──


def test_compress_number_yi():
    assert "亿" in cc._compress_number(5_0000_0000)


def test_compress_number_wan():
    result = cc._compress_number(50000)
    assert "万" in result


def test_compress_number_small():
    result = cc._compress_number(123.456)
    assert "123.46" in result


# ── chunk_large_task / estimate_chunk_count ──


def test_chunk_empty_returns_nested_empty():
    assert cc.chunk_large_task([]) == [[]]


def test_chunk_exact_division():
    result = cc.chunk_large_task(list(range(20)), chunk_size=5)
    assert len(result) == 4
    assert len(result[0]) == 5


def test_chunk_partial_last():
    result = cc.chunk_large_task(list(range(12)), chunk_size=5)
    assert len(result) == 3
    assert len(result[-1]) == 2


def test_estimate_chunk_count():
    assert cc.estimate_chunk_count(55, 10) == 6
    assert cc.estimate_chunk_count(0, 10) == 0
    assert cc.estimate_chunk_count(1, 10) == 1


# ── _extract_keywords ──


def test_extract_keywords_chinese():
    # 中文按连续汉字提取，无空格时整体为一个词
    keywords = cc._extract_keywords("分析股票 投资价值")
    assert "分析股票" in keywords or "投资价值" in keywords


def test_extract_keywords_english():
    keywords = cc._extract_keywords("check the budget_guard module")
    assert "budget_guard" in keywords


def test_extract_keywords_filters_stopwords():
    """停用词（的、了、是、我）应被过滤"""
    keywords = cc._extract_keywords("这是我的股票分析结果了吗")
    assert "我的" not in keywords
    assert "了" not in keywords


# ── 常量 ──


def test_constants_positive():
    assert cc.MAX_HISTORY_ROUNDS > 0
    assert cc.MAX_CHUNK_SIZE > 0
    assert cc.MAX_DATA_CHARS > 0
    assert cc.MAX_CODE_TOKENS > 0


def test_field_map_has_keys():
    assert "trend_analysis" in cc.FIELD_MAP
    assert "quick_overview" in cc.FIELD_MAP
    assert "fundamental" in cc.FIELD_MAP


# ── _extract_relevant_functions ──


def test_extract_relevant_functions_finds_function():
    code = """
def calculate_rsi(prices):
    return 55.0

def other_stuff(x):
    return x + 1
"""
    result = cc._extract_relevant_functions(code, ["rsi"])
    assert len(result) == 1
    assert "calculate_rsi" in result[0]


def test_extract_relevant_functions_no_match():
    code = "def foo(x):\n    return x"
    result = cc._extract_relevant_functions(code, ["rsi"])
    assert result == []


def test_extract_relevant_functions_matches_class():
    code = """
class RSICalculator:
    def compute(self, data):
        pass
"""
    result = cc._extract_relevant_functions(code, ["rsi"])
    assert len(result) >= 1
    assert "RSICalculator" in result[0]


def test_extract_relevant_functions_syntax_error_fallback():
    """语法错误时回退到整文件关键词匹配"""
    code = "def broken( { invalid python syntax here"
    result = cc._extract_relevant_functions(code, ["broken"])
    assert len(result) >= 1


# ── load_relevant_code (mock fs) ──


def test_load_relevant_code_with_mock_project(tmp_path):
    """用临时目录模拟项目结构，验证关键词代码加载"""
    (tmp_path / "utils.py").write_text(
        "def calculate_rsi(prices):\n    return sum(prices)/len(prices)\n",
        encoding="utf-8",
    )
    (tmp_path / "irrelevant.py").write_text(
        "def helper(x):\n    pass\n",
        encoding="utf-8",
    )
    # venv 应被跳过
    (tmp_path / "venv").mkdir()
    (tmp_path / "venv" / "skip_me.py").write_text("rsi_keyword_here = True", encoding="utf-8")

    # 使用全小写关键词以匹配 content.lower() 的查找逻辑
    result = cc.load_relevant_code("calculate rsi", str(tmp_path))
    assert "calculate_rsi" in result
    assert len(result) > 0


def test_load_relevant_code_no_keywords():
    """无有意义关键词时返回提示而非空字符串"""
    result = cc.load_relevant_code("的了吗呢吧啊", "/nonexistent/path")
    assert "未找到" in result
    assert len(result) > 0
