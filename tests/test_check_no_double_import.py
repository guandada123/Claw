"""test_check_no_double_import.py — 双导入守卫测试。"""

import check_no_double_import as cd


def test_load_ignored_paths_returns_set():
    result = cd._load_ignored_paths()
    assert isinstance(result, set)


def test_main_no_violations():
    """在项目目录运行时不应发现双导入（已清理干净）"""
    code = cd.main([])
    assert code == 0
