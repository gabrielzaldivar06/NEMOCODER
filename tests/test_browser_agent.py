import time
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import pytest

from nemo_coding_platform.browser_agent import (
    _is_dangerous,
    _browser_sessions,
    _session_lock,
    BrowserSession,
    execute_browser_task,
)


# ---------------------------------------------------------------------------
# _is_dangerous
# ---------------------------------------------------------------------------

def test_is_dangerous_submit_in_target():
    assert _is_dangerous({"action": "click", "target": "submit", "value": ""})


def test_is_dangerous_buy_in_value():
    assert _is_dangerous({"action": "fill", "target": "#q", "value": "buy now"})


def test_is_dangerous_safe_target():
    assert not _is_dangerous({"action": "click", "target": "Search", "value": ""})


def test_is_dangerous_checkout_in_target():
    assert _is_dangerous({"action": "click", "target": "Checkout", "value": ""})


def test_is_dangerous_case_insensitive():
    assert _is_dangerous({"action": "click", "target": "SUBMIT button", "value": ""})


# ---------------------------------------------------------------------------
# execute_browser_task — VLM returns "done" immediately
# ---------------------------------------------------------------------------

def _make_pw_mocks():
    """Build playwright context manager mock."""
    page = MagicMock()
    page.title.return_value = "Test Page"
    context = MagicMock()
    browser = MagicMock()
    browser.new_context.return_value = context
    context.new_page.return_value = page

    pw_obj = MagicMock()
    pw_obj.chromium.launch.return_value = browser

    pw_cm = MagicMock()
    pw_cm.__enter__ = MagicMock(return_value=pw_obj)
    pw_cm.__exit__ = MagicMock(return_value=False)
    return pw_cm, page


def test_execute_browser_task_vlm_done_on_first_step(tmp_path):
    pw_cm, page = _make_pw_mocks()
    fake_screenshot = tmp_path / "fake.png"
    fake_screenshot.write_bytes(b"\x89PNG\r\n\x1a\n")  # minimal PNG header

    with patch("nemo_coding_platform.browser_agent.sync_playwright", return_value=pw_cm), \
         patch(
             "nemo_coding_platform.browser_agent._take_screenshot",
             return_value=(str(fake_screenshot), "/api/artifacts/image/fake.png"),
         ), \
         patch(
             "nemo_coding_platform.browser_agent._vlm_action",
             return_value={"action": "done", "target": "", "value": "", "reason": "task complete"},
         ):
        events = list(execute_browser_task(
            session_id="sess-done",
            url="https://example.com",
            task="find the title",
            credential_alias=None,
            max_steps=5,
            artifacts_dir=tmp_path,
            base_url="http://localhost:1234/v1",
            vlm_model="test-vlm",
            vault_db_path=None,
        ))

    step_events = [e for e in events if e["type"] == "step"]
    done_events = [e for e in events if e["type"] == "done"]
    assert len(step_events) == 1
    assert done_events[0]["success"] is True
    assert done_events[0]["steps_taken"] == 1


def test_execute_browser_task_max_steps_reached(tmp_path):
    pw_cm, page = _make_pw_mocks()
    fake_screenshot = tmp_path / "fake.png"
    fake_screenshot.write_bytes(b"\x89PNG")

    with patch("nemo_coding_platform.browser_agent.sync_playwright", return_value=pw_cm), \
         patch(
             "nemo_coding_platform.browser_agent._take_screenshot",
             return_value=(str(fake_screenshot), "/api/artifacts/image/fake.png"),
         ), \
         patch(
             "nemo_coding_platform.browser_agent._vlm_action",
             return_value={"action": "click", "target": "#btn", "value": "", "reason": "keep going"},
         ):
        events = list(execute_browser_task(
            session_id="sess-max",
            url="https://example.com",
            task="click forever",
            credential_alias=None,
            max_steps=3,
            artifacts_dir=tmp_path,
            base_url="http://localhost:1234/v1",
            vlm_model="test-vlm",
            vault_db_path=None,
        ))

    done = [e for e in events if e["type"] == "done"][0]
    assert done["success"] is False
    assert done["reason"] == "max_steps_reached"
    assert done["steps_taken"] == 3


def test_execute_browser_task_cancel(tmp_path):
    """Session cancel flag stops the loop."""
    pw_cm, page = _make_pw_mocks()
    fake_screenshot = tmp_path / "fake.png"
    fake_screenshot.write_bytes(b"\x89PNG")

    call_count = 0

    def slow_vlm(*_a, **_kw):
        nonlocal call_count
        call_count += 1
        # After first step, mark session as cancelled
        with _session_lock:
            for sess in _browser_sessions.values():
                sess.cancel = True
        return {"action": "click", "target": "#btn", "value": "", "reason": "step"}

    with patch("nemo_coding_platform.browser_agent.sync_playwright", return_value=pw_cm), \
         patch(
             "nemo_coding_platform.browser_agent._take_screenshot",
             return_value=(str(fake_screenshot), "/api/artifacts/image/fake.png"),
         ), \
         patch("nemo_coding_platform.browser_agent._vlm_action", side_effect=slow_vlm):
        events = list(execute_browser_task(
            session_id="sess-cancel",
            url="https://example.com",
            task="do things",
            credential_alias=None,
            max_steps=10,
            artifacts_dir=tmp_path,
            base_url="http://localhost:1234/v1",
            vlm_model="test-vlm",
            vault_db_path=None,
        ))

    done = [e for e in events if e["type"] == "done"]
    assert done[0]["reason"] == "cancelled"


def test_execute_browser_task_playwright_not_installed(tmp_path):
    with patch("nemo_coding_platform.browser_agent.sync_playwright", side_effect=ImportError("no playwright")):
        events = list(execute_browser_task(
            session_id="sess-nopw",
            url="https://example.com",
            task="test",
            credential_alias=None,
            max_steps=3,
            artifacts_dir=tmp_path,
            base_url="http://localhost:1234/v1",
            vlm_model=None,
            vault_db_path=None,
        ))
    assert events[0]["type"] == "error"
    assert "Playwright" in events[0]["message"]


def test_max_steps_capped_at_20(tmp_path):
    pw_cm, _ = _make_pw_mocks()
    fake_screenshot = tmp_path / "fake.png"
    fake_screenshot.write_bytes(b"\x89PNG")

    with patch("nemo_coding_platform.browser_agent.sync_playwright", return_value=pw_cm), \
         patch(
             "nemo_coding_platform.browser_agent._take_screenshot",
             return_value=(str(fake_screenshot), "/api/artifacts/image/fake.png"),
         ), \
         patch(
             "nemo_coding_platform.browser_agent._vlm_action",
             return_value={"action": "click", "target": "#b", "value": "", "reason": "x"},
         ):
        events = list(execute_browser_task(
            session_id="sess-cap",
            url="https://example.com",
            task="go",
            credential_alias=None,
            max_steps=999,  # should be capped at 20
            artifacts_dir=tmp_path,
            base_url="http://localhost:1234/v1",
            vlm_model="test-vlm",
            vault_db_path=None,
        ))

    done = [e for e in events if e["type"] == "done"][0]
    assert done["steps_taken"] == 20
