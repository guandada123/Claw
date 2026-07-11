"""
earnings_calendar.py 单元测试
"""
import sys
from pathlib import Path

import pytest

# 确保 scripts/ 目录可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
from earnings_calendar import parse_markdown_table


class TestParseMarkdownTable:
    """parse_markdown_table 函数测试"""

    def test_valid_table(self):
        """标准 Markdown 表格解析"""
        md = """| code | name | reportEndDate | disclosureDate | disclosureDesc |
| --- | --- | --- | --- | --- |
| sh600522 | 中天科技 | 2026-06-30 | 2026-08-28 | 公司预计于2026-08-28披露2026中期报告 |"""
        rows = parse_markdown_table(md)
        assert len(rows) == 1
        assert rows[0]["code"] == "sh600522"
        assert rows[0]["name"] == "中天科技"
        assert rows[0]["disclosureDate"] == "2026-08-28"

    def test_empty_input(self):
        """空字符串返回空列表"""
        assert parse_markdown_table("") == []
        assert parse_markdown_table("   \n") == []

    def test_table_with_header_only(self):
        """仅表头无数据行"""
        md = """| code | name |
| --- | --- |"""
        rows = parse_markdown_table(md)
        assert rows == []

    def test_multiple_rows(self):
        """多行数据解析"""
        md = """| code | name | date |
| --- | --- | --- |
| sh600522 | 中天科技 | 08-28 |
| sh600206 | 有研新材 | 08-05 |
| sz000021 | 深科技 | 08-26 |"""
        rows = parse_markdown_table(md)
        assert len(rows) == 3
        assert rows[1]["name"] == "有研新材"

    def test_exdiv_table(self):
        """除权除息表格解析"""
        md = """| code | name | exDivDate | dividendPerShare | dividendPlan |
| --- | --- | --- | --- | --- |
| sh600522 | 中天科技 | 2026-07-15 | 2.60 | 10派2.600元 |"""
        rows = parse_markdown_table(md)
        assert len(rows) == 1
        assert rows[0]["dividendPlan"] == "10派2.600元"
