"""unified_ops_center_readback 测试 — 把「曾踩过的坑」逐条钉死（P2-4）。

这个模块是**回读验证器**（只读诊断），它的每段代码都对应一次真实事故（脚本头部列了 4 条）。
覆盖率低不是重点，重点是：这些坑一旦回归**不会报错、只会给出错结论**（静默失效）。
所以用例只钉真事故，不做覆盖率填充。

① `/api/admin/status` 是**扁平结构**（字段在顶层）→ 误当双层会得到 {} 并误判"接口挂了"
② 状态锚在 ~/.workbuddy/cross_project_state.json，且 `last_run` 是 **dict**（时间在 .ts）
③ `.ops_alerted.json` 的 key 里 `通道@服务` 是**合法命名** —— 污染特征是「6 位以上连续数字串」，
   拿 `@` 当污染特征会 100% 误报
④ `interval_min` 不在 monitoring.global.unified_ops_center 顶层，而在 `.self_health.interval_min`
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / ".workbuddy" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import unified_ops_center_readback as rb  # noqa: E402

# ══════════════════════════════════════════════════
# _ts：epoch 与 ISO 双格式（坑 ③ 的判据基础）
# ══════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("value", "expected_year"),
    [
        (1759500000, 2025),  # epoch 秒
        (1759500000.5, 2025),  # epoch 浮点
        ("2026-10-11T12:00:00", 2026),  # ISO
        ("2026-10-11T12:00:00Z", 2026),  # ISO + Z 后缀必须能解析
    ],
)
def test_ts_parses_both_formats(value, expected_year):
    got = rb._ts(value)
    assert got is not None, f"{value!r} 应可解析"
    assert got.year == expected_year


@pytest.mark.parametrize("bad", ["not-a-time", "", None, {"a": 1}])
def test_ts_bad_input_returns_none(bad):
    """无法解析必须返回 None（调用方据此打印 ⚠️），不得抛异常中断整个回读。"""
    assert rb._ts(bad) is None


# ══════════════════════════════════════════════════
# sec1：污染判据（坑 ③）
# ══════════════════════════════════════════════════


def test_sec1_does_not_flag_at_sign_keys(tmp_path, monkeypatch, capsys):
    """`通道@服务` 是**合法命名** —— 拿 `@` 当污染特征会 100% 误报。"""
    f = tmp_path / ".ops_alerted.json"
    f.write_text(json.dumps({"飞书@盘中监控": 1759500000}), encoding="utf-8")
    monkeypatch.setattr(rb, "ALERTED", f)
    rb.sec1_alerted()
    out = capsys.readouterr().out
    assert "污染(6+位数字串)=无" in out, out


def test_sec1_flags_keys_with_long_digit_runs(tmp_path, monkeypatch, capsys):
    """真污染 = key 里出现 6 位以上连续数字串（如误把 epoch 当 key）。"""
    f = tmp_path / ".ops_alerted.json"
    f.write_text(json.dumps({"1759500000": 1759500000}), encoding="utf-8")
    monkeypatch.setattr(rb, "ALERTED", f)
    rb.sec1_alerted()
    assert "污染(6+位数字串)=['1759500000']" in capsys.readouterr().out


def test_sec1_missing_file_is_reported_not_raised(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rb, "ALERTED", tmp_path / "nope.json")
    rb.sec1_alerted()  # 不得抛异常
    assert "文件不存在" in capsys.readouterr().out


# ══════════════════════════════════════════════════
# sec2：状态锚（坑 ② / ④）
# ══════════════════════════════════════════════════


def _write_state(tmp_path, payload):
    f = tmp_path / "cross_project_state.json"
    f.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return f


def test_sec2_reads_ts_from_dict_last_run(tmp_path, monkeypatch, capsys):
    """`last_run` 是 dict（时间在 .ts）—— 只按字符串解析会漏掉新鲜度。"""
    anchor = dt.datetime(2026, 10, 11, 22, 0, 0)
    f = _write_state(
        tmp_path,
        {"monitoring": {"global": {"unified_ops_center": {"last_run": {"ts": anchor.timestamp()}}}}},
    )
    monkeypatch.setattr(rb, "CROSS_STATE", f)
    monkeypatch.setattr(rb, "now_ts", (anchor + dt.timedelta(minutes=5)).timestamp())
    rb.sec2_anchor()
    out = capsys.readouterr().out
    assert "新鲜度=5.00min" in out, out


def test_sec2_interval_min_read_from_self_health(tmp_path, monkeypatch, capsys):
    """坑 ④：`interval_min` 在 .self_health 里，不在锚顶层 → 读错会永远显示 None。"""
    f = _write_state(
        tmp_path,
        {"monitoring": {"global": {"unified_ops_center": {"self_health": {"interval_min": 999}}}}},
    )
    monkeypatch.setattr(rb, "CROSS_STATE", f)
    rb.sec2_anchor()
    out = capsys.readouterr().out
    assert "interval_min=999" in out and "超阈值" in out, out


def test_sec2_small_interval_not_flagged(tmp_path, monkeypatch, capsys):
    f = _write_state(
        tmp_path,
        {"monitoring": {"global": {"unified_ops_center": {"self_health": {"interval_min": 60}}}}},
    )
    monkeypatch.setattr(rb, "CROSS_STATE", f)
    rb.sec2_anchor()
    assert "未触发" in capsys.readouterr().out


# ══════════════════════════════════════════════════
# sec3：微信通道（坑 ①：扁平结构）
# ══════════════════════════════════════════════════


def _fake_curl(monkeypatch, payload_text):
    class _Out:
        stdout = payload_text

    monkeypatch.setattr(rb.subprocess, "run", lambda *a, **k: _Out())


def test_sec3_handles_flat_structure(monkeypatch, capsys):
    """坑 ①：字段在**顶层**（无 data.data 双层）→ 误当双层会得到 {} 并误判"接口挂了"。"""
    _fake_curl(
        monkeypatch,
        json.dumps({"loggedIn": True, "isExpired": False, "account": "测试号", "expireTime": 4102444800000}),
    )
    rb.sec3_wechat()
    out = capsys.readouterr().out
    assert "loggedIn=True" in out and "isExpired=False" in out, out


def test_sec3_handles_double_nested_structure(monkeypatch, capsys):
    """兼容双层（另一个通道返回 data.data）。"""
    _fake_curl(monkeypatch, json.dumps({"data": {"data": {"loggedIn": False, "isExpired": True}}}))
    rb.sec3_wechat()
    out = capsys.readouterr().out
    assert "loggedIn=False" in out and "isExpired=True" in out, out


def test_sec3_curl_failure_is_reported_not_raised(monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("curl 挂了")

    monkeypatch.setattr(rb.subprocess, "run", boom)
    rb.sec3_wechat()  # 不得抛异常（否则整个回读中断）
    assert "取状态失败" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("expire", "should_parse"),
    [
        (4102444800000, True),  # 毫秒 epoch
        (4102444800, True),  # 秒 epoch
        ("4102444800000", True),  # 字符串毫秒
        ("2026-10-11T00:00:00", True),  # ISO
        ("not-a-time", False),  # 无法解析 → 打印 raw
    ],
)
def test_sec3_expire_time_variants(monkeypatch, capsys, expire, should_parse):
    _fake_curl(monkeypatch, json.dumps({"loggedIn": True, "expireTime": expire}))
    rb.sec3_wechat()
    out = capsys.readouterr().out
    if should_parse:
        assert "已过期" in out, out
    else:
        assert "expireTime raw" in out, out


# ══════════════════════════════════════════════════
# sec5：git 漂移（折叠低估 + 分组不可退化）
# ══════════════════════════════════════════════════


def _fake_git(monkeypatch, collapsed, expanded):
    """按是否带 --untracked-files=all 返回不同结果。"""

    def _run(cmd, **k):
        class _Out:
            stdout = expanded if "--untracked-files=all" in cmd else collapsed

        return _Out()

    monkeypatch.setattr(rb.subprocess, "run", _run)


def test_sec5_detects_folded_underestimate(monkeypatch, capsys):
    """run#39 实事故：默认 git status 折叠未跟踪目录（报 1 项，实际 44 个文件）。"""
    _fake_git(monkeypatch, "?? docs/\n", "?? docs/a.md\n?? docs/b.md\n?? docs/c.md\n")
    rb.sec5_git()
    out = capsys.readouterr().out
    # 隐藏数 = 展开项 − 折叠项（折叠报 1、展开 3 → 隐藏 2）
    assert "折叠低估" in out and "2 个被目录名隐藏" in out, out


def test_sec5_groups_same_dir_instead_of_listing_each(monkeypatch, capsys):
    """分组不得退化：同一目录下多项应聚合成 `[N 项] 目录/`，而非逐条刷屏。"""
    rows = "".join(f"?? .workbuddy/memory/automations/a{i}/memory.md\n" for i in range(12))
    _fake_git(monkeypatch, rows, rows)
    rb.sec5_git()
    out = capsys.readouterr().out
    assert "[12 项]" in out, out
    assert "另有" in out or "└─" in out  # 大组需展开一层子结构


def test_sec5_flags_root_config_changes(monkeypatch, capsys):
    """根级配置/基建的改动要显著标注（历史上修复落在盲区、连续 3 轮没被发现）。"""
    _fake_git(monkeypatch, " M .gitignore\n", " M .gitignore\n")
    rb.sec5_git()
    out = capsys.readouterr().out
    assert "根级配置" in out, out
