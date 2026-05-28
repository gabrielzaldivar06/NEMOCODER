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
