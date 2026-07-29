"""test_summarize_batch.py — summarize_batch 核心函数测试。"""

from unittest.mock import patch

import summarize_batch as sb


def test_summarize_article_empty_content():
    article = {"content": "", "title": "测试标题"}
    result = sb.summarize_article_content(article)
    assert "测试标题" in result


def test_summarize_article_shortcut_text():
    article = {"content": "（仅有标题，无完整正文）", "title": "标题"}
    result = sb.summarize_article_content(article)
    assert "标题" in result


@patch("summarize_batch.summarize_text")
def test_summarize_article_mock_call(mock_summarize):
    mock_summarize.return_value = "这是摘要结果。"
    article = {"content": "这是一段超过三个句子的内容。这是第二句。这是第三句。", "title": "测试"}
    result = sb.summarize_article_content(article)
    assert "摘要结果" in result


@patch("summarize_batch.summarize_text")
def test_summarize_article_truncates_long(mock_summarize):
    mock_summarize.return_value = "a" * 300
    article = {"content": "x" * 2500, "title": "长文"}
    result = sb.summarize_article_content(article)
    assert len(result) <= 200


@patch("summarize_batch.summarize_text")
def test_summarize_article_fallback_on_exception(mock_summarize):
    mock_summarize.side_effect = Exception("timeout")
    article = {"content": "内容内容。这是第一句。这是第二句。", "title": "测试"}
    result = sb.summarize_article_content(article)
    assert len(result) > 0
