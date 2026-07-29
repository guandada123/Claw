"""test_heartbeat_local.py — heartbeat_local 函数测试。"""

from unittest.mock import patch

import heartbeat_local as hl


def test_project_dir_exists():
    assert hl.PROJECT_DIR.exists()


def test_ollama_base_default():
    assert hl.OLLAMA_BASE == "http://localhost:11434"
    assert hl.OLLAMA_MODEL == "qwen2.5:7b"


def test_heartbeat_file_location():
    assert "heartbeat.json" in str(hl.HEARTBEAT_FILE)


@patch("heartbeat_local.DB_PATH")
@patch("pathlib.Path.exists")
def test_check_dependencies_db_ok(mock_exists, mock_db_path):
    mock_exists.return_value = True
    mock_db_path.exists.return_value = True
    with patch("sqlite3.connect") as mock_conn:
        mock_conn.return_value.__enter__.return_value.execute.return_value = True
        result = hl.check_dependencies()
    assert result["db"] is True
