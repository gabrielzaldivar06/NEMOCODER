# tests/test_decision_agent.py
from __future__ import annotations
import json
from unittest.mock import patch
import pytest
from nemo_coding_platform.core.decision_agent import (
    DecisionAgentStatus,
    SynthesisReport,
    analyze_nemo_signal,
    build_selfmod_request_from_report,
)
from nemo_coding_platform.core.self_modification import SelfModRequest, SelfModTaskType


def _nemo_stub(tool_name, **kwargs):
    if tool_name == "memory_chronicle":
        return {"memories": [{"content": "repair_failure python runtime_error subprocess", "tags": ["repair_failure"]}]}
    if tool_name == "search_memories":
        return {"results": [{"content": "[critic_cal][python] score=6.5 exec_ok=True threshold=7.5 stop=quality_threshold iterations=3", "tags": ["critic_calibration"]}]}
    return {}


def test_synthesis_report_defaults():
    r = SynthesisReport(
        dominant_pattern="runtime_error in python",
        task_type="bug_fix",
        target_files=("src/nemo_coding_platform/plan_loop.py",),
        proposed_description="Fix runtime errors in plan loop",
        confidence=0.75,
        rationale="Most common failure pattern",
    )
    assert r.dominant_pattern == "runtime_error in python"
    assert r.confidence == 0.75
    assert r.task_type == "bug_fix"


def test_decision_agent_status_values():
    assert DecisionAgentStatus.IDLE == "idle"
    assert DecisionAgentStatus.ANALYZING == "analyzing"
    assert DecisionAgentStatus.AWAITING_REVIEW == "awaiting_review"


def test_analyze_nemo_signal_with_stub():
    _lm_response = json.dumps({
        "dominant_pattern": "runtime_error in python subprocess",
        "task_type": "bug_fix",
        "target_files": ["src/nemo_coding_platform/plan_loop.py"],
        "proposed_description": "Add better error handling for subprocess execution",
        "confidence": 0.82,
        "rationale": "Appears in 5 of 8 recent failure memories",
    })
    with patch("nemo_coding_platform.core.decision_agent._lm_call", return_value=_lm_response), \
         patch("nemo_coding_platform.core.decision_agent.mcp_call_nemo_tool", side_effect=lambda t, **kw: _nemo_stub(t, **kw)):
        report = analyze_nemo_signal(
            nemo_mcp_url="http://127.0.0.1:8765/mcp/sse",
            lm_base_url="http://localhost:1234/v1",
            lm_model="test-model",
        )
    assert report.dominant_pattern == "runtime_error in python subprocess"
    assert report.confidence == 0.82
    assert "plan_loop.py" in report.target_files[0]


def test_analyze_nemo_signal_lm_failure_returns_fallback():
    with patch("nemo_coding_platform.core.decision_agent._lm_call", return_value=""), \
         patch("nemo_coding_platform.core.decision_agent.mcp_call_nemo_tool", side_effect=lambda t, **kw: _nemo_stub(t, **kw)):
        report = analyze_nemo_signal(
            nemo_mcp_url="http://127.0.0.1:8765/mcp/sse",
            lm_base_url="",
            lm_model="",
        )
    assert isinstance(report, SynthesisReport)
    assert report.confidence <= 0.5


def test_build_selfmod_request_from_report():
    report = SynthesisReport(
        dominant_pattern="runtime_error",
        task_type="bug_fix",
        target_files=("src/nemo_coding_platform/plan_loop.py",),
        proposed_description="Fix runtime errors in plan loop subprocess calls",
        confidence=0.8,
        rationale="test",
    )
    req = build_selfmod_request_from_report(report, repo_root="c:/dev/dev4")
    assert isinstance(req, SelfModRequest)
    assert req.task_type == SelfModTaskType.BUG_FIX
    assert "plan_loop.py" in req.target_files[0]
    assert req.description == "Fix runtime errors in plan loop subprocess calls"


def test_build_selfmod_request_unknown_task_type_defaults_to_bug_fix():
    report = SynthesisReport(
        dominant_pattern="test", task_type="unknown_type",
        target_files=("src/nemo_coding_platform/plan_loop.py",),
        proposed_description="desc", confidence=0.5, rationale="r",
    )
    req = build_selfmod_request_from_report(report, repo_root=".")
    assert req.task_type == SelfModTaskType.BUG_FIX


import threading
import time
from unittest.mock import MagicMock, patch
from nemo_coding_platform.decision_agent_manager import DecisionAgentManager, DecisionAgentJob
from nemo_coding_platform.core.decision_agent import _FALLBACK_REPORT


def test_manager_initial_state():
    m = DecisionAgentManager()
    assert m.current_job() is None


def test_manager_start_returns_job_id():
    m = DecisionAgentManager()
    with patch("nemo_coding_platform.decision_agent_manager.analyze_nemo_signal",
               return_value=_FALLBACK_REPORT), \
         patch("nemo_coding_platform.decision_agent_manager.execute_self_modification",
               side_effect=RuntimeError("blocked in test")):
        job_id = m.start(nemo_mcp_url="", lm_base_url="", lm_model="", repo_root=".")
    assert job_id.startswith("da-")
    time.sleep(0.15)
    job = m.current_job()
    assert job is not None
    assert job.job_id == job_id


def test_manager_emits_phase_events():
    m = DecisionAgentManager()

    def fake_analyze(*args, on_event=None, **kwargs):
        if on_event:
            on_event({"type": "signal_read", "detail": "reading failures"})
            on_event({"type": "synthesizing", "detail": "running LLM"})
        return _FALLBACK_REPORT

    with patch("nemo_coding_platform.decision_agent_manager.analyze_nemo_signal",
               side_effect=fake_analyze), \
         patch("nemo_coding_platform.decision_agent_manager.execute_self_modification",
               side_effect=RuntimeError("blocked in test")):
        job_id = m.start(nemo_mcp_url="", lm_base_url="", lm_model="", repo_root=".")
    time.sleep(0.3)
    job = m.current_job()
    assert job is not None
    types = [e.get("type") for e in job.events]
    assert "signal_read" in types or "nemo_event" in types


def test_manager_blocks_double_start():
    m = DecisionAgentManager()
    started = threading.Event()

    def slow_analyze(*args, on_event=None, **kwargs):
        started.set()
        time.sleep(30)
        return _FALLBACK_REPORT

    with patch("nemo_coding_platform.decision_agent_manager.analyze_nemo_signal",
               side_effect=slow_analyze):
        m.start(nemo_mcp_url="", lm_base_url="", lm_model="", repo_root=".")
        started.wait(timeout=1.0)
        with pytest.raises(RuntimeError, match="already running"):
            m.start(nemo_mcp_url="", lm_base_url="", lm_model="", repo_root=".")
