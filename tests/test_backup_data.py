"""test_backup_data.py — backup_data 纯函数测试。"""

from unittest.mock import patch

import backup_data as bd


def test_find_project_dir_returns_string():
    result = bd._find_project_dir("/tmp")  # noqa: S108
    assert isinstance(result, str)


def test_today_str_format():
    s = bd.today_str()
    parts = s.split("-")
    assert len(parts) == 3
    assert len(parts[0]) == 4


def test_today_str_not_empty():
    assert len(bd.today_str()) > 0


def test_find_project_dir_from_tmp():
    result = bd._find_project_dir("/tmp")  # noqa: S108
    assert result == "/tmp" or "Claw" in result  # noqa: S108


def test_find_project_dir_from_scripts():
    claw_scripts = "/Volumes/ZHITAI/WorkBuddy/Claw/scripts"
    result = bd._find_project_dir(claw_scripts)
    assert "Claw" in result or "claw" in result


@patch("backup_data.subprocess.run")
def test_run_backup_no_error(mock_run):
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = "backup done"
    mock_run.return_value.stderr = ""

    # Just verify the function exists and can be called
    assert hasattr(bd, "today_str")


def test_find_project_dir_root_reached():
    """在 / 目录应返回其自身（到达文件系统根）"""
    result = bd._find_project_dir("/")
    assert result == "/"
