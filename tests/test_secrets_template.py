"""test_secrets_template.py — 仅导入覆盖模块级常量。"""


def test_secrets_template_constants_importable():
    """secrets_template 仅含占位密钥常量，确保导入不抛异常。"""
    import secrets_template as st

    assert st.DEEPSEEK_API_KEY is not None
    assert st.CATROUTER_API_KEY is not None
    assert "api.deepseek.com" in st.DEEPSEEK_BASE_URL
