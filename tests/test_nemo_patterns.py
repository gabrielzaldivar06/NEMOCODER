import pytest
from nemo_coding_platform.core.nemo_patterns import (
    nemo_before_attempt,
    nemo_after_failure,
    nemo_after_success,
    nemo_cross_run_context,
)


def test_before_attempt_none_adapter():
    result = nemo_before_attempt(None, "repair: pytest failed")
    assert result == ""


def test_before_attempt_in_memory_adapter():
    from nemo_coding_platform.core.nemo_adapter import InMemoryNemoAdapter
    result = nemo_before_attempt(InMemoryNemoAdapter(), "repair: pytest failed")
    assert result == ""


def test_after_failure_none_adapter():
    # Must not raise
    nemo_after_failure(None, "evidence text", task_id="t1", attempt_n=1)


def test_after_failure_calls_cognitive_ingest(mocker):
    adapter = mocker.MagicMock()
    adapter.call.return_value = (adapter, mocker.MagicMock(ok=True, payload={}))
    nemo_after_failure(adapter, "cmd failed with exit 1", task_id="task-42", attempt_n=2)
    adapter.call.assert_called_once()
    call_args = adapter.call.call_args
    assert call_args.kwargs.get("memory_type") == "repair_failure"
    assert "task-42" in call_args.kwargs.get("tags", [])


def test_after_success_uses_high_importance(mocker):
    adapter = mocker.MagicMock()
    adapter.call.return_value = (adapter, mocker.MagicMock(ok=True, payload={}))
    nemo_after_success(adapter, "fixed: add auth. diff: +10 lines", task_id="task-42")
    call_args = adapter.call.call_args
    assert call_args.kwargs.get("importance_level") == 8


def test_cross_run_context_returns_empty_for_none():
    result = nemo_cross_run_context(None, "task-1", "add JWT auth")
    assert result == ""
