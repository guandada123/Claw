"""test_mass_update_automations.py — mass_update_automations 常量测试。"""

import mass_update_automations as mua


def test_changes_is_dict():
    assert isinstance(mua.CHANGES, dict)


def test_changes_has_expected_keys():
    for aid, change in mua.CHANGES.items():
        assert "name" in change
        assert "from" in change
        assert "to" in change
        assert "reason" in change
