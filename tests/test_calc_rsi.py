"""test_calc_rsi.py — calc_rsi 测试。"""

import calc_rsi as cr


def test_prefix_sh():
    assert cr._prefix("600000") == "sh600000"


def test_prefix_sz():
    assert cr._prefix("000001") == "sz000001"


def test_prefix_already():
    assert cr._prefix("sh600000") == "sh600000"
    assert cr._prefix("SZ000001") == "sz000001"


def test_prefix_strip():
    assert cr._prefix(" 600000 ") == "sh600000"


def test_rsi_wilder_known():
    prices = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84,
              46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41,
              46.22, 43.50, 46.78]
    result = cr.rsi_wilder(prices)
    assert result is not None
    assert isinstance(result, float)
    assert 0 < result < 100


def test_rsi_wilder_short_data():
    assert cr.rsi_wilder([10.0, 11.0]) is None


def test_rsi_wilder_empty():
    assert cr.rsi_wilder([]) is None
