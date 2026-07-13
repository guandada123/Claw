"""Wind 数据源核心路径测试

覆盖: WindKlineSource / WindRealtimeSource / WindFundamentalsSource / WindAnalytics
"""

import os
import sys

# 确保 claw 包可导入
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from unittest.mock import patch

from claw.feeds.data_sources import (
    WindFundamentalsSource,
    WindKlineSource,
    WindRealtimeSource,
)

from claw.feeds.wind_utils import (
    plain_code_to_windcode,
)

# ── 工具函数测试 ──


def test_plain_code_to_windcode_shanghai():
    assert plain_code_to_windcode("600519") == "600519.SH"
    assert plain_code_to_windcode("600522") == "600522.SH"


def test_plain_code_to_windcode_shenzhen():
    assert plain_code_to_windcode("000001") == "000001.SZ"
    assert plain_code_to_windcode("002185") == "002185.SZ"
    assert plain_code_to_windcode("300750") == "300750.SZ"


def test_plain_code_to_windcode_beijing():
    assert plain_code_to_windcode("837242") == "837242.BJ"
    assert plain_code_to_windcode("430090") == "430090.BJ"


# ── WindKlineSource 测试 ──


def test_wind_kline_success():
    """正常 K 线数据解析"""
    mock_data = {
        "columns": ["TIME", "OPEN", "MATCH", "HIGH", "LOW", "TURNOVER", "VOLUME", "CHANGEHANDRATE", "AVPRICE", "_DATE"],
        "rows": [
            ["2026-07-06T00:00:00.000+08:00", "1186.00", "1206.91", "1215.00", "1180.00", "4913750668", "4097001", "0.3277", "1199.35", "20260706"],
            ["2026-07-07T00:00:00.000+08:00", "1200.00", "1188.80", "1202.00", "1188.11", "3264967794", "2736500", "0.2189", "1193.12", "20260707"],
            ["2026-07-08T00:00:00.000+08:00", "1188.77", "1199.30", "1200.98", "1177.00", "3071933498", "2577602", "0.2062", "1191.78", "20260708"],
        ],
    }

    with patch("claw.feeds.data_sources.call_wind_cli", return_value=mock_data):
        source = WindKlineSource()
        df = source.fetch_kline("600519", days=3)

    assert not df.empty
    assert len(df) == 3
    assert df["收盘"].iloc[-1] == 1199.3
    assert "涨跌幅" in df.columns


def test_wind_kline_empty():
    """空数据返回空 DataFrame"""
    with patch("claw.feeds.data_sources.call_wind_cli", return_value={"columns": [], "rows": []}):
        source = WindKlineSource()
        df = source.fetch_kline("600519", days=120)
    assert df.empty


def test_wind_kline_cli_failure():
    """CLI 不可用时返回空"""
    with patch("claw.feeds.data_sources.call_wind_cli", return_value=None):
        source = WindKlineSource()
        df = source.fetch_kline("600519", days=120)
    assert df.empty


def test_wind_kline_is_available():
    """is_available 检查"""
    with patch("claw.feeds.data_sources.wind_available", return_value=True):
        source = WindKlineSource()
        assert source.is_available()

    with patch("claw.feeds.data_sources.wind_available", return_value=False):
        source = WindKlineSource()
        assert not source.is_available()


# ── WindRealtimeSource 测试 ──


def test_wind_realtime_success():
    """正常实时行情解析"""
    mock_data = {
        "columns": ["最新成交价", "涨跌幅", "今日开盘价", "今日最高价", "今日最低价", "成交量", "成交额"],
        "rows": [["1204.98", "1.93", "1182.20", "1204.98", "1170.28", "5221255", "6223343642"]],
    }

    with patch("claw.feeds.data_sources.call_wind_cli", return_value=mock_data):
        source = WindRealtimeSource()
        result = source.fetch_realtime(["600519"])

    assert "600519" in result
    assert result["600519"]["最新价"] == 1204.98
    assert result["600519"]["涨跌幅"] == 1.93
    assert result["600519"]["_source"] == "wind"


