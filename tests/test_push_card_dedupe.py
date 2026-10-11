"""push_card 去重语义的守卫测试（2026-10-11 审计修复配套）。

事故形态（修复前）：
    去重标记在**发送前**落盘 → 首次发送失败（网络/限流重试耗尽）后，
    6 小时内的重试会被判定为「已发送」并返回 rc=0 → **告警被静默吞掉**。

本测试钉住三条：
  · 发送**失败** ⇒ 不得留下去重标记，且 rc≠0（调用方能感知失败）
  · 发送**成功** ⇒ 才写去重标记，rc=0
  · 已有标记 ⇒ 短路返回 0，且**不重复调用发送**（去重本身仍有效）

全部离线：send_card 被替身，不发起任何网络请求；去重键带随机后缀避免互相污染。
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / ".workbuddy" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import push_card  # noqa: E402


def _dedupe_path(key: str) -> Path:
    # 必须与被测实现（push_card.py）的路径规则一致，故同样落在 /tmp
    return Path(f"/tmp/feishu_card_dedupe_{key.replace(' ', '_')[:40]}")  # noqa: S108


@pytest.fixture
def key():
    k = f"单测-去重-{uuid.uuid4().hex[:12]}"
    yield k
    _dedupe_path(k).unlink(missing_ok=True)


def _run(monkeypatch, key: str, send_ok: bool) -> tuple[int, list]:
    calls = []

    def _fake_send(**kwargs):
        calls.append(kwargs)
        return send_ok

    monkeypatch.setattr(push_card, "send_card", _fake_send)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "push_card.py",
            "--title",
            "单测标题",
            "--section",
            "单测小节",
            "单测正文",
            "--dedupe-key",
            key,
        ],
    )
    return push_card.main(), calls


def test_failed_send_leaves_no_dedupe_marker(monkeypatch, key):
    """发送失败 → 不写标记 + rc=1（保证后续重试不会被误判为已发）。"""
    rc, calls = _run(monkeypatch, key, send_ok=False)
    assert rc == 1
    assert len(calls) == 1
    assert not _dedupe_path(key).exists(), "失败也留下了去重标记 → 会静默吞掉后续告警"


def test_successful_send_writes_marker(monkeypatch, key):
    rc, _ = _run(monkeypatch, key, send_ok=True)
    assert rc == 0
    assert _dedupe_path(key).exists(), "成功后应落去重标记"


def test_existing_marker_short_circuits(monkeypatch, key):
    """已有标记 → rc=0 且不调用发送（去重仍生效，避免重复打扰）。"""
    _dedupe_path(key).write_text("sent", encoding="utf-8")
    rc, calls = _run(monkeypatch, key, send_ok=True)
    assert rc == 0
    assert calls == [], "命中去重时不应再调用发送"
