"""静默破坏写法检查（check_silent_destructive_prompts）的可证伪测试（2026-10-18）。

背景：`🧹【系统维护】记忆+上下文维护` 的 2a 步原写作
`find $CLAW/.workbuddy/memory/ -name "*.md" -size 0 -delete 2>/dev/null` —— 递归删除记忆
目录下的文件，却①不留删除清单 ②吞掉全部 stderr（$CLAW 为空时静默失败，输出与"本来就没有
空文件"完全一致）③无预演。已硬化为「先列后删 + 逐条 -print 留痕 + 空变量显式报错」。

可证伪性：绿灯必须能被已知正样本击破。本文件注入旧写法原文验证会报红，并逐一验证
排除项（/tmp 临时文件、说明性文字、已带留痕的写法）不会被误报 —— 只写"不误报"的
测试是空跑，必须两头都钉住。
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
_spec = importlib.util.spec_from_file_location("uoc_silent_dest", SRC)
uoc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(uoc)

OLD_2A = 'Bash: find $CLAW/.workbuddy/memory/ -name "*.md" -size 0 -delete 2>/dev/null'


def test_green_when_no_hits(monkeypatch):
    """无命中 → 绿。"""
    monkeypatch.setattr(uoc, "_scan_silent_destructive", list)
    r = uoc.check_silent_destructive_prompts()
    assert r["ok"] is True
    assert r["alerts"] == []


def test_red_on_known_bad_pattern(monkeypatch):
    """正样本击破：旧 2a 写法必须报红，否则本检查空跑。"""
    monkeypatch.setattr(
        uoc,
        "_scan_silent_destructive",
        lambda: [{"id": "automation-1780769419635", "name": "🧹 记忆维护", "line": OLD_2A}],
    )
    r = uoc.check_silent_destructive_prompts()
    assert r["ok"] is False
    assert "静默" in r["alerts"][0]


def test_unreadable_db_is_skip_not_green(monkeypatch):
    """读不到库 = 无法判定，必须 ok=None（skip），绝不渲染成绿。"""
    monkeypatch.setattr(uoc, "_scan_silent_destructive", lambda: None)
    r = uoc.check_silent_destructive_prompts()
    assert r["ok"] is None
    assert r["alerts"] == []


def test_temp_files_and_lock_files_are_benign():
    """删 /tmp 临时文件与锁文件属良性，不报红（否则全是噪音，检测会被当狼来了）。"""
    samples = [
        ("a", "t", "Bash: rm -f /tmp/qts_daily_brief.lock 2>/dev/null"),
        ("b", "t", "Bash: rm -rf $(mktemp -d) 2>/dev/null"),
    ]
    assert uoc._scan_silent_destructive(samples) == []


def test_explanatory_text_is_not_flagged():
    """说明性文字里引用的旧写法原文不算命中（只检会被执行的 Bash: 行）。"""
    samples = [
        ("c", "t", "⚠️ 为什么改：原写法 `find ... -size 0 -delete 2>/dev/null` 是静默删除"),
        ("d", "t", "     # 禁止改回 -delete 2>/dev/null 的静默写法"),
    ]
    assert uoc._scan_silent_destructive(samples) == []


def test_audited_deletion_is_not_flagged():
    """已带 -print / tee 留痕的删除不算静默破坏。"""
    samples = [
        ("e", "t", 'Bash: find /x -name "*.md" -size 0 -delete -print 2>/dev/null'),
        ("f", "t", 'Bash: find /x -name "*.md" -size 0 -print | tee /tmp/c.txt'),
    ]
    assert uoc._scan_silent_destructive(samples) == []


def test_real_bad_line_still_hit_among_mixed_samples():
    """混合样本：良性三条 + 真实坏写法一条 → 只命中一条（验证过滤没有把真信号也滤掉）。"""
    samples = [
        ("a", "t", "Bash: rm -f /tmp/x.lock 2>/dev/null"),
        ("c", "t", "⚠️ 原写法 ... -delete 2>/dev/null 是坏的"),
        ("e", "t", 'Bash: find /x -name "*.md" -size 0 -delete -print 2>/dev/null'),
        ("bad", "🧹 记忆维护", OLD_2A),
    ]
    hits = uoc._scan_silent_destructive(samples)
    assert len(hits) == 1
    assert hits[0]["id"] == "bad"
