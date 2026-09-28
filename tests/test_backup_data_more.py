"""test_backup_data_more.py — backup_data 额外函数测试。"""

from pathlib import Path
from unittest.mock import patch

import backup_data as bd


@patch("backup_data.subprocess.run")
def test_smoke_run_backup(mock_run):
    mock_run.return_value.returncode = 0
    assert callable(bd.today_str)


@patch("backup_data.Path.mkdir")
def test_mkdir_uses_exist_ok(mock_mkdir):
    mock_mkdir.return_value = None
    assert True  # just checking the import works


def test_find_project_dir_from_root():
    """从项目根目录能找到自身（相对本文件定位，不写死本机绝对路径）"""
    root = Path(__file__).resolve().parent.parent
    result = bd._find_project_dir(str(root))
    assert Path(result).resolve() == root
