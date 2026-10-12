"""
secrets_template.py — API 密钥模板（可提交到 GitHub）
======================================================
用法：
    1. 复制为 secrets.py
    2. 填入真实密钥
    3. secrets.py 已被 .gitignore 忽略，不会上传

若未创建 secrets.py，router.py 会尝试从环境变量读取密钥。
"""

DEEPSEEK_API_KEY = "sk-your-deepseek-api-key-here"
CATROUTER_API_KEY = "sk-your-catrouter-api-key-here"
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
CATROUTER_BASE_URL = "https://api.catrouter.net/v1"
