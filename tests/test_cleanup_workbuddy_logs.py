"""日志清理脚本 `cleanup_workbuddy_logs.py` 的保留口径回归测试（2026-10-17）。

背景：清理规则原先只写在自动化 prompt 的自然语言里，由模型临场解释"早于今天减 7 天"，
存在 `< today-7` 与 `<= today-7` 两种合理解释（删除量可差 338M），且删除前无法预演。
删除不可逆，故把口径写死进脚本，并用本文件把它钉住。

可证伪性要求：每个绿灯都必须能被已知正样本击破 —— 因此每个"不该删"的断言都配一个
"必须删"的对照项（today-8 目录 / drop31 文件），只写保留断言的测试等于空跑。
"""

from __future__ import annotations

import datetime
import importlib.util
import pathlib
import subprocess
import sys

SRC = (
    pathlib.Path(__file__).resolve().parent.parent
    / ".workbuddy"
    / "scripts"
    / "cleanup_workbuddy_logs.py"
)
_spec = importlib.util.spec_from_file_location("cleanup_logs", SRC)
cleanup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cleanup)

TODAY = datetime.date(2026, 10, 18)
KEEP_DAYS = 7
STRAY_DAYS = 30


def _mkdir(root: pathlib.Path, offset_days: int) -> pathlib.Path:
    """创建相对 TODAY 偏移 offset_days 天的日期目录。"""
    d = root / (TODAY - datetime.timedelta(days=offset_days)).isoformat()
    d.mkdir(parents=True, exist_ok=True)
    (d / "a.log").write_text("x", encoding="utf-8")
    return d


def test_date_dir_boundary_keeps_seven_days_back():
    """口径：目录日期 < 今天-7 才删。today-7 保留（对照项 today-8 必须删）。"""
    root = pathlib.Path(_tmp_root())
    keep = _mkdir(root, 7)  # 边界内：应保留
    drop = _mkdir(root, 8)  # 对照：必须删（否则本测试空跑）
    items, _total = cleanup.scan(str(root), TODAY, KEEP_DAYS, STRAY_DAYS)
    names = {i["name"] for i in items}
    assert keep.name not in names
    assert drop.name in names


def test_protected_dirs_and_files_never_deleted():
    """_archive / Crash-Log / automation.log / 非法日期目录永不进清单。"""
    root = pathlib.Path(_tmp_root())
    _mkdir(root, 30)  # 对照：老日期目录必须删
    (root / "_archive").mkdir()
    (root / "_archive" / "k.log").write_text("y", encoding="utf-8")
    (root / "Crash-Log").mkdir()
    (root / "2026-13-45").mkdir()  # 形似日期但非法
    (root / "2026-13-45" / "b.log").write_text("q", encoding="utf-8")
    (root / "automation.log").write_text("z", encoding="utf-8")

    items, _total = cleanup.scan(str(root), TODAY, KEEP_DAYS, STRAY_DAYS)
    names = {i["name"] for i in items}
    for protected in ("_archive", "Crash-Log", "2026-13-45", "automation.log"):
        assert protected not in names
    assert any(i["kind"] == "dir" for i in items)  # 对照项确实被判定删除


def test_stray_file_boundary():
    """散落文件：mtime < 今天-30 才删。边界值（恰好 30 天）保留。"""
    root = pathlib.Path(_tmp_root())
    keep = root / "keep30.log"
    drop = root / "drop31.log"
    keep.write_text("k", encoding="utf-8")
    drop.write_text("d", encoding="utf-8")
    base = datetime.datetime.combine(TODAY, datetime.time(12, 0))
    for path, offset in ((keep, 30), (drop, 31)):
        ts = (base - datetime.timedelta(days=offset)).timestamp()
        path.touch()
        import os

        os.utime(path, (ts, ts))

    items, _total = cleanup.scan(str(root), TODAY, KEEP_DAYS, STRAY_DAYS)
    names = {i["name"] for i in items}
    assert "keep30.log" not in names
    assert "drop31.log" in names


def _tmp_root() -> str:
    import tempfile

    return tempfile.mkdtemp()


def _run(root: str, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SRC), "--root", root, "--min-total-gb", "0", *extra],
        capture_output=True,
        text=True,
        check=False,
    )


def test_dry_run_deletes_nothing():
    """默认 dry-run 必须一个字节都不动。"""
    root = _tmp_root()
    old = _mkdir(pathlib.Path(root), 9)
    proc = _run(root)
    assert proc.returncode == 0
    assert old.exists(), "dry-run 不应删除任何目录"
    assert "[dry-run]" in proc.stdout


def test_apply_removes_old_dir_and_reports_size():
    """--apply 真正删除，并报告释放空间与清理后大小。"""
    root = _tmp_root()
    old = _mkdir(pathlib.Path(root), 9)
    fresh = _mkdir(pathlib.Path(root), 1)
    proc = _run(root, "--apply")
    assert proc.returncode == 0
    assert not old.exists(), "过期目录应被删除"
    assert fresh.exists(), "保留期内目录不应被删除"
    assert "清理后总大小" in proc.stdout


def test_no_purge_keeps_rollback_staging():
    """--no-purge 保留 _trash 暂存，提供回滚窗口。"""
    root = _tmp_root()
    _mkdir(pathlib.Path(root), 9)
    _run(root, "--apply", "--no-purge")
    staging = pathlib.Path(root) / "_trash"
    assert staging.is_dir()
    runs = [p for p in staging.iterdir() if p.is_dir()]
    assert runs, "应存在本轮暂存目录"
    assert (runs[0] / "manifest.json").is_file(), "暂存目录需带审计清单"


def test_below_threshold_deletes_nothing_even_with_apply():
    """总大小低于阈值时，即使 --apply 也不删（防止把日志清到裸奔）。"""
    root = _tmp_root()
    old = _mkdir(pathlib.Path(root), 9)
    proc = subprocess.run(
        [sys.executable, str(SRC), "--root", root, "--min-total-gb", "999", "--apply"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert old.exists()
    assert "未达清理阈值" in proc.stdout
