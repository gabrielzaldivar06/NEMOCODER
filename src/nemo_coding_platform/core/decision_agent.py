# src/nemo_coding_platform/core/decision_agent.py
"""Decision Agent — reads NEMO signal and synthesizes a SelfModRequest.

Public API:
  analyze_nemo_signal()               NEMO signal -> LLM -> SynthesisReport
  build_selfmod_request_from_report() SynthesisReport -> SelfModRequest
"""
from __future__ import annotations

import json
import logging
import re
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Callable

logger = logging.getLogger(__name__)

from nemo_coding_platform.core.self_modification import SelfModRequest, SelfModTaskType
from nemo_coding_platform.spacecode_mcp_tools import mcp_call_nemo_tool


class DecisionAgentStatus(StrEnum):
    IDLE = "idle"
    ANALYZING = "analyzing"
    GENERATING = "generating"
    EXECUTING = "executing"
    AWAITING_REVIEW = "awaiting_review"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SynthesisReport:
    dominant_pattern: str
    task_type: str          # "bug_fix" | "architecture_hardening" | "refactor" | "test_coverage"
    target_files: tuple[str, ...]
    proposed_description: str
    confidence: float
    rationale: str


_SYNTHESIS_SYS = (
    "You are a software quality analyst for a Python AI coding platform called Space Code. "
    "Analyze NEMO memory signal and identify the single highest-ROI code change to make. "
    "Respond ONLY with valid JSON matching this exact schema:\n"
    '{"dominant_pattern": str, '
    '"task_type": "bug_fix"|"architecture_hardening"|"refactor"|"test_coverage"|"tool_expansion"|"documentation", '
    '"target_files": [str], '
    '"proposed_description": str, '
    '"confidence": float 0.0-1.0, '
    '"rationale": str}'
)

_FALLBACK_REPORT = SynthesisReport(
    dominant_pattern="insufficient signal — no dominant failure pattern found",
    task_type="test_coverage",
    target_files=("src/nemo_coding_platform/plan_loop.py",),
    proposed_description=(
        "Add unit tests for plan loop edge cases to improve observability "
        "and detect regressions in score calibration"
    ),
    confidence=0.3,
    rationale="Fallback: LLM synthesis unavailable or NEMO signal too sparse",
)

_TASK_TYPE_MAP: dict[str, SelfModTaskType] = {
    "bug_fix": SelfModTaskType.BUG_FIX,
    "architecture_hardening": SelfModTaskType.ARCHITECTURE_HARDENING,
    "refactor": SelfModTaskType.REFACTOR,
    "test_coverage": SelfModTaskType.TEST_COVERAGE,
    "tool_expansion": SelfModTaskType.TOOL_EXPANSION,
    "documentation": SelfModTaskType.DOCUMENTATION,
}


