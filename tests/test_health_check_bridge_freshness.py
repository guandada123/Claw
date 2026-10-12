"""`health_check.check_bridge` 的**新鲜度**判据（2026-10-12 新增）。

## 为什么
Marvis Bridge 的状态产物 `status/bridge.json` **自带 `updated_at` / `last_health_check`**，
而原实现只取 `mode` 一个字段、把时间戳丢掉 → 实测读到的是 **2026-06-29**（105 天前）的状态，
同一文件里却还写着 `sla_status: healthy`。即：**「很久以前写的 healthy」被当现状报出**。
（同类：产物自证新鲜度，消费方却把它扔了。）

故新增 `age_days` / `stale`，**只报不拦**（该函数不进 rc）。本文件把这条判据钉住，
并且**两侧都验**（旧的判 stale=True、新的判 stale=False），防止它退化成恒真或恒假的空检查。

## 为什么不测真实路径
真实产物是别人的活数据；用 `bridge_file` 注入 tmp_path，判据才可复现。
"""

from __future__ import annotations

import importlib.util
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HC = REPO / ".workbuddy" / "scripts" / "health_check.py"


@pytest.fixture(scope="module")
def hc():
    spec = importlib.util.spec_from_file_location("health_check_under_test", HC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write(tmp_path: Path, payload: dict, mtime_days_ago: float | None = None) -> Path:
    p = tmp_path / "bridge.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    if mtime_days_ago is not None:
        t = time.time() - mtime_days_ago * 86400
        os.utime(p, (t, t))
    return p


def test_stale_product_is_flagged(hc, tmp_path):
    """产物是 100 天前写的 healthy → 必须报 stale=True（而不是只回一个 mode）。"""
    old = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    p = _write(tmp_path, {"mode": "direct", "updated_at": old, "sla_status": "healthy"})
    r = hc.check_bridge(p)
    assert r["status"] == "direct"  # 原有字段不变
    assert r["declared_updated_at"] == old  # 时间戳不再被丢掉
    assert r["age_days"] == pytest.approx(100, abs=1)
    assert r["stale"] is True


def test_fresh_product_is_not_flagged(hc, tmp_path):
    """刚更新的产物 → stale=False（反例侧：防判据退化成恒真）。"""
    now = datetime.now(timezone.utc).isoformat()
    r = hc.check_bridge(_write(tmp_path, {"mode": "direct", "updated_at": now}))
    assert r["age_days"] < 1
    assert r["stale"] is False


def test_zulu_suffix_is_parsed_not_silently_dropped(hc, tmp_path):
    """`...Z` 结尾：3.7~3.10 的 fromisoformat 不认 → 必须归一化，不能静默退回 mtime。"""
    old = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    # 文件 mtime 故意设成"刚刚" → 若解析失败退回 mtime，就会得到 age≈0 而掩盖问题
    r = hc.check_bridge(_write(tmp_path, {"mode": "direct", "updated_at": old}, mtime_days_ago=0))
    assert r["declared_updated_at"] == old
    assert r["age_days"] == pytest.approx(30, abs=1), "Z 结尾被静默丢弃 → 退回了 mtime"
    assert r["stale"] is True


def test_missing_timestamp_falls_back_to_mtime(hc, tmp_path):
    """产物没写时间戳 → 退回文件 mtime（宁可粗判，也不丢新鲜度）。"""
    r = hc.check_bridge(_write(tmp_path, {"mode": "direct"}, mtime_days_ago=45))
    assert r["declared_updated_at"] is None
    assert r["age_days"] == pytest.approx(45, abs=1)
    assert r["stale"] is True


def test_missing_file_is_unknown_not_stale(hc, tmp_path):
    """产物不存在 → age/stale 均为空/假（不能凭"没有文件"就报 stale，那会变成噪音）。"""
    r = hc.check_bridge(tmp_path / "nope.json")
    assert r["status"] == "unknown"
    assert r["age_days"] is None
    assert r["stale"] is False
