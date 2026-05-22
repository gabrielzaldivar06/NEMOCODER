from unittest.mock import patch, MagicMock
import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


def test_nemo_gateway_import():
    from nemo_coding_platform.nemo_gateway import NemoGateway
    assert NemoGateway is not None


def test_nemo_gateway_not_configured_when_no_url_no_db():
    from nemo_coding_platform.nemo_gateway import NemoGateway
    gw = NemoGateway(memory_db=None, mcp_url=None)
    assert gw.is_configured() is False


def test_nemo_gateway_configured_with_url():
    from nemo_coding_platform.nemo_gateway import NemoGateway
    gw = NemoGateway(memory_db=None, mcp_url="http://127.0.0.1:8765/mcp/sse")
    assert gw.is_configured() is True


def test_nemo_gateway_call_skips_when_not_configured():
    from nemo_coding_platform.nemo_gateway import NemoGateway
    gw = NemoGateway(memory_db=None, mcp_url=None)
    tool_calls: list = []
    result = gw.call("context_bootstrap", "start", tool_calls, task="test")
    assert result == {}
    assert tool_calls[0]["status"] == "skipped"


def test_nemo_gateway_call_invokes_mcp_when_configured():
    from nemo_coding_platform.nemo_gateway import NemoGateway

    fake_result = {"ok": True, "payload": {"context": "some context"}}
    with patch("nemo_coding_platform.nemo_gateway._mcp_call_nemo_tool", return_value=fake_result) as mock_call:
        gw = NemoGateway(memory_db=None, mcp_url="http://127.0.0.1:8765/mcp/sse")
        tool_calls: list = []
        result = gw.call("context_bootstrap", "start", tool_calls, task="test task")

    mock_call.assert_called_once()
    assert tool_calls[0]["status"] == "completed"


def test_nemo_gateway_call_disabled_tool_skips():
    from nemo_coding_platform.nemo_gateway import NemoGateway
    gw = NemoGateway(memory_db=None, mcp_url="http://127.0.0.1:8765/mcp/sse",
                     allowed_tools={"search_memories"})
    tool_calls: list = []
    result = gw.call("context_bootstrap", "start", tool_calls, task="test")
    assert result == {}
    assert tool_calls[0]["status"] == "skipped"
    assert "disabled" in tool_calls[0]["summary"].lower()
