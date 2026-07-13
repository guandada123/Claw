"""
summarize_batch.py 单元测试
"""
import sys
from pathlib import Path

# 确保 scripts/ 目录可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
from summarize_batch import summarize_article_content


class TestSummarizeArticleContent:
    """summarize_article_content 函数测试"""

    def test_valid_content_generates_summary(self):
        """正常正文应生成有效摘要"""
        article = {
            "title": "科技板块午后走强",
            "content": "今日科技板块午后大幅走强，半导体和AI概念股领涨。中天科技盘中涨幅超过5%，"
                       "成交量较昨日放大近一倍。有研新材半导体材料订单增长明显。市场分析师指出，"
                       "三季度是科技股传统旺季，叠加国产替代政策推进，板块有望持续活跃。",
        }
        summary = summarize_article_content(article)
        assert len(summary) > 0
        assert len(summary) <= 200

    def test_empty_content_returns_title_fallback(self):
        """无正文时应返回标题简述"""
        article = {"title": "明日重点关注", "content": ""}
        summary = summarize_article_content(article)
        assert "明日重点关注" in summary

    def test_no_content_title_marker(self):
        """正文为占位符文本时应返回标题"""
        article = {"title": "某篇文章标题", "content": "（仅有标题，无完整正文）"}
        summary = summarize_article_content(article)
        assert "某篇文章标题" in summary

    def test_summary_never_exceeds_200_chars(self):
        """长正文摘要不得超过 200 字"""
        article = {
            "title": "长文",
            "content": "。" * 2000,  # 2000 个句号
        }
        summary = summarize_article_content(article)
        # 2000 个句号 → summarize skill 返回完整文本 → 被截断到 200
        assert len(summary) <= 200

    def test_handles_single_word_content(self):
        """极短正文应返回 fallback"""
        article = {"title": "测试", "content": "涨"}
        summary = summarize_article_content(article)
        assert len(summary) > 0

    def test_no_title_field(self):
        """缺少 title 字段不应崩溃"""
        article = {"content": "这是一篇有内容但没标题的文章。" * 10}
        summary = summarize_article_content(article)
        assert len(summary) > 0
