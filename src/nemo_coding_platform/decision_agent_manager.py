# src/nemo_coding_platform/decision_agent_manager.py
"""DecisionAgentManager — runs the Decision Agent job in a background thread.

One job at a time:
  1. analyze_nemo_signal()            -> SynthesisReport
  2. build_selfmod_request_from_report() -> SelfModRequest
  3. execute_self_modification()      -> SelfModRunResult (blocks; runs Aider)
  4. Status -> AWAITING_REVIEW

Events accumulate in job.events for SSE streaming.
apply() calls self_mod_apply on a completed job and transitions to COMPLETED.

Persistence: when snapshot_root is provided, the current job is written to
`<snapshot_root>/current.json` after every event. On startup, the file is loaded
and a non-terminal job is force-transitioned to FAILED with a "lost across
restart" note (its worker thread did not survive the restart).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
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

_TERMINAL_STATUSES = {
    DecisionAgentStatus.COMPLETED,
    DecisionAgentStatus.FAILED,
    DecisionAgentStatus.AWAITING_REVIEW,
}
_NON_TERMINAL_STATUSES = {
    DecisionAgentStatus.IDLE,
    DecisionAgentStatus.ANALYZING,
    DecisionAgentStatus.GENERATING,
    DecisionAgentStatus.EXECUTING,
}
# Min seconds between auto-triggers. Prevents a flaky failing job from spamming
# the agent — the cooldown is per-process and shared across all triggers.
AUTO_TRIGGER_COOLDOWN_SECONDS = 1800  # 30 minutes


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
    _emit_lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    _persist_fn: Callable[[], None] | None = field(default=None, repr=False, compare=False)

    def _emit(self, event: dict[str, Any]) -> None:
        with self._emit_lock:
            event.setdefault("seq", len(self.events))
            event.setdefault("ts", datetime.now(timezone.utc).isoformat())
            self.events.append(event)
            self.updated_at = event["ts"]
        # Persist outside the emit lock to avoid blocking other emitters during disk IO.
        # Best-effort — failure to persist must not interrupt the agent's work.
        if self._persist_fn is not None:
            try:
                self._persist_fn()
            except Exception:  # noqa: BLE001
                logger.debug("decision_agent: persist failed", exc_info=True)

    def to_dict(self) -> dict[str, Any]:
        with self._emit_lock:
            events_snapshot = self.events[-50:]
            updated_at = self.updated_at
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
            "events": events_snapshot,
            "synthesis_report": report_dict,
            "run_json": self.run_json,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": updated_at,
        }


class DecisionAgentManager:
    def __init__(self, snapshot_root: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._job: DecisionAgentJob | None = None
        self._snapshot_root: Path | None = None
        self._last_auto_trigger_ts: float = 0.0
        if snapshot_root is not None:
            self.configure_snapshot_root(snapshot_root)

    def configure_snapshot_root(self, snapshot_root: Path) -> None:
        """Set the persistence directory and load the most recent job if any."""
        self._snapshot_root = Path(snapshot_root)
        try:
            self._snapshot_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("decision_agent: cannot create snapshot dir: %s", exc)
            self._snapshot_root = None
            return
        self._restore_from_snapshot()

    def _snapshot_path(self) -> Path | None:
        return self._snapshot_root / "current.json" if self._snapshot_root else None

    def _persist_current_job(self) -> None:
        target = self._snapshot_path()
        if target is None:
            return
        job = self._job
        if job is None:
            return
        snapshot = job.to_dict()
        # Include a tag so a future restore knows whether the job had completed cleanly.
        snapshot["_schema"] = 1
        temp = target.with_suffix(".tmp")
        try:
            temp.write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")
            temp.replace(target)
        except OSError as exc:
            logger.debug("decision_agent: snapshot write failed: %s", exc)

    def _restore_from_snapshot(self) -> None:
        target = self._snapshot_path()
        if target is None or not target.exists():
            return
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("decision_agent: corrupt snapshot %s — ignoring: %s", target, exc)
            return
        if not isinstance(payload, dict):
            return
        try:
            status = DecisionAgentStatus(payload.get("status") or "idle")
        except ValueError:
            status = DecisionAgentStatus.FAILED
        report_payload = payload.get("synthesis_report")
        report: SynthesisReport | None = None
        if isinstance(report_payload, dict):
            try:
                report = SynthesisReport(
                    dominant_pattern=str(report_payload.get("dominant_pattern") or ""),
                    task_type=str(report_payload.get("task_type") or "bug_fix"),
                    target_files=tuple(str(f) for f in (report_payload.get("target_files") or [])),
                    proposed_description=str(report_payload.get("proposed_description") or ""),
                    confidence=float(report_payload.get("confidence") or 0.0),
                    rationale=str(report_payload.get("rationale") or ""),
                )
            except Exception:  # noqa: BLE001
                report = None
        job = DecisionAgentJob(
            job_id=str(payload.get("job_id") or ("da-" + uuid4().hex[:12])),
            status=status,
            events=list(payload.get("events") or []),
            synthesis_report=report,
            run_json=payload.get("run_json"),
            error=payload.get("error"),
            created_at=str(payload.get("created_at") or datetime.now(timezone.utc).isoformat()),
            updated_at=str(payload.get("updated_at") or datetime.now(timezone.utc).isoformat()),
        )
        # Assign before wiring _persist_fn so emit() can read self._job during persist.
        self._job = job
        job._persist_fn = self._persist_current_job
        # If the job was in a non-terminal status when the server died, its worker
        # thread is gone. Mark as failed with a note rather than letting it look "live".
        if status in _NON_TERMINAL_STATUSES and status != DecisionAgentStatus.IDLE:
            job.status = DecisionAgentStatus.FAILED
            job.error = (job.error or "")[:300] + " [recovered: worker thread lost across server restart]"
            job._emit({
                "type": "restart_recovery",
                "detail": f"job was {status.value} when server stopped; marked failed",
            })
        logger.info(
            "decision_agent: restored job %s status=%s from snapshot",
            job.job_id, job.status.value,
        )

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
            job._persist_fn = self._persist_current_job
            self._job = job

        # Persist initial state before launching the worker.
        self._persist_current_job()

        thread = threading.Thread(
            target=self._run_loop,
            args=(job, nemo_mcp_url, lm_base_url, lm_model, repo_root, lang_filter, memory_db),
            daemon=True,
            name=f"decision-agent-{job_id}",
        )
        thread.start()
        logger.info("decision_agent: started job %s", job_id)
        return job_id

    def maybe_trigger_on_failure(
        self,
        *,
        nemo_mcp_url: str,
        lm_base_url: str,
        lm_model: str,
        repo_root: str,
        memory_db: str = ".nemo-runtimes/nemo-memory.sqlite",
        lang_filter: str = "python",
        failure_context: str = "",
    ) -> str | None:
        """Auto-fire the decision agent after a handoff job failed, with safety guards.

        Returns the job_id when a new run is launched, else None.

        Guards:
          - No-op if a DA job is currently running (analyzing/generating/executing).
          - No-op if a previous DA was triggered less than AUTO_TRIGGER_COOLDOWN_SECONDS
            ago — prevents a flaky job from spamming the agent.
        """
        now = time.time()
        with self._lock:
            existing = self._job
            running = existing is not None and existing.status in (
                DecisionAgentStatus.ANALYZING,
                DecisionAgentStatus.GENERATING,
                DecisionAgentStatus.EXECUTING,
            )
            if running:
                logger.debug("decision_agent: auto-trigger skipped (running %s)", existing.job_id)
                return None
            since_last = now - self._last_auto_trigger_ts
            if since_last < AUTO_TRIGGER_COOLDOWN_SECONDS:
                logger.debug(
                    "decision_agent: auto-trigger skipped (cooldown %.0fs remaining)",
                    AUTO_TRIGGER_COOLDOWN_SECONDS - since_last,
                )
                return None
            self._last_auto_trigger_ts = now
        try:
            job_id = self.start(
                nemo_mcp_url=nemo_mcp_url,
                lm_base_url=lm_base_url,
                lm_model=lm_model,
                repo_root=repo_root,
                lang_filter=lang_filter,
                memory_db=memory_db,
            )
        except RuntimeError as exc:
            # Race: another start landed between guard and start. Not a hard error.
            logger.debug("decision_agent: auto-trigger race: %s", exc)
            return None
        # Mark the new job so the UI can distinguish auto-fires from manual clicks.
        with self._lock:
            job = self._job
            if job is not None and job.job_id == job_id:
                job._emit({
                    "type": "auto_triggered",
                    "trigger": "handoff_failure",
                    "context": (failure_context or "")[:300],
                })
        logger.info(
            "decision_agent: auto-triggered job %s after failure (%s)",
            job_id, (failure_context or "")[:80],
        )
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
            run_json = job.run_json
            job.status = DecisionAgentStatus.COMPLETED  # atomic claim — prevents double-apply
        self._persist_current_job()
        result = self_mod_apply(run_json, approve_review=True, memory_db=memory_db)
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
        finally:
            # Final persist after status transition — _emit also persists, but on
            # exceptions raised between status mutation and the next emit, this is the
            # safety net that captures the final state.
            self._persist_current_job()
