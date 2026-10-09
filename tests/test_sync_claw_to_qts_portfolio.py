"""sync_claw_to_qts_portfolio 的契约守卫（重点：尾换行）。

2026-10-09 踩到：脚本 `write_text(json.dumps(...))` 不带尾换行，而 QTS 仓的
pre-commit `end-of-file-fixer` 强制「文件以单个换行结尾」→ 每次同步后 hook 都要
再改一次文件 → QTS 侧 `git commit` 被 hook 中断（hook 改了文件即失败）+ 无尽 diff。
修法：写入时补 `+ "\\n"`。幂等判定走 JSON 内容比较，不受尾换行影响。

全部离线：SRC / DST / BACKUP_DIR 全部 monkeypatch 到 tmp。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import sync_claw_to_qts_portfolio as scq  # noqa: E402

PAYLOAD = {
    "config": {"initial_capital": 30000.0},
    "cash": 44555.18,
    "positions": {"600036": {"name": "招商银行", "shares": 200, "current_price": 41.42}},
    "total_assets": 52839.18,
}


@pytest.fixture()
def paths(monkeypatch, tmp_path):
    src = tmp_path / "portfolio.json"
    src.write_text(json.dumps(PAYLOAD, ensure_ascii=False, indent=2), encoding="utf-8")
    dst = tmp_path / "qts" / "portfolio.json"
    monkeypatch.setattr(scq, "SRC", src)
    monkeypatch.setattr(scq, "DST", dst)
    monkeypatch.setattr(scq, "BACKUP_DIR", tmp_path / "bak")
    return src, dst


def test_written_target_ends_with_exactly_one_newline(paths):
    """核心回归点：产出必须满足 QTS 仓 end-of-file-fixer 的「单个尾换行」。"""
    _src, dst = paths
    assert scq.main() == 0
    raw = dst.read_bytes()
    assert raw.endswith(b"\n"), "必须带尾换行"
    assert not raw.endswith(b"\n\n"), "且只能有一个"
    assert json.loads(raw.decode("utf-8")) == PAYLOAD


def test_second_run_is_idempotent_and_keeps_newline(paths, capsys):
    src, dst = paths
    scq.main()
    first = dst.read_bytes()
    capsys.readouterr()
    assert scq.main() == 0
    assert "目标已是最新" in capsys.readouterr().out
    assert dst.read_bytes() == first, "幂等路径不得改动字节（否则 hook 天天改）"


def test_missing_target_is_not_an_error(paths):
    """首次同步 / 目标被清理时不得崩（原实现 copy2 会抛 FileNotFoundError）。"""
    _src, dst = paths
    assert not dst.exists()
    assert scq.main() == 0
    assert dst.is_file()


def test_bad_source_json_returns_1_and_does_not_touch_target(paths, capsys):
    src, dst = paths
    src.write_text("{ not json", encoding="utf-8")
    assert scq.main() == 1
    assert "[ERROR]" in capsys.readouterr().out
    assert not dst.exists()


def test_rollback_restores_newline_form(paths, monkeypatch, capsys):
    """复验失败 → 回滚。回滚后的文件仍须是「带尾换行」形态，不能把 hook 再惹一遍。"""
    _src, dst = paths
    scq.main()
    before = dst.read_bytes()
    capsys.readouterr()

    real_read = Path.read_text
    calls = {"n": 0}

    def flaky(self, *a, **k):
        if self == dst:
            calls["n"] += 1
            if calls["n"] >= 2:  # 第一次是幂等比较，第二次是写入后的复验
                return '{"tampered": true}'
        return real_read(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", flaky)
    # 让源与目标不同，确保走到写入+复验分支
    scq.SRC.write_text(json.dumps({**PAYLOAD, "cash": 1.0}), encoding="utf-8")
    rc = scq.main()
    monkeypatch.undo()
    assert rc == 1
    assert dst.read_bytes() == before, "回滚应还原写入前内容"
