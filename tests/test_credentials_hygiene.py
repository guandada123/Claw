"""凭证纪律与密钥扫描配置的回归测试（2026-10-11 审计整改配套）。

钉住三条**已被真实事故打回**的教训：

  · P0-1：macro_data.py 曾明文硬编码 `Bearer ak_...` Wind token，且在**公开仓库**
    暴露 73 天 → 源码必须无该形态（防复发）。
  · 降级语义：未配置 `WIND_MCP_TOKEN` 时须**显式降级**（不发起网络请求），
    而不是拿空凭证去撞 401。
  · `.gitleaks.toml` 必须含 `[extend] useDefault = true` —— 实测证据：一个「只有
    [allowlist]、没有规则」的 gitleaks 配置会让扫描**静默空跑**（rules-less → 0 命中；
    补 useDefault → 2 命中），这正是 CI Secret Detection 长期漏检的根因。

全部离线：不发起真实网络请求。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

# 3.11+ 才有 stdlib tomllib；本机 /usr/bin/python3 是 3.9。
# importorskip 让 3.9 下**可见地跳过**（而非采集期 ImportError 中断整个套件），CI 用 3.12 照常全跑。
tomllib = pytest.importorskip("tomllib", reason="需要 Python 3.11+（stdlib tomllib）")

PROJECT = Path(__file__).resolve().parent.parent
SCRIPTS = PROJECT / ".workbuddy" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

MACRO_SRC = SCRIPTS / "macro_data.py"
GITLEAKS_CFG = PROJECT / ".gitleaks.toml"


# ── 1. 源码不得出现明文 token ──────────────────────────────


def test_macro_data_has_no_hardcoded_bearer_token():
    src = MACRO_SRC.read_text(encoding="utf-8")
    assert not re.search(r"Bearer\s+ak_[A-Za-z0-9]{16,}", src), (
        "macro_data.py 出现明文 `Bearer ak_...`（P0-1 形态，必须走 .env/环境变量）"
    )


def test_auth_is_derived_from_env_token():
    """WIND_MCP_AUTH 必须由 WIND_MCP_TOKEN 派生（有则 Bearer 前缀，无则空串）。"""
    import macro_data

    expected = f"Bearer {macro_data.WIND_MCP_TOKEN}" if macro_data.WIND_MCP_TOKEN else ""
    assert expected == macro_data.WIND_MCP_AUTH


def test_missing_token_degrades_without_network(monkeypatch):
    """无 token 时必须直接返回 None（显式降级），不得发起请求。"""
    import macro_data

    calls = {"n": 0}

    def _no_request(*_args, **_kwargs):  # pragma: no cover - 仅用于断言未触发
        calls["n"] += 1
        raise AssertionError("缺 token 时不应发起网络请求")

    monkeypatch.setattr(macro_data, "WIND_MCP_AUTH", "")
    monkeypatch.setattr(macro_data.urllib.request, "urlopen", _no_request)
    assert macro_data._fetch_wind_mcp_macro("中国CPI当月同比 最近12期") is None
    assert calls["n"] == 0


def test_load_env_file_does_not_override_existing(monkeypatch):
    """_load_env_file 只补缺失键：进程已有同名变量时不得覆盖。"""
    import macro_data

    env_path = PROJECT / ".env"
    if not env_path.exists():
        pytest.skip(".env 不存在（CI 环境属正常）")

    key = None
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key = line.partition("=")[0].strip()
            break
    if not key:
        pytest.skip(".env 无可解析键")

    monkeypatch.setenv(key, "SENTINEL_DO_NOT_OVERRIDE")
    macro_data._load_env_file()
    import os

    assert os.environ[key] == "SENTINEL_DO_NOT_OVERRIDE"


# ── 2. gitleaks 配置不得静默空跑（本次事故根因） ──────────────


def test_gitleaks_config_keeps_default_rules_enabled():
    cfg = tomllib.loads(GITLEAKS_CFG.read_text(encoding="utf-8"))
    assert cfg.get("extend", {}).get("useDefault") is True, (
        ".gitleaks.toml 缺 [extend] useDefault = true —— 会让扫描静默空跑（实测 0 命中）"
    )


def test_gitleaks_config_has_wind_bearer_rule():
    cfg = tomllib.loads(GITLEAKS_CFG.read_text(encoding="utf-8"))
    rules = cfg.get("rules", [])
    assert any(r.get("id") == "wind-mcp-bearer-token" for r in rules), (
        "缺自定义规则 wind-mcp-bearer-token（默认规则不覆盖 `Bearer ak_` 形态）"
    )
