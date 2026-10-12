"""
secrets.py — API 密钥（兼容旧引用）
================================
已无业务代码直接 import 本文件；保留仅为兼容。
真实密钥从 .env 读取（见 claw_secrets.py）。如需模板，请用 secrets_template.py。

如需本地使用：复制 secrets_template.py 为 secrets.py 并填入，或配置 .env。
"""

import os
from pathlib import Path  # 2026-10-12 P1-14：位置无关化后需要


def _claw_root() -> Path:
    """位置无关地定位仓库根（向上找含 .workbuddy 的目录）。

    2026-10-12 P1-14：原写 `Path(__file__).resolve().parent.parent` ——
    该式**只在 scripts/ 下成立**；一旦搬到 .workbuddy/scripts/，parent.parent 会指到
    `.workbuddy/` 而算出 `.workbuddy/.workbuddy/…`（曾由此误建目录、被守卫抓到）。
    改为向上搜索后，两种位置都指向同一仓库根。
    """
    p = Path(__file__).resolve().parent
    for cand in (p, *p.parents):
        if (cand / ".workbuddy").is_dir():
            return cand
    raise RuntimeError("找不到含 .workbuddy 的仓库根（脚本被放在异常位置？）")


_root = str(_claw_root())
_env = {}
try:
    with open(os.path.join(_root, ".env"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            _env[k.strip()] = v.strip().strip('"').strip("'")
except OSError:
    pass


DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY") or _env.get("DEEPSEEK_API_KEY", "")
CATROUTER_API_KEY = os.environ.get("CATROUTER_API_KEY") or _env.get("CATROUTER_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL") or _env.get(
    "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"
)
CATROUTER_BASE_URL = os.environ.get("CATROUTER_BASE_URL") or _env.get(
    "CATROUTER_BASE_URL", "https://api.catrouter.net/v1"
)