def test_wind_realtime_batch():
    """批量查询多只股票"""
    mock_data = {
        "columns": ["最新成交价", "涨跌幅"],
        "rows": [["10.45", "-0.38"]],
    }

    with patch("claw.feeds.data_sources.call_wind_cli", return_value=mock_data):
        source = WindRealtimeSource()
        result = source.fetch_realtime(["600522", "000001"])

    # 两只都返回了相同数据（mock 行为）
    assert len(result) == 2


def test_wind_realtime_parse_error():
    """解析异常时静默跳过"""
    with patch("claw.feeds.data_sources.call_wind_cli", return_value={"columns": [], "rows": []}):
        source = WindRealtimeSource()
        result = source.fetch_realtime(["600519"])
    assert result == {}


# ── WindFundamentalsSource 测试 ──


def test_wind_fundamentals_success():
    """正常基本面解析"""
    mock_data = {
        "columns": ["最新每股收益EPS_基本", "最新净资产收益率ROE", "最新市盈率PE_TTM", "最新市净率PB_LF"],
        "rows": [["21.76", "10.5687", "20.1693", "8.52"]],
    }

    with patch("claw.feeds.data_sources.call_wind_cli", return_value=mock_data):
        source = WindFundamentalsSource()
        result = source.fetch_fundamentals("600519")

    assert result["每股收益"] == 21.76
    assert result["ROE"] == 10.57
    assert result["市盈率"] == 20.17
    assert result["市净率"] == 8.52


def test_wind_fundamentals_empty():
    """无数据时返回全 None"""
    with patch("claw.feeds.data_sources.call_wind_cli", return_value=None):
        source = WindFundamentalsSource()
        result = source.fetch_fundamentals("600519")

    assert result["市盈率"] is None
    assert result["ROE"] is None


# ── WindAnalytics 测试 ──


def test_wind_analytics_available():
    """可用性检查"""
    from claw.feeds.wind_analytics import WindAnalytics

    with patch("claw.feeds.wind_analytics.wind_available", return_value=True):
        wa = WindAnalytics()
        assert wa.available

    with patch("claw.feeds.wind_analytics.wind_available", return_value=False):
        wa = WindAnalytics()
        assert not wa.available


def test_wind_analytics_news_format():
    """新闻 items 格式解析"""
    from claw.feeds.wind_analytics import WindAnalytics

    mock_data = {
        "columns": [],
        "rows": [
            {"title": "测试新闻标题", "content": "测试内容", "date": "2026-07-09"},
        ],
    }

    with patch("claw.feeds.wind_analytics.call_wind_cli_as_rows", return_value=mock_data["rows"]):
        with patch("claw.feeds.wind_analytics.wind_available", return_value=True):
            wa = WindAnalytics()
            news = wa.get_news("测试", top_k=1)

    assert news is not None
    assert len(news) == 1
    assert news[0]["title"] == "测试新闻标题"


def test_wind_analytics_macro_edb():
    """EDB 宏数据展平"""
    from claw.feeds.wind_analytics import WindAnalytics

    mock_data = {
        "columns": [],
        "rows": [
            {"指标": "中国:CPI:当月同比", "单位": "%", "日期": "20260731", "值": 0.0},
        ],
    }

    with patch("claw.feeds.wind_analytics.call_wind_cli_as_rows", return_value=mock_data["rows"]):
        with patch("claw.feeds.wind_analytics.wind_available", return_value=True):
            wa = WindAnalytics()
            macro = wa.get_macro_data("中国CPI同比", observation="1")

    assert macro is not None
    assert len(macro) == 1
    assert macro[0]["指标"] == "中国:CPI:当月同比"
    assert macro[0]["日期"] == "20260731"
