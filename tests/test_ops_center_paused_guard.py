"""看守盲区检查（check_paused_guard_automations）的可证伪测试（2026-10-17）。

背景：`🛡️ 巡检中枢存活看门狗（每6h）` 于 10-15 23:30 被置为 PAUSED，近 10 次运行全部成功、
无失败记录、无文档说明原因。它是唯一能发现「统一巡检中枢自己失联」的告警源 —— 被暂停期间
中枢即使彻底停摆也不会有人知道。这类盲区的特征正是**零噪声**：不会有任何报错提示你去查。

可证伪性要求：绿灯必须能被已知正样本击破。本文件用注入的 PAUSED 看守条目把绿灯打红，
并单独验证「读不到库」必须渲染成 skip（ok=None）而不是绿 —— 否则等于把取不到证当通过。
"""

from __future__ import annotations

import importlib.util
import pathlib

SRC = (
    pathlib.Path(__file__).resolve().parent.parent
    / ".workbuddy"
    / "scripts"
    / "unified_ops_center.py"
)
_spec = importlib.util.spec_from_file_location("uoc_paused_guard", SRC)
uoc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(uoc)


def _row(name: str, aid: str = "automation-1786000625371") -> dict:
    return {"id": aid, "name": name, "rrule": "FREQ=HOURLY;INTERVAL=6"}


def test_green_when_no_paused_guard(monkeypatch):
    """无被暂停的看守类条目 → 绿。"""
    monkeypatch.setattr(uoc, "_query_paused_guard_automations", list)
    r = uoc.check_paused_guard_automations()
    assert r["ok"] is True
    assert r["alerts"] == []


def test_red_when_paused_guard_exists(monkeypatch):
    """正样本击破：存在被暂停的看守条目 → 必须红，且告警里带上名称与 id。"""
    monkeypatch.setattr(
        uoc,
        "_query_paused_guard_automations",
        lambda: [_row("🛡️ 巡检中枢存活看门狗（每6h）")],
    )
    r = uoc.check_paused_guard_automations()
    assert r["ok"] is False, "存在暂停的看守条目时必须报红，否则本检查空跑"
    assert r["alerts"], "报红必须给出可处置的告警文本"
    text = r["alerts"][0]
    assert "看门狗" in text
    assert "automation-1786000625371"[:12] in text


def test_unreadable_db_is_skip_not_green(monkeypatch):
    """读不到库 = 无法判定，必须 ok=None（skip），绝不能渲染成绿。"""
    monkeypatch.setattr(uoc, "_query_paused_guard_automations", lambda: None)
    r = uoc.check_paused_guard_automations()
    assert r["ok"] is None
    assert r["alerts"] == []


def test_alert_truncates_at_five_entries(monkeypatch):
    """条目很多时只列前 5 条并注明剩余数量，避免单条告警刷屏。"""
    rows = [_row(f"🛡️ 看守{i}", f"id-{i}") for i in range(8)]
    monkeypatch.setattr(uoc, "_query_paused_guard_automations", lambda: rows)
    r = uoc.check_paused_guard_automations()
    assert r["ok"] is False
    assert "另有 3 条" in r["alerts"][0]


def test_guard_keywords_cover_real_watchdog_name():
    """关键词表必须能命中真实看门狗名称（防止关键词删改后检测静默失效）。"""
    name = "🛡️ 巡检中枢存活看门狗（每6h）"
    assert any(k in name for k in uoc.GUARD_NAME_KEYWORDS)
    # 反例：普通业务自动化不应被误判为看守类
    assert not any(k in "🐟 鱼盆主生成（Wind+东财自建）" for k in uoc.GUARD_NAME_KEYWORDS)
