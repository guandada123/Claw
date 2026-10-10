"""audit_locks 的守卫测试（锁文件依赖漏洞门禁）。

门禁的**设计判据**（本文件守的就是这几条）：
  · 只挡「可处置的」：有修复版本 → 挡；无修复版本 → 只提示（上游没修，红了也没用）
  · 同一 CVE 的多个 ID（GHSA-* / PYSEC-*）必须归并成 1 条（实测 requests 那例）
  · 豁免必须带到期日，过期即失效（防「永久豁免」）
  · 判不了（网络/API 失败）→ rc=2，**不静默放过**

全部离线：不联网，query_osv / fetch_vuln 用替身。
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / ".workbuddy" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import audit_locks as al  # noqa: E402

LOCK_OK = """\
[[package]]
name = "requests"
version = "2.34.2"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "urllib3"
version = "2.8.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "claw"
version = "0.1.0"
source = { editable = "." }
"""


def _write_lock(tmp_path: Path, text: str = LOCK_OK) -> Path:
    p = tmp_path / "uv.lock"
    p.write_text(text, encoding="utf-8")
    return p


# ── 读锁 ─────────────────────────────────────────────────────────


def test_read_lock_packages_parses_name_version(tmp_path):
    pkgs = al.read_lock_packages(_write_lock(tmp_path))
    assert pkgs == {"requests": "2.34.2", "urllib3": "2.8.0"}


def test_read_lock_packages_skips_editable_self(tmp_path):
    """本仓库自身的 editable 包不是依赖，不该被拿去查 OSV。"""
    assert "claw" not in al.read_lock_packages(_write_lock(tmp_path))


def test_find_locks_skips_vcs_and_venv(tmp_path):
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    for d in (".git", ".venv", "node_modules"):
        p = tmp_path / d / "uv.lock"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")
    found = [str(p.relative_to(tmp_path)) for p in al.find_locks(tmp_path)]
    assert found == ["uv.lock"]


# ── 归并（同一 CVE 的两个 ID）─────────────────────────────────────


def test_group_vulns_merges_ids_sharing_an_alias():
    """实测形态：GHSA-gc5v-m9x4-r6x2 与 PYSEC-2026-2275 共享 CVE-2026-25645 → 1 组。"""
    details = {
        "GHSA-gc5v-m9x4-r6x2": {"aliases": ["CVE-2026-25645", "PYSEC-2026-2275"]},
        "PYSEC-2026-2275": {"aliases": ["CVE-2026-25645", "GHSA-gc5v-m9x4-r6x2"]},
    }
    groups = al.group_vulns(["GHSA-gc5v-m9x4-r6x2", "PYSEC-2026-2275"], details)
    assert len(groups) == 1, "同一漏洞必须只报一次"
    assert groups[0]["members"] == ["GHSA-gc5v-m9x4-r6x2", "PYSEC-2026-2275"]


def test_group_vulns_keeps_distinct_issues_apart():
    details = {"CVE-A": {"aliases": ["CVE-A"]}, "CVE-B": {"aliases": ["CVE-B"]}}
    assert len(al.group_vulns(["CVE-A", "CVE-B"], details)) == 2


def test_classify_prefers_cve_id_as_display():
    details = {"GHSA-x": {"aliases": ["CVE-2026-1", "GHSA-x"]}}
    f = al.classify(al.group_vulns(["GHSA-x"], details)[0], "pkg", "1.0", details)
    assert f["display_id"] == "CVE-2026-1"


# ── 判据：有修复版本才挡 ──────────────────────────────────────────


def _vuln(fixed: str | None, pkg: str = "requests", aliases: list[str] | None = None) -> dict:
    """构造一条 OSV 漏洞。**必须能让 aliases 透传** —— 归并全靠它
    （真实响应里 GHSA-* 与 PYSEC-* 靠共享的 CVE 号合并；替身不给 aliases
    就会把 1 条漏洞拆成 2 条，测试反而掩盖了归并逻辑）。"""
    events = [{"introduced": "0"}]
    if fixed:
        events.append({"fixed": fixed})
    return {
        "summary": "s",
        "aliases": aliases or [],
        "affected": [{"package": {"name": pkg}, "ranges": [{"events": events}]}],
        "database_specific": {"severity": "HIGH"},
    }


def test_classify_actionable_when_fix_exists():
    details = {"CVE-1": _vuln("2.33.0")}
    f = al.classify({"members": ["CVE-1"], "canonical": "CVE-1"}, "requests", "2.32.5", details)
    assert f["actionable"] is True
    assert f["fix_versions"] == ["2.33.0"]
    assert f["severity"] == "HIGH"


def test_classify_not_actionable_when_no_fix():
    """无修复版本 → 不挡（否则是让无关提交替上游背锅）。"""
    details = {"CVE-2": _vuln(None)}
    f = al.classify({"members": ["CVE-2"], "canonical": "CVE-2"}, "requests", "1.0", details)
    assert f["actionable"] is False
    assert f["fix_versions"] == []


def test_extract_fixes_ignores_other_packages():
    v = _vuln("9.9.9", pkg="other")
    assert al.extract_fixes(v, "requests") == []


# ── 豁免表 ───────────────────────────────────────────────────────


@pytest.fixture()
def finding():
    return {"package": "requests", "version": "2.32.5", "ids": ["CVE-2026-1", "GHSA-x"], "display_id": "CVE-2026-1"}


def test_allowlist_hit_and_unexpired(finding):
    entries = [{"id": "CVE-2026-1", "package": "requests", "reason": "暂无重建窗口", "expires": "2026-12-31"}]
    ok, why = al.is_allowed(finding, entries, dt.date(2026, 10, 10))
    assert ok is True and "2026-12-31" in why


def test_allowlist_expired_must_fail(finding):
    """过期豁免 → 不豁免，并给出「须复审」的理由（防永久豁免）。"""
    entries = [{"id": "CVE-2026-1", "package": "requests", "reason": "x", "expires": "2026-01-01"}]
    ok, why = al.is_allowed(finding, entries, dt.date(2026, 10, 10))
    assert ok is False and "已过期" in why


def test_allowlist_without_expiry_fails(finding):
    ok, why = al.is_allowed(finding, [{"id": "CVE-2026-1", "reason": "x"}], dt.date(2026, 10, 10))
    assert ok is False and "expires" in why


def test_allowlist_package_mismatch_does_not_apply(finding):
    entries = [{"id": "CVE-2026-1", "package": "urllib3", "reason": "x", "expires": "2099-01-01"}]
    ok, _ = al.is_allowed(finding, entries, dt.date(2026, 10, 10))
    assert ok is False


def test_allowlist_missing_file_is_empty(tmp_path):
    assert al.load_allowlist(tmp_path / "nope.json") == []


def test_allowlist_broken_json_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ValueError):
        al.load_allowlist(p)


# ── 端到端（替身，不联网）────────────────────────────────────────


def test_audit_end_to_end_blocks_actionable(tmp_path, monkeypatch):
    _write_lock(tmp_path)
    _aliases = ["CVE-2026-1", "GHSA-x"]
    monkeypatch.setattr(
        al, "query_osv", lambda pkgs, timeout=45: {"requests": list(_aliases)}
    )
    monkeypatch.setattr(
        al, "fetch_vuln", lambda vid, timeout=30: _vuln("2.33.0", aliases=list(_aliases))
    )
    rep = al.audit(tmp_path, tmp_path / "none.json", dt.date(2026, 10, 10))
    assert len(rep["blocking"]) == 1, "同一 CVE 归并后应只有 1 条"
    assert rep["blocking"][0]["display_id"] == "CVE-2026-1"
    assert rep["warnings"] == [] and rep["errors"] == []


def test_audit_warns_but_does_not_block_without_fix(tmp_path, monkeypatch):
    _write_lock(tmp_path)
    monkeypatch.setattr(al, "query_osv", lambda pkgs, timeout=45: {"requests": ["CVE-2026-2"]})
    monkeypatch.setattr(al, "fetch_vuln", lambda vid, timeout=30: _vuln(None))
    rep = al.audit(tmp_path, tmp_path / "none.json", dt.date(2026, 10, 10))
    assert rep["blocking"] == [] and len(rep["warnings"]) == 1


def test_audit_network_failure_is_recorded_not_swallowed(tmp_path, monkeypatch):
    """判不了必须留痕（上层据此 rc=2），不得当成「干净」。"""

    def boom(pkgs, timeout=45):
        raise OSError("network down")

    _write_lock(tmp_path)
    monkeypatch.setattr(al, "query_osv", boom)
    rep = al.audit(tmp_path, tmp_path / "none.json", dt.date(2026, 10, 10))
    assert rep["blocking"] == [] and rep["errors"], "必须记录错误而非静默通过"


def test_audit_reports_missing_locks(tmp_path):
    rep = al.audit(tmp_path, tmp_path / "none.json", dt.date(2026, 10, 10))
    assert rep["errors"] and "未找到" in rep["errors"][0]


def test_main_returns_1_on_blocking_and_0_when_clean(tmp_path, monkeypatch, capsys):
    _write_lock(tmp_path)
    monkeypatch.setattr(sys, "argv", ["audit_locks", "--root", str(tmp_path)])
    monkeypatch.setattr(al, "query_osv", lambda pkgs, timeout=45: {"requests": ["CVE-2026-1"]})
    monkeypatch.setattr(al, "fetch_vuln", lambda vid, timeout=30: _vuln("2.33.0"))
    assert al.main() == 1

    monkeypatch.setattr(al, "query_osv", lambda pkgs, timeout=45: {})
    capsys.readouterr()
    assert al.main() == 0
    assert "无可处置漏洞" in capsys.readouterr().out
