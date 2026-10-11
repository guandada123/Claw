"""`scan_git_history_secrets.compare_env` 的回归测试（2026-10-11 建立）。

## 为什么单独立一个文件
`tests/test_secret_scan_selfcheck.py` 管的是「检测器的模式串别把朴素扫描器打红」，
这里是另一件事：**判定「历史里泄漏的值，是不是现在还在用」**。

## 为什么需要它（实踩一次相反结论）
我先用手写的 `sha1(整值)[:10]` 去比扫描器的 `sha256(匹配片段)[:10]`，
得出「🟢 不同、已被换过」——**与实际相反**。真相是：公开仓历史里那把
**就是当前 `.env` 在用的凭据**。所以这个比对本该由工具做，而不是每次手算：
手算极易**算法与粒度双错**。
"""

from __future__ import annotations

import hashlib
import importlib.util
import pathlib
import sys

import pytest

SRC = (
    pathlib.Path(__file__).resolve().parent.parent
    / ".workbuddy"
    / "scripts"
    / "scan_git_history_secrets.py"
)
_spec = importlib.util.spec_from_file_location("sec_hist", SRC)
sec = importlib.util.module_from_spec(_spec)
sys.modules["sec_hist"] = sec
_spec.loader.exec_module(sec)

LIVE = "ak_ABCDEFGHIJKLMNOPQRSTUVWX"  # 命中 wind_token 模式（ak_ + 24 位）


def _h(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()[:10]


def _patch(monkeypatch, hashes: list[str], rotated: set[str] | None = None):
    monkeypatch.setattr(
        sec,
        "history_hits",
        lambda rev, allow, r: [
            {"pattern": "wind_token", "path": "macro_data.py", "blob": "b", "value_sha256_10": h}
            for h in hashes
        ],
    )
    monkeypatch.setattr(sec, "load_value_allowlist", lambda p: set(rotated or set()))


def test_compare_env_flags_live_value_matching_history(tmp_path, monkeypatch):
    """在用值 = 历史泄漏值 → rc=1（这正是本仓库 PA-013 的实况）。"""
    env = tmp_path / ".env"
    env.write_text(f"WIND_MCP_TOKEN={LIVE}\n", encoding="utf-8")
    _patch(monkeypatch, [_h(LIVE)])
    assert sec.compare_env(env, "--all", [], tmp_path / "al.json") == 1


def test_compare_env_clean_when_values_differ(tmp_path, monkeypatch):
    """反向对照：在用值与泄漏值不同 → rc=0（不许恒报红）。"""
    env = tmp_path / ".env"
    env.write_text(f"WIND_MCP_TOKEN={LIVE}\n", encoding="utf-8")
    _patch(monkeypatch, [_h("ak_ZZZZZZZZZZZZZZZZZZZZZZZZ")])
    assert sec.compare_env(env, "--all", [], tmp_path / "al.json") == 0


def test_compare_env_does_not_skip_allowlisted_matches(tmp_path, monkeypatch):
    """若"在用值"命中一条「已轮换」豁免 → 那是**豁免写错了**，必须报而不是静默跳过。"""
    env = tmp_path / ".env"
    env.write_text(f"WIND_MCP_TOKEN={LIVE}\n", encoding="utf-8")
    _patch(monkeypatch, [_h(LIVE)], rotated={_h(LIVE)})
    assert sec.compare_env(env, "--all", [], tmp_path / "al.json") == 1


def test_compare_env_missing_file_is_rc2(tmp_path, monkeypatch):
    """读不到 = 判不了 → rc=2（**不许当成"没问题"**）。"""
    _patch(monkeypatch, [])
    assert sec.compare_env(tmp_path / "nope.env", "--all", [], tmp_path / "al.json") == 2


@pytest.mark.parametrize("frag", ["ak_" + "A" * 16, "ak_" + "a1B2" * 6])
def test_pattern_matches_expected_token_shapes(frag):
    """判据边界：`ak_` + ≥16 位字母数字才算候选（避免把 `ak_your_key_here` 这类占位符算进来）。"""
    names = [n for n, rx in sec.PATTERNS if rx.search(frag)]
    assert "wind_token" in names
