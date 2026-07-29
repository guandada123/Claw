"""test_small_modules.py — 小模块的轻量导入测试。"""


def test_secrets_importable():
    import secrets_template
    assert secrets_template.DEEPSEEK_API_KEY is not None


def test_log_setup_importable():
    import log_setup
    assert hasattr(log_setup, "get_logger")


def test_check_no_double_import_importable():
    import check_no_double_import
    assert callable(check_no_double_import.main)
