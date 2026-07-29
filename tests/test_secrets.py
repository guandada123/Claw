"""test_secrets.py — secrets 模块导入测试。"""


def test_secrets_importable():
    """secrets.py 是一个 py 文件（非 git 跟踪），但模板 secrets_template 应可导入"""
    import secrets_template
    assert secrets_template.DEEPSEEK_API_KEY is not None
    assert "sk-" in secrets_template.DEEPSEEK_API_KEY


def test_constants():
    import secrets_template as st
    assert "api.deepseek.com" in st.DEEPSEEK_BASE_URL
    assert "catrouter" in st.CATROUTER_BASE_URL
