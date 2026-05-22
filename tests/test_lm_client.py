import json
import threading
import time
from unittest.mock import patch, MagicMock
import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


def test_lm_client_import():
    from nemo_coding_platform.lm_client import LmClient
    assert LmClient is not None


def test_lm_client_sem_timeout_raises():
    from nemo_coding_platform.lm_client import LmClient
    client = LmClient(base_url="http://localhost:1234/v1", api_key="test")

    acquired = threading.Event()
    release_flag = threading.Event()

    def holder():
        client._sem.acquire()
        acquired.set()
        release_flag.wait(timeout=5)
        client._sem.release()

    t = threading.Thread(target=holder, daemon=True)
    t.start()
    acquired.wait(timeout=2)

    with pytest.raises(RuntimeError, match="LLM semaphore busy"):
        client._acquire(timeout=0.3)

    release_flag.set()
    t.join(timeout=2)


def test_lm_client_chat_returns_content():
    from nemo_coding_platform.lm_client import LmClient

    fake_response = json.dumps({
        "choices": [{"message": {"content": "hello", "tool_calls": []}}]
    }).encode()
    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.read.return_value = fake_response

    with patch("urllib.request.urlopen", return_value=mock_resp):
        client = LmClient(base_url="http://localhost:1234/v1", api_key="test")
        result = client.chat(messages=[{"role": "user", "content": "hi"}], timeout=5.0)

    assert result == "hello"


def test_lm_client_chat_raises_on_no_choices():
    from nemo_coding_platform.lm_client import LmClient

    fake_response = json.dumps({"choices": []}).encode()
    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.read.return_value = fake_response

    with patch("urllib.request.urlopen", return_value=mock_resp):
        from nemo_coding_platform.lm_client import LmClientError
        client = LmClient(base_url="http://localhost:1234/v1", api_key="test")
        with pytest.raises(LmClientError, match="no choices"):
            client.chat(messages=[{"role": "user", "content": "hi"}], timeout=5.0)
