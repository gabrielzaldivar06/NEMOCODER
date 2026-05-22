"""Tests for _lmstudio_chat_completion internals — specifically _do_request."""
import json
from unittest.mock import MagicMock, patch

import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


def _make_mock_response(body: bytes) -> MagicMock:
    resp = MagicMock()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    resp.read.return_value = body
    return resp


def _make_payload(base_url: str = "http://localhost:1234/v1") -> dict:
    return {"model_base_url": base_url, "model": "test-model", "objective": "test"}


def test_do_request_raises_value_error_on_non_json_response():
    """_do_request must raise ValueError (not JSONDecodeError) when LM Studio returns garbage."""
    import nemo_coding_platform.mission_control_server as mcs

    garbage = b"Internal Server Error"
    mock_resp = _make_mock_response(garbage)

    with patch("urllib.request.urlopen", return_value=mock_resp), \
         patch.object(mcs.LmClient._sem, "acquire", return_value=True), \
         patch.object(mcs.LmClient._sem, "release"):
        with pytest.raises(ValueError, match="non-JSON"):
            mcs._lmstudio_chat_completion(_make_payload(), "hello", "")


def test_do_request_raises_value_error_message_includes_raw_snippet():
    """Error message must include up to 200 chars of the raw response."""
    import nemo_coding_platform.mission_control_server as mcs

    garbage = b"not-json-at-all"
    mock_resp = _make_mock_response(garbage)

    with patch("urllib.request.urlopen", return_value=mock_resp), \
         patch.object(mcs.LmClient._sem, "acquire", return_value=True), \
         patch.object(mcs.LmClient._sem, "release"):
        with pytest.raises(ValueError) as exc_info:
            mcs._lmstudio_chat_completion(_make_payload(), "hello", "")
    assert "not-json-at-all" in str(exc_info.value)
