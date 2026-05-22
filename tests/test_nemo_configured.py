# tests/test_nemo_configured.py
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
import pytest
from pathlib import Path


def test_nemo_configured_neither():
    from nemo_coding_platform.mission_control_server import _nemo_configured
    assert _nemo_configured(None, None) is False
    assert _nemo_configured(None, "") is False
    assert _nemo_configured(None, "   ") is False


def test_nemo_configured_with_url():
    from nemo_coding_platform.mission_control_server import _nemo_configured
    assert _nemo_configured(None, "http://127.0.0.1:8765/mcp/sse") is True


def test_nemo_configured_with_db():
    from nemo_coding_platform.mission_control_server import _nemo_configured
    assert _nemo_configured(Path("/some/path.sqlite"), None) is True
    assert _nemo_configured(Path("/some/path.sqlite"), "") is True


def test_nemo_configured_with_both():
    from nemo_coding_platform.mission_control_server import _nemo_configured
    assert _nemo_configured(Path("/some/path.sqlite"), "http://127.0.0.1:8765/mcp/sse") is True
