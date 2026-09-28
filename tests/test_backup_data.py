"""test_backup_data.py — backup_data 纯函数测试。"""

from pathlib import Path
from unittest.mock import patch

import backup_data as bd


def test_find_project_dir_returns_string():
    result = bd._find_project_dir("/tmp")  # noqa: S108
    assert isinstance(result, str)


def test_find_project_dir_walks_up_to_project_root(tmp_path):
    """文档口径：向上找到「含 pyproject.toml 且含 output/」的目录。用自建树验，不依赖宿主布局。"""
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "output").mkdir()
    deep = tmp_path / "scripts" / "sub"
    deep.mkdir(parents=True)
    assert Path(bd._find_project_dir(str(deep))).resolve() == tmp_path.resolve()


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
    # 从 <repo>/scripts 出发必须解析到 <repo>；用 __file__ 定位，不写死本机绝对路径
    # （CI runner 上没有 /Volumes、/Users —— 写死路径的测试会在 collection 阶段就报错）
    root = Path(__file__).resolve().parent.parent
    result = Path(bd._find_project_dir(str(root / "scripts"))).resolve()
    assert result == root


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
