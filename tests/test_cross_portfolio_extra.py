"""test_cross_portfolio_extra.py — cross_portfolio_monitor 函数测试。"""


import cross_portfolio_monitor as cpm


def test_get_industry_known_code():
    assert cpm.get_industry("002049") == "🏭 科技/半导体"


def test_get_industry_unknown_code():
    assert cpm.get_industry("999999") == "📦 其他"


def test_load_json_nonexistent(tmp_path):
    p = tmp_path / "nope.json"
    result = cpm.load_json(p)
    assert result == {}


def test_load_json_valid(tmp_path):
    p = tmp_path / "valid.json"
    p.write_text('{"key": "value"}', encoding="utf-8")
    result = cpm.load_json(p)
    assert result["key"] == "value"


def test_stock_pool_defined():
    assert hasattr(cpm, "STOCK_POOL") or "CHAIN_MAP" in dir(cpm)
    assert len(cpm.INDUSTRY_MAP) > 10


def test_get_industry_by_name_semiconductor():
    assert "科技" in cpm.get_industry("000000", name="中芯半导体")


def test_get_industry_by_name_medical():
    assert "医药" in cpm.get_industry("000000", name="恒瑞医疗")


def test_parse_sim_positions_dict():
    result = cpm.parse_sim_positions({"positions": {"600000": {"shares": 100}}})
    assert "600000" in result


def test_parse_sim_positions_non_dict():
    assert cpm.parse_sim_positions({"positions": []}) == {}


def test_parse_user_holdings():
    result = cpm.parse_user_holdings({"holdings": [{"code": "600000"}]})
    assert len(result) == 1