def _lm_call(
    system_prompt: str,
    user_prompt: str,
    base_url: str,
    model: str,
    max_tokens: int = 512,
) -> str:
    if not base_url or not model:
        return ""
    try:
        body = json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.0,
        }).encode()
        req = urllib.request.Request(
            base_url.rstrip("/") + "/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        logger.debug("_lm_call failed: %s", exc)
        return ""


def _nemo(tool_name: str, nemo_mcp_url: str, **kwargs: Any) -> dict[str, Any]:
    result = mcp_call_nemo_tool(
        tool_name, lifecycle_phase="review", memory_db="", mcp_url=nemo_mcp_url, **kwargs
    )
    if not isinstance(result, dict):
        return {}
    # mcp_call_nemo_tool wraps in {"payload": {...}}; test stubs return raw dict directly
    return result.get("payload") or result


def _collect_signal(
    nemo_mcp_url: str,
    lang_filter: str,
    on_event: Callable[[dict], None] | None,
) -> str:
    lookback = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
    parts: list[str] = []

    if on_event:
        on_event({"type": "signal_read", "detail": "reading recent failures"})
    failures = _nemo("memory_chronicle", nemo_mcp_url,
        date_from=lookback, limit=30, tags_include=["repair_failure", "plan_failure"])
    mems = failures.get("memories") or failures.get("results") or []
    if mems:
        parts.append("RECENT FAILURES (last 14 days):\n" + "\n".join(
            f"- {str(m.get('content') or '')[:200]}"
            for m in mems[:15] if isinstance(m, dict)
        ))

    if on_event:
        on_event({"type": "signal_read", "detail": "reading critic calibration data"})
    cal = _nemo("search_memories", nemo_mcp_url,
        query=f"critic calibration {lang_filter}", limit=10,
        tags_include=["critic_calibration", lang_filter], min_importance=3, compact=True)
    cal_mems = cal.get("results") or cal.get("memories") or []
    if cal_mems:
        parts.append("CRITIC CALIBRATION (score reliability):\n" + "\n".join(
            f"- {str(m.get('content') or '')[:200]}"
            for m in cal_mems[:8] if isinstance(m, dict)
        ))

    if on_event:
        on_event({"type": "signal_read", "detail": "reading improvement patterns"})
    contr = _nemo("search_memories", nemo_mcp_url,
        query=f"improvement patterns {lang_filter}", limit=8,
        tags_include=["contrastive", lang_filter], min_importance=6, compact=True)
    contr_mems = contr.get("results") or contr.get("memories") or []
    if contr_mems:
        parts.append("IMPROVEMENT PATTERNS (what made code better):\n" + "\n".join(
            f"- {str(m.get('content') or '')[:200]}"
            for m in contr_mems[:5] if isinstance(m, dict)
        ))

    if on_event:
        on_event({"type": "signal_read", "detail": "reading recurring error anchors"})
    acc = _nemo("search_memories", nemo_mcp_url,
        query="recurring failure practice anchor", limit=6,
        tags_include=["failure_accumulator"], min_importance=6, compact=True)
    acc_mems = acc.get("results") or acc.get("memories") or []
    if acc_mems:
        parts.append("RECURRING ERRORS (failure accumulator):\n" + "\n".join(
            f"- {str(m.get('content') or '')[:200]}"
            for m in acc_mems[:4] if isinstance(m, dict)
        ))

    return "\n\n".join(parts)


def _parse_synthesis_response(text: str) -> SynthesisReport | None:
    json_match = re.search(r'\{.*\}', text, re.DOTALL)
    if not json_match:
        return None
    try:
        data = json.loads(json_match.group(0))
        target_files = data.get("target_files") or []
        if isinstance(target_files, str):
            target_files = [target_files]
        return SynthesisReport(
            dominant_pattern=str(data.get("dominant_pattern") or "")[:200],
            task_type=str(data.get("task_type") or "bug_fix"),
            target_files=tuple(str(f) for f in target_files[:4] if f),
            proposed_description=str(data.get("proposed_description") or "")[:500],
            confidence=max(0.0, min(1.0, float(data.get("confidence") or 0.5))),
            rationale=str(data.get("rationale") or "")[:400],
        )
    except (json.JSONDecodeError, ValueError, TypeError):
        return None


def analyze_nemo_signal(
    nemo_mcp_url: str,
    lm_base_url: str,
    lm_model: str,
    lang_filter: str = "python",
    on_event: Callable[[dict], None] | None = None,
) -> SynthesisReport:
    """Read NEMO signal, synthesize with LLM, return SynthesisReport. Never raises."""
    try:
        signal_block = _collect_signal(nemo_mcp_url, lang_filter, on_event)
        if not signal_block.strip():
            if on_event:
                on_event({"type": "fallback", "detail": "NEMO signal empty — using fallback"})
            return _FALLBACK_REPORT

        if on_event:
            on_event({"type": "synthesizing", "detail": "running LLM synthesis on signal"})
        user_prompt = (
            "Analyze this NEMO signal from Space Code's plan loop and identify "
            "the single highest-ROI code change:\n\n"
            f"{signal_block[:3000]}\n\n"
            "Consider: which error class repeats most, whether quality_threshold is miscalibrated, "
            "what code patterns consistently improve scores. "
            "Suggest a specific, implementable change to ONE existing file "
            "(prefer plan_loop.py, handoff_job_manager.py, or mission_control_server.py for "
            "backend logic; tests/ for test coverage gaps)."
        )
        raw = _lm_call(_SYNTHESIS_SYS, user_prompt, lm_base_url, lm_model, max_tokens=512)
        report = _parse_synthesis_response(raw) if raw else None
        if report is None:
            if on_event:
                on_event({"type": "fallback", "detail": "LLM synthesis failed — using fallback"})
            return _FALLBACK_REPORT
        if on_event:
            on_event({
            "type": "synthesis_done",
            "confidence": report.confidence,
            "dominant_pattern": report.dominant_pattern,
        })
        return report
    except Exception as exc:
        if on_event:
            on_event({"type": "error", "detail": str(exc)[:200]})
        return _FALLBACK_REPORT


def build_selfmod_request_from_report(
    report: SynthesisReport,
    repo_root: str,
    output_dir: str = ".spacecode-runtimes/self-mod/decision-agent-runs",
    memory_db: str = ".nemo-runtimes/nemo-memory.sqlite",
) -> SelfModRequest:
    task_type = _TASK_TYPE_MAP.get(report.task_type, SelfModTaskType.BUG_FIX)
    return SelfModRequest(
        description=report.proposed_description,
        task_type=task_type,
        target_files=report.target_files,
        validation_policy="smoke",
        repair_budget=2,
        memory_db=memory_db,
        repo_root=repo_root,
        output_dir=output_dir,
        real_validation=True,
    )
