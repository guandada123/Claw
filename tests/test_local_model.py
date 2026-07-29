"""test_local_model.py — local_model 测试。"""

import json
from unittest.mock import MagicMock, patch

import local_model as lm


def test_constants():
    assert lm.OLLAMA_BASE == "http://localhost:11434"
    assert lm.DEFAULT_MODEL == "qwen2.5:7b"
    assert lm.TIMEOUT > 0


@patch("local_model.urllib.request.urlopen")
def test_is_available_true(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_urlopen.return_value.__enter__.return_value = mock_resp
    lm._is_available_cache = None
    assert lm.is_available() is True


@patch("local_model.urllib.request.urlopen", side_effect=ConnectionRefusedError)
def test_is_available_false(mock_urlopen):
    lm._is_available_cache = None
    assert lm.is_available() is False


@patch("local_model.urllib.request.urlopen")
def test_list_models_returns_list(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({
        "models": [{"name": "qwen2.5:7b", "size": 4000000000, "modified_at": ""}]
    }).encode()
    mock_urlopen.return_value.__enter__.return_value = mock_resp
    result = lm.list_models()
    assert len(result) == 1
    assert result[0]["name"] == "qwen2.5:7b"


@patch("local_model.urllib.request.urlopen", side_effect=OSError)
def test_list_models_error_returns_empty(mock_urlopen):
    result = lm.list_models()
    assert result == []


@patch("local_model.is_available", return_value=True)
@patch("local_model.urllib.request.urlopen")
def test_call_success(mock_urlopen, mock_avail):
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"response": "hello from ollama"}).encode()
    mock_urlopen.return_value.__enter__.return_value = mock_resp
    result = lm.call("hello", model="qwen2.5:7b", timeout=5)
    assert result["success"] is True
    assert "hello from ollama" in result["response"]


@patch("local_model.is_available", return_value=False)
def test_call_unavailable(mock_avail):
    result = lm.call("hello", model="qwen2.5:7b", timeout=5)
    assert result["success"] is False
    assert "未运行" in result["error"]


@patch("local_model.urllib.request.urlopen")
def test_get_running_models(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({
        "models": [{"name": "qwen2.5:7b"}]
    }).encode()
    mock_urlopen.return_value.__enter__.return_value = mock_resp
    result = lm.get_running_models()
    assert "qwen2.5:7b" in result
