"""test_subscription_brief.py — subscription_brief 测试。"""

from unittest.mock import patch

import subscription_brief as sb


def test_parse_iso_valid():
    from datetime import datetime
    result = sb._parse_iso("2026-07-19T10:00:00")
    assert result == datetime(2026, 7, 19, 10, 0, 0)


def test_parse_iso_invalid():
    assert sb._parse_iso("not-a-date") is None
    assert sb._parse_iso("") is None


def test_parse_iso_none():
    assert sb._parse_iso(None) is None


@patch("subscription_brief.fetch_local_sub_count", return_value=None)
@patch("subscription_brief.load_candidates", return_value=[
    {"name": "测试号", "status": "subscribed", "discovered_at": "2026-07-18"},
    {"name": "待审号", "status": "pending", "discovered_at": "2026-07-17"},
])
def test_build_brief_dry(mock_load, mock_fetch):
    result = sb.build_brief(dry=True)
    assert result["subscribed"] == 1
    assert result["total"] == 2
    assert "lines" in result
