# src/nemo_coding_platform/decision_agent_manager.py
"""DecisionAgentManager — runs the Decision Agent job in a background thread.

One job at a time:
  1. analyze_nemo_signal()            -> SynthesisReport
  2. build_selfmod_request_from_report() -> SelfModRequest
  3. execute_self_modification()      -> SelfModRunResult (blocks; runs Aider)
  4. Status -> AWAITING_REVIEW

Events accumulate in job.events for SSE streaming.
apply() calls self_mod_apply on a completed job and transitions to COMPLETED.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from nemo_coding_platform.core.decision_agent import (
    DecisionAgentStatus,
    SynthesisReport,
    analyze_nemo_signal,
    build_selfmod_request_from_report,
)
from nemo_coding_platform.core.self_modification import (
    SelfModRunResult,
    execute_self_modification,
    self_mod_apply,
    self_mod_status,
)

logger = logging.getLogger(__name__)


@dataclass
class DecisionAgentJob:
    job_id: str
    status: DecisionAgentStatus
    events: list[dict[str, Any]] = field(default_factory=list)
    synthesis_report: SynthesisReport | None = None
    run_result: SelfModRunResult | None = None
    run_json: str | None = None
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def _emit(self, event: dict[str, Any]) -> None:
        event.setdefault("seq", len(self.events))
        event.setdefault("ts", datetime.now(timezone.utc).isoformat())
        self.events.append(event)
        self.updated_at = event["ts"]

    def to_dict(self) -> dict[str, Any]:
        report_dict: dict[str, Any] | None = None
        if self.synthesis_report:
            r = self.synthesis_report
            report_dict = {
                "dominant_pattern": r.dominant_pattern,
                "task_type": r.task_type,
                "target_files": list(r.target_files),
                "proposed_description": r.proposed_description,
                "confidence": r.confidence,
                "rationale": r.rationale,
            }
        return {
            "job_id": self.job_id,
            "status": self.status.value,
            "events": self.events[-50:],
            "synthesis_report": report_dict,
            "run_json": self.run_json,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class DecisionAgentManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._job: DecisionAgentJob | None = None

    def current_job(self) -> DecisionAgentJob | None:
        with self._lock:
            return self._job

    def start(
        self,
        nemo_mcp_url: str,
        lm_base_url: str,
        lm_model: str,
        repo_root: str,
        lang_filter: str = "python",
        memory_db: str = ".nemo-runtimes/nemo-memory.sqlite",
    ) -> str:
        with self._lock:
            if self._job is not None and self._job.status in (
                DecisionAgentStatus.ANALYZING,
                DecisionAgentStatus.GENERATING,
                DecisionAgentStatus.EXECUTING,
            ):
                raise RuntimeError(
                    f"Decision agent already running (job {self._job.job_id})"
                )
            job_id = "da-" + uuid4().hex[:12]
            job = DecisionAgentJob(job_id=job_id, status=DecisionAgentStatus.ANALYZING)
            self._job = job

        thread = threading.Thread(
            target=self._run_loop,
            args=(job, nemo_mcp_url, lm_base_url, lm_model, repo_root, lang_filter, memory_db),
            daemon=True,
            name=f"decision-agent-{job_id}",
        )
        thread.start()
        logger.info("decision_agent: started job %s", job_id)
        return job_id

    def apply(self, job_id: str, memory_db: str) -> dict[str, Any]:
        with self._lock:
            job = self._job
        if job is None or job.job_id != job_id:
            raise FileNotFoundError(f"No decision agent job found: {job_id}")
        if job.status != DecisionAgentStatus.AWAITING_REVIEW:
            raise PermissionError(
                f"Job {job_id} is not awaiting review (status: {job.status})"
            )
        if not job.run_json:
            raise FileNotFoundError("run_json not set for this job")
        result = self_mod_apply(job.run_json, approve_review=True, memory_db=memory_db)
        with self._lock:
            job.status = DecisionAgentStatus.COMPLETED
            job._emit({"type": "applied", "detail": "changes merged to main branch"})
        return result

    def _run_loop(
        self,
        job: DecisionAgentJob,
        nemo_mcp_url: str,
        lm_base_url: str,
        lm_model: str,
        repo_root: str,
        lang_filter: str,
        memory_db: str,
    ) -> None:
        try:
            # Phase 1: Analyze NEMO signal
            job._emit({"type": "phase", "phase": "analyzing", "detail": "reading NEMO signal"})
            report = analyze_nemo_signal(
                nemo_mcp_url=nemo_mcp_url,
                lm_base_url=lm_base_url,
                lm_model=lm_model,
                lang_filter=lang_filter,
                on_event=lambda e: job._emit({"type": "nemo_event", **e}),
            )
            with self._lock:
                job.synthesis_report = report
                job.status = DecisionAgentStatus.GENERATING
            job._emit({
                "type": "synthesis_complete",
                "dominant_pattern": report.dominant_pattern,
                "confidence": report.confidence,
                "task_type": report.task_type,
                "target_files": list(report.target_files),
                "proposed_description": report.proposed_description,
            })

            # Phase 2: Build SelfModRequest
            job._emit({
                "type": "phase",
                "phase": "generating",
                "detail": f"building SelfModRequest (type={report.task_type})",
            })
            request = build_selfmod_request_from_report(
                report, repo_root=repo_root, memory_db=memory_db
            )

            # Phase 3: Execute self-modification (blocking — runs Aider)
            with self._lock:
                job.status = DecisionAgentStatus.EXECUTING
            targets = ", ".join(request.target_files) or "agent-selected files"
            job._emit({
                "type": "phase",
                "phase": "executing",
                "detail": f"running Aider on {targets}",
            })
            task_id = f"da-task-{job.job_id}"
            run_id = f"da-run-{job.job_id}"
            run_result = execute_self_modification(request, task_id=task_id, run_id=run_id)
            with self._lock:
                job.run_result = run_result
                job.run_json = run_result.run_json
            status = self_mod_status(run_result.run_json)
            job._emit({
                "type": "execution_done",
                "grade": status.get("grade"),
                "score": status.get("score"),
                "mergeable": status.get("mergeable"),
                "risk_flags": status.get("risk_flags", []),
                "changed_files": status.get("changed_files", []),
                "run_json": run_result.run_json,
            })

            # Phase 4: Await human review via merge gate
            with self._lock:
                job.status = DecisionAgentStatus.AWAITING_REVIEW
            job._emit({
                "type": "phase",
                "phase": "awaiting_review",
                "detail": f"grade={status.get('grade')} — awaiting human review",
            })

        except Exception as exc:
            logger.exception("decision_agent job %s failed", job.job_id)
            with self._lock:
                job.status = DecisionAgentStatus.FAILED
                job.error = str(exc)[:500]
            job._emit({"type": "error", "detail": str(exc)[:400]})
