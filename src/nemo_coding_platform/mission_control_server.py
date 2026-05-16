from __future__ import annotations

import ast
import base64
import json
import html
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from hashlib import sha256
from datetime import datetime, timezone
from dataclasses import dataclass, field
from enum import StrEnum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlparse
from uuid import uuid4

from nemo_coding_platform.core.context_assembler import assemble_context
from nemo_coding_platform.core.context_policy import ContextMode, get_profile
from nemo_coding_platform.core.evals import score_persisted_result, score_spec10_lite
from nemo_coding_platform.core.search_layer import resolve_layers, run_layered_search
from nemo_coding_platform.core.trace import TraceEventKind, TraceEventStatus, append_tool_call_traces, build_trace_event
from nemo_coding_platform.core.engine_interface import ENGINE_MESSAGE_FILE
from nemo_coding_platform.core.headless_handoff import HandoffRequest
from nemo_coding_platform.core.long_handoff_supervisor import LongHandoffBudget, execute_long_handoff_supervisor
from nemo_coding_platform.core.memory import MemoryAtomType, NEMO_TOOL_REGISTRY, NemoToolRisk
from nemo_coding_platform.core.memory_persistence import PersistentMemoryStore
from nemo_coding_platform.core.mission_control import build_mission_control_state
from nemo_coding_platform.core.model_config import MODEL_ROLES, default_model_role_profile
from nemo_coding_platform.core.nemo_adapter import McpNemoAdapter, PersistentNemoAdapter
from nemo_coding_platform.core.nemo_lifecycle import NemoLifecyclePhase, lifecycle_contract, tool_allowed_in_lifecycle
from nemo_coding_platform.core.persistence import load_headless_result_json, save_headless_result_json
from nemo_coding_platform.core.review_gate import MergeApplyResult, apply_merge_plan, build_merge_plan, rollback_apply_result
from nemo_coding_platform.core.rate_limiter import RateLimiter
from nemo_coding_platform.core.self_modification import get_self_mod_continuity, query_self_mod_risk_patterns, self_mod_impact, self_mod_similar_runs, self_mod_trajectory
from nemo_coding_platform.core.validation import validation_commands_for_policy
from nemo_coding_platform.core.vscode_mcp_config import VSCODE_STDIO_NEMO_URL, default_nemo_mcp_url, discover_vscode_mcp_server
from nemo_coding_platform.spacecode_mcp_tools import mcp_call_nemo_tool


class HandoffJobStatus(StrEnum):
    STARTING            = "starting"
    AWAITING_PERMISSION = "awaiting_permission"
    RUNNING             = "running"
    COMPLETED           = "completed"
    FAILED              = "failed"
    PERMISSION_DENIED   = "permission_denied"
    ORPHANED            = "orphaned"
    CANCELLED           = "cancelled"


NEMO_EVENT_PREFIX = "NEMO_EVENT:"
_TIMELINE_PERSIST_BATCH = 5

DEFAULT_APPLY_RESULTS = ".nemo-runtimes/mission-control/apply-results"
DEFAULT_RUN_RESULTS = ".nemo-runtimes/mission-control/runs"
DEFAULT_JOB_SNAPSHOTS = ".nemo-runtimes/mission-control/jobs"
JOB_LOG_LIMIT = 10_000
DECISION_LOG_LIMIT = 200
DEFAULT_CONTEXT_WINDOW_TOKENS = 131_072
DEFAULT_CHAT_MAX_TOKENS = 16_384
MAX_CHAT_MAX_TOKENS = 65_536
NEMO_TOOL_SCAN_TTL_SECONDS = 30
LEGACY_NEMO_SSE_URL = "http://127.0.0.1:8765/mcp/sse"
URL_SOURCE_CACHE_TTL_SECONDS = 300
SOURCE_ANALYTICS_MAX_EVENTS = 400
JOB_HEARTBEAT_SECONDS = 20.0
JOB_STALL_HEARTBEATS = 8
_NEMO_TOOL_SCAN_CACHE: dict[str, object] = {"at": 0.0, "verified_read_only": (), "declared_write_or_destructive": ()}
_NEMO_MCP_CAPABILITY_CACHE: dict[str, object] = {"at": 0.0, "key": None, "payload": None}
_URL_SOURCE_CACHE: dict[str, dict[str, object]] = {}
_DEFAULT_SESSION_NEMO_TOOLS: tuple[str, ...] = (
    "context_bootstrap",
    "prime_context",
    "build_context_portfolio",
    "get_context_portfolio_stats",
    "search_memories",
    "anticipate",
    "store_conversation",
    "cognitive_ingest",
)


@dataclass(slots=True)
class HandoffJob:
    job_id: str
    task_id: str
    run_id: str
    run_json: str
    status: str
    command: tuple[str, ...]
    payload: dict[str, object]
    logs: list[str]
    returncode: int | None = None
    process: subprocess.Popen[str] | None = None
    heartbeat_stop: threading.Event | None = None
    heartbeat_thread: threading.Thread | None = None
    run_thread: threading.Thread | None = None
    error: str | None = None
    last_runtime_signature: str = ""
    stagnant_heartbeats: int = 0
    permission_request: dict[str, object] | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    timeline: list[dict[str, object]] = field(default_factory=list)
    _tl_event_count: int = 0

    @classmethod
    def from_snapshot(cls, payload: dict[str, Any]) -> "HandoffJob":
        return cls(
            job_id=str(payload.get("job_id") or ""),
            task_id=str(payload.get("task_id") or ""),
            run_id=str(payload.get("run_id") or ""),
            run_json=str(payload.get("run_json") or ""),
            status=str(payload.get("status") or "orphaned"),
            command=tuple(str(item) for item in payload.get("command", []) if isinstance(item, str)),
            payload=dict(payload.get("payload") or {}),
            logs=[str(item) for item in payload.get("logs", []) if isinstance(item, str)],
            returncode=int(payload["returncode"]) if isinstance(payload.get("returncode"), int) else None,
            error=str(payload.get("error")) if isinstance(payload.get("error"), str) and payload.get("error") else None,
            last_runtime_signature=str(payload.get("last_runtime_signature") or ""),
            stagnant_heartbeats=int(payload.get("stagnant_heartbeats") or 0),
            permission_request=payload.get("permission_request") if isinstance(payload.get("permission_request"), dict) else None,
            created_at=str(payload.get("created_at") or datetime.now(timezone.utc).isoformat()),
            updated_at=str(payload.get("updated_at") or datetime.now(timezone.utc).isoformat()),
            timeline=list(payload.get("timeline") or []),
        )

    def to_dict(self, *, include_logs: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "job_id": self.job_id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "run_json": self.run_json,
            "status": self.status,
            "returncode": self.returncode,
            "error": self.error,
            "command": list(self.command),
            "objective": self.payload.get("objective"),
            "timeout_seconds": self.payload.get("timeout_seconds"),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_runtime_signature": self.last_runtime_signature,
            "stagnant_heartbeats": self.stagnant_heartbeats,
            "permission_request": self.permission_request,
            "timeline": list(self.timeline),
        }
        if include_logs:
            payload["logs"] = list(self.logs)
        return payload


class HandoffJobManager:
    def __init__(self, snapshot_root: Path | None = None) -> None:
        self._jobs: dict[str, HandoffJob] = {}
        self._lock = threading.Lock()
        self._snapshot_root = snapshot_root
        if self._snapshot_root is not None:
            self._snapshot_root.mkdir(parents=True, exist_ok=True)
            self._restore_snapshots()

    def configure_snapshot_root(self, snapshot_root: Path) -> None:
        self._snapshot_root = snapshot_root
        self._snapshot_root.mkdir(parents=True, exist_ok=True)
        self._restore_snapshots()

    def start(self, config: "MissionControlServerConfig", payload: dict[str, object]) -> HandoffJob:
        settings = _load_settings(config)
        merged_payload = _enforce_workspace_scope(config, {**settings, **payload})
        objective = _objective(merged_payload)
        provider = _provider_mode(merged_payload)
        timeout = _timeout_seconds(merged_payload)
        run_suffix = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        short_id = uuid4().hex[:8]
        job_id = f"job-{run_suffix}-{short_id}"
        task_id = f"mc-task-{run_suffix}-{short_id}"
        run_id = f"mc-run-{run_suffix}-{short_id}"
        config.run_results_path.mkdir(parents=True, exist_ok=True)
        run_json = config.run_results_path / f"{task_id}-{run_id}.json"

        # ── Permission Gate (Phase A: pre-run) ─────────────────────────────
        from nemo_coding_platform.core.permission_engine import (
            PermissionAnalyzer, PermissionMode, PermissionDecision as _PermDecision,
        )
        raw_autonomy = str(merged_payload.get("autonomy_mode") or "trusted")
        perm_mode = PermissionMode.FREEDOM if raw_autonomy == "aggressive" else PermissionMode.RESTRICTION
        target_files = tuple(
            str(f) for f in merged_payload.get("files", []) if isinstance(f, str)
        )
        perm_request = PermissionAnalyzer().analyze(job_id, objective, target_files, perm_mode)
        if perm_request.requires_user_approval:
            job = HandoffJob(
                job_id, task_id, run_id, str(run_json),
                "awaiting_permission", (),
                dict(merged_payload), ["awaiting permission approval"],
                permission_request=perm_request.to_dict(),
            )
            with self._lock:
                self._jobs[job_id] = job
            self._persist_job(job)
            return job
        auto_decision = _PermDecision(
            decided_at=datetime.now(timezone.utc).isoformat(),
            decided_by="policy_auto",
            approved=True,
            categories=perm_request.auto_approved,
            note="",
        )
        auto_perm_meta: dict[str, object] = {
            **perm_request.to_dict(),
            "decision": auto_decision.to_dict(),
        }
        # ── end Permission Gate ─────────────────────────────────────────────

        command = self._build_command(config, merged_payload, objective, provider, timeout, task_id, run_id, run_json)
        job = HandoffJob(
            job_id, task_id, run_id, str(run_json), "starting", command, dict(merged_payload),
            ["starting handoff job"],
            permission_request=auto_perm_meta,
        )
        with self._lock:
            self._jobs[job_id] = job
        self._persist_job(job)
        run_thread = threading.Thread(target=self._run_job, args=(config, job), daemon=True)
        job.run_thread = run_thread
        run_thread.start()
        return job

    def start_self_modify(self, config: "MissionControlServerConfig", payload: dict[str, object]) -> HandoffJob:
        settings = _load_settings(config)
        merged_payload = _enforce_workspace_scope(config, {**settings, **payload})
        objective = _objective(merged_payload)
        provider = _provider_mode({**merged_payload, "provider": payload.get("provider") or "subprocess"})
        timeout = _timeout_seconds(merged_payload)
        run_suffix = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        short_id = uuid4().hex[:8]
        job_id = f"self-job-{run_suffix}-{short_id}"
        task_id = f"self-task-{run_suffix}-{short_id}"
        run_id = f"self-run-{run_suffix}-{short_id}"
        run_json = config.runtimes_path / "self-mod" / "runs" / f"{_safe_stem(task_id)}-{_safe_stem(run_id)}.json"
        command = self._build_self_modify_command(config, merged_payload, objective, provider, timeout, task_id, run_id)
        job = HandoffJob(job_id, task_id, run_id, str(run_json), "starting", command, dict(merged_payload), ["starting self-modification job"])
        with self._lock:
            self._jobs[job_id] = job
        self._persist_job(job)
        run_thread = threading.Thread(target=self._run_job, args=(config, job), daemon=True)
        job.run_thread = run_thread
        run_thread.start()
        return job

    def grant_permission(
        self, config: "MissionControlServerConfig", job_id: str, note: str = ""
    ) -> "HandoffJob":
        from datetime import timezone as _tz
        from nemo_coding_platform.core.permission_engine import PermissionDecision, PermissionCategory

        job = self.get(job_id)
        if job.status != "awaiting_permission":
            raise ApiRequestError(
                f"job {job_id} is not awaiting_permission (status={job.status})",
                error_code="invalid_job_status",
            )
        req_dict = job.permission_request or {}
        all_cats = tuple(
            PermissionCategory(v)
            for v in (req_dict.get("categories") or [])
            if isinstance(v, str)
        )
        decision = PermissionDecision(
            decided_at=datetime.now(_tz.utc).isoformat(),
            decided_by="user",
            approved=True,
            categories=all_cats,
            note=note,
        )
        updated_request = {**req_dict, "decision": decision.to_dict()}
        with self._lock:
            job.status = "starting"
            job.permission_request = updated_request
        self._persist_job(job)
        self._append_log(job, f"permission granted: {[c.value for c in all_cats]}")
        settings = _load_settings(config)
        merged_payload = _enforce_workspace_scope(config, {**settings, **job.payload})
        objective = _objective(merged_payload)
        provider = _provider_mode(merged_payload)
        timeout = _timeout_seconds(merged_payload)
        run_json = Path(job.run_json)
        command = self._build_command(config, merged_payload, objective, provider, timeout, job.task_id, job.run_id, run_json)
        with self._lock:
            job.command = command
        run_thread = threading.Thread(target=self._run_job, args=(config, job), daemon=True)
        job.run_thread = run_thread
        run_thread.start()
        return job

    def deny_permission(self, job_id: str, note: str = "") -> "HandoffJob":
        from datetime import timezone as _tz
        from nemo_coding_platform.core.permission_engine import PermissionDecision, PermissionCategory

        job = self.get(job_id)
        if job.status != "awaiting_permission":
            raise ApiRequestError(
                f"job {job_id} is not awaiting_permission (status={job.status})",
                error_code="invalid_job_status",
            )
        req_dict = job.permission_request or {}
        all_cats = tuple(
            PermissionCategory(v)
            for v in (req_dict.get("categories") or [])
            if isinstance(v, str)
        )
        decision = PermissionDecision(
            decided_at=datetime.now(_tz.utc).isoformat(),
            decided_by="user",
            approved=False,
            categories=all_cats,
            note=note,
        )
        updated_request = {**req_dict, "decision": decision.to_dict()}
        with self._lock:
            job.status = "permission_denied"
            job.permission_request = updated_request
        self._persist_job(job)
        self._append_log(job, f"permission denied: {note or '(no note)'}")
        return job

    def list(self) -> list[dict[str, object]]:
        with self._lock:
            return [job.to_dict(include_logs=False) for job in sorted(self._jobs.values(), key=lambda item: item.job_id, reverse=True)]

    def get(self, job_id: str) -> HandoffJob:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise FileNotFoundError(job_id)
        return job

    def cancel(self, job_id: str) -> HandoffJob:
        return self._stop(job_id, "cancelled", "cancel requested")

    def pause(self, job_id: str) -> HandoffJob:
        return self._stop(job_id, "paused", "pause requested")

    def resume(self, config: "MissionControlServerConfig", job_id: str) -> HandoffJob:
        source = self.get(job_id)
        if source.status in {"starting", "running", "completed", "failed", "cancelled"}:
            self._append_log(source, f"resume ignored status={source.status}")
            return source
        if source.status != "paused":
            self._append_log(source, f"resume ignored status={source.status}")
            return source
        payload = dict(source.payload)
        resumed = self.start(config, payload)
        self._append_log(resumed, f"resumed from {job_id}")
        return resumed

    def _stop(self, job_id: str, stopped_status: str, log_line: str) -> HandoffJob:
        job = self.get(job_id)
        if job.status in {"completed", "failed", "cancelled"}:
            self._append_log(job, f"{log_line} ignored status={job.status}")
            return job
        self._append_log(job, log_line)
        if job.heartbeat_stop is not None:
            job.heartbeat_stop.set()
        if job.process and job.process.poll() is None:
            self._set_status(job, stopped_status)
            job.process.terminate()
            try:
                job.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                job.process.kill()
                self._append_log(job, "process did not stop after terminate; sent kill")
        elif job.status not in {"completed", "failed", "cancelled"}:
            self._set_status(job, stopped_status)
        self._join_heartbeat(job)
        self._join_run_thread(job)
        return job

    def _join_run_thread(self, job: HandoffJob) -> None:
        run_thread = job.run_thread
        if run_thread is None:
            return
        if run_thread.is_alive() and run_thread is not threading.current_thread():
            run_thread.join(timeout=6)
        if not run_thread.is_alive():
            job.run_thread = None

    def find_orphans(self, *, grace_seconds: int = 120) -> list[dict[str, object]]:
        now_ts = time.time()
        orphans: list[dict[str, object]] = []
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            if job.status not in {"starting", "running"}:
                continue
            updated = _parse_iso_datetime(job.updated_at)
            age_seconds = int(now_ts - updated.timestamp()) if updated else None
            process_alive = bool(job.process and job.process.poll() is None)
            if process_alive:
                continue
            if age_seconds is not None and age_seconds < grace_seconds:
                continue
            orphans.append(
                {
                    "kind": "memory_job",
                    "job_id": job.job_id,
                    "status": job.status,
                    "run_json": job.run_json,
                    "age_seconds": age_seconds,
                    "reason": "job_process_not_alive",
                }
            )
        return orphans

    def mark_orphaned(self, job_id: str, *, reason: str = "orphan detected") -> HandoffJob:
        job = self.get(job_id)
        if job.status in {"completed", "failed", "cancelled"}:
            return job
        self._set_status(job, "orphaned")
        self._append_log(job, reason)
        return job

    def _append_log(self, job: HandoffJob, line: str) -> None:
        job.logs.append(line)
        job.updated_at = datetime.now(timezone.utc).isoformat()
        if len(job.logs) > JOB_LOG_LIMIT:
            del job.logs[:-JOB_LOG_LIMIT]
        self._persist_job(job)

    def _set_status(self, job: HandoffJob, status: str) -> None:
        job.status = status
        job.updated_at = datetime.now(timezone.utc).isoformat()
        self._persist_job(job)

    def _snapshot_path(self, job_id: str) -> Path | None:
        if self._snapshot_root is None:
            return None
        return self._snapshot_root / f"{_safe_stem(job_id)}.json"

    def _persist_job(self, job: HandoffJob) -> None:
        target = self._snapshot_path(job.job_id)
        if target is None:
            return
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        snapshot = {
            **job.to_dict(include_logs=True),
            "payload": dict(job.payload),
            "schema_version": 1,
        }
        temp = target.with_suffix(".tmp")
        try:
            temp.write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")
            temp.replace(target)
        except FileNotFoundError:
            # Temp directories can disappear during test teardown while background
            # threads are still settling; treat persistence as best-effort.
            return
        except OSError:
            return

    def _restore_snapshots(self) -> None:
        if self._snapshot_root is None or not self._snapshot_root.exists():
            return
        with self._lock:
            if self._jobs:
                return
            for snapshot in sorted(self._snapshot_root.glob("*.json")):
                try:
                    payload = json.loads(snapshot.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                if not isinstance(payload, dict):
                    continue
                job = HandoffJob.from_snapshot(payload)
                if not job.job_id:
                    continue
                self._reconcile_restored_job(job)
                self._jobs[job.job_id] = job

    def _reconcile_restored_job(self, job: HandoffJob) -> None:
        if job.status not in {"starting", "running"}:
            return
        run_path = Path(job.run_json)
        if run_path.exists():
            job.status = "completed"
            if job.returncode is None:
                job.returncode = 0
            if not any("restored snapshot detected completed run_json" in line for line in job.logs):
                job.logs.append("restored snapshot detected completed run_json")
        else:
            job.status = "orphaned"
            if not any("restored snapshot missing run_json" in line for line in job.logs):
                job.logs.append("restored snapshot missing run_json")
        job.updated_at = datetime.now(timezone.utc).isoformat()

    def _heartbeat_tick(self, config: "MissionControlServerConfig", job: HandoffJob) -> None:
        self._append_log(job, self._heartbeat_snapshot(config, job))
        stalled_reason = self._stall_reason(config, job)
        if stalled_reason and job.process and job.process.poll() is None:
            self._append_log(job, stalled_reason)
            job.error = stalled_reason
            job.process.kill()
            self._set_status(job, "failed")

    def _stall_reason(self, config: "MissionControlServerConfig", job: HandoffJob) -> str | None:
        runtime_path = self._runtime_path(config, job)
        run_path = Path(job.run_json)
        if runtime_path is None or not runtime_path.exists() or run_path.exists():
            job.last_runtime_signature = ""
            job.stagnant_heartbeats = 0
            return None

        top_level = [path for path in sorted(runtime_path.iterdir(), key=lambda item: item.name) if path.is_file()]
        signature = "|".join(f"{path.name}:{path.stat().st_size}" for path in top_level)
        if signature and signature == job.last_runtime_signature:
            job.stagnant_heartbeats += 1
        else:
            job.last_runtime_signature = signature
            job.stagnant_heartbeats = 0
        self._persist_job(job)

        if job.stagnant_heartbeats < JOB_STALL_HEARTBEATS:
            return None
        return (
            "stall detected: runtime artifacts unchanged for "
            f"{job.stagnant_heartbeats} heartbeats (~{int(job.stagnant_heartbeats * JOB_HEARTBEAT_SECONDS)}s); "
            "terminating job to fail closed"
        )

    def _start_heartbeat(self, config: "MissionControlServerConfig", job: HandoffJob) -> None:
        stop_event = threading.Event()
        job.heartbeat_stop = stop_event

        def _heartbeat_loop() -> None:
            while job.process and job.process.poll() is None:
                if stop_event.wait(JOB_HEARTBEAT_SECONDS):
                    break
                if not job.process or job.process.poll() is not None:
                    break
                self._heartbeat_tick(config, job)

        heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
        job.heartbeat_thread = heartbeat_thread
        heartbeat_thread.start()

    def _join_heartbeat(self, job: HandoffJob) -> None:
        heartbeat_thread = job.heartbeat_thread
        if heartbeat_thread is not None and heartbeat_thread.is_alive():
            heartbeat_thread.join(timeout=1)
        job.heartbeat_thread = None
        job.heartbeat_stop = None

    def _runtime_path(self, config: "MissionControlServerConfig", job: HandoffJob) -> Path | None:
        if not job.task_id or not job.run_id:
            return None
        return config.runtimes_path / f"{_safe_stem(job.task_id)}-{_safe_stem(job.run_id)}"

    def _heartbeat_snapshot(self, config: "MissionControlServerConfig", job: HandoffJob) -> str:
        run_path = Path(job.run_json)
        run_exists = run_path.exists()
        run_size = run_path.stat().st_size if run_exists else 0
        runtime_path = self._runtime_path(config, job)
        if runtime_path is None or not runtime_path.exists():
            return f"heartbeat process_alive=1 run_json_exists={int(run_exists)} run_json_size={run_size} runtime_exists=0"

        top_level_files = sorted(path.name for path in runtime_path.iterdir() if path.is_file())
        engine_message = runtime_path / ENGINE_MESSAGE_FILE
        checkpoint_count = sum(1 for path in runtime_path.glob("checkpoint-*.json") if path.is_file())
        interesting_files = ",".join(top_level_files[:4]) if top_level_files else "-"
        return (
            "heartbeat "
            f"process_alive=1 run_json_exists={int(run_exists)} run_json_size={run_size} "
            f"runtime_exists=1 runtime_files={len(top_level_files)} "
            f"engine_message_exists={int(engine_message.exists())} checkpoints={checkpoint_count} "
            f"runtime_sample={interesting_files}"
        )

    def _build_command(
        self,
        config: "MissionControlServerConfig",
        payload: dict[str, object],
        objective: str,
        provider: str,
        timeout: float,
        task_id: str,
        run_id: str,
        run_json: Path,
    ) -> tuple[str, ...]:
        command: list[str] = [
            sys.executable,
            "-u",
            "-m",
            "nemo_coding_platform",
            "long-handoff-run",
            objective,
            "--repo",
            str(config.repo_path),
            "--provider",
            provider,
            "--timeout",
            str(timeout),
            "--max-runtime-minutes",
            str(payload.get("max_runtime_minutes") or 120),
            "--heartbeat-minutes",
            str(payload.get("heartbeat_minutes") or 15),
            "--max-heartbeats",
            str(payload.get("max_heartbeats") or 4),
            "--token-budget",
            str(payload.get("token_budget") or 128000),
            "--plan-minutes",
            str(payload.get("plan_minutes") or 30),
            "--execute-minutes",
            str(payload.get("execute_minutes") or 60),
            "--review-minutes",
            str(payload.get("review_minutes") or 30),
            "--validation-policy",
            str(payload.get("validation_policy") or "smoke"),
            "--repair-time-limit-seconds",
            str(payload.get("repair_time_limit_seconds") or 3600),
            "--validation-time-budget-seconds",
            str(payload.get("validation_time_budget_seconds") or 1800),
            "--save-json",
            str(run_json),
            "--task-id",
            task_id,
            "--run-id",
            run_id,
            "--json",
        ]
        for item in _string_list(payload, "acceptance_criteria", ("implementation satisfies the objective",)):
            command.extend(("--acceptance", item))
        for item in _string_list(payload, "validation_commands", ()):
            command.extend(("--validation", item))
        for item in _string_list(payload, "target_files", ()):
            command.extend(("--target-file", item))
        prd_text = _handoff_prd_text(payload)
        if prd_text:
            command.extend(("--prd-text", prd_text))
        command.extend(("--spec-mode", _handoff_spec_mode(payload)))
        if config.memory_db is None:
            command.append("--no-memory-db")
        else:
            command.extend(("--memory-db", str(config.memory_db)))
            mcp_url = str(payload.get("nemo_mcp_url") or "").strip()
            if mcp_url:
                # stdio://vscode/nemo only works inside VS Code — child subprocesses need HTTP SSE
                if mcp_url.lower() == VSCODE_STDIO_NEMO_URL:
                    mcp_url = LEGACY_NEMO_SSE_URL
                command.extend(("--mcp-url", mcp_url))
                command.extend(("--mcp-prefix", str(payload.get("nemo_mcp_prefix") or "nemo.")))
        base_url_run = str(payload.get("base_url") or "http://127.0.0.1:1234/v1")
        model_run = str(payload.get("model") or "").strip() or _resolve_lmstudio_model(base_url_run)
        command.extend(("--model-profile", model_run))
        command.extend(("--lmstudio-base-url", base_url_run))
        if payload.get("pause_after_minutes") is not None:
            command.extend(("--pause-after-minutes", str(payload.get("pause_after_minutes"))))
        if bool(payload.get("validation_escalation_mode")):
            command.append("--validation-escalation-mode")
        if bool(payload.get("real_validation", True)):
            command.append("--real-validation")
        if bool(payload.get("use_git_worktree")):
            command.append("--use-git-worktree")
        return tuple(command)

    def _build_self_modify_command(
        self,
        config: "MissionControlServerConfig",
        payload: dict[str, object],
        objective: str,
        provider: str,
        timeout: float,
        task_id: str,
        run_id: str,
    ) -> tuple[str, ...]:
        command: list[str] = [
            sys.executable,
            "-u",
            "-m",
            "nemo_coding_platform",
            "self-modify",
            objective,
            "--repo",
            str(config.repo_path),
            "--provider",
            provider,
            "--timeout",
            str(timeout),
            "--validation-policy",
            str(payload.get("validation_policy") or "targeted"),
            "--task-id",
            task_id,
            "--run-id",
            run_id,
            "--json",
        ]
        command.extend(("--type", str(payload.get("task_type") or "tool_expansion")))
        base_url_job = str(payload.get("base_url") or payload.get("model_base_url") or "http://127.0.0.1:1234/v1")
        model_job = str(payload.get("model") or payload.get("default_model") or "").strip() or _resolve_lmstudio_model(base_url_job)
        command.extend(("--model-profile", model_job))
        command.extend(("--lmstudio-base-url", base_url_job))
        for item in _string_list(payload, "validation_commands", ()): 
            command.extend(("--validation", item))
        for item in _string_list(payload, "target_files", ()): 
            command.extend(("--target-file", item))
        if bool(payload.get("real_validation")):
            command.append("--real-validation")
        if bool(payload.get("bounded_simulation")):
            command.append("--bounded-simulation")
        if config.memory_db is not None:
            command.extend(("--memory-db", str(config.memory_db)))
        return tuple(command)

    def _run_job(self, config: "MissionControlServerConfig", job: HandoffJob) -> None:
        source_root = Path(__file__).resolve().parents[1]
        existing_pythonpath = os.environ.get("PYTHONPATH", "")
        pythonpath = str(source_root) if not existing_pythonpath else f"{source_root};{existing_pythonpath}"
        env = {**os.environ, "PYTHONPATH": pythonpath, "PYTHONUNBUFFERED": "1"}
        _nemo_settings = _load_settings(config) if config.memory_db else {}
        _nemo_url = str(_nemo_settings.get("nemo_mcp_url") or "")
        _nemo_db = str(config.memory_db) if config.memory_db else ""
        if _nemo_db and _nemo_url:
            try:
                mcp_call_nemo_tool(
                    "context_bootstrap",
                    lifecycle_phase="start",
                    memory_db=_nemo_db,
                    mcp_url=_nemo_url,
                    task=str(job.payload.get("objective") or "handoff job")[:200],
                    topic="Space Code Handoff",
                    token_budget=400,
                    compact=True,
                )
            except Exception:
                pass
        try:
            job.process = subprocess.Popen(
                job.command,
                cwd=config.repo_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )
            self._set_status(job, "running")
            self._append_log(job, f"process started pid={job.process.pid}")
            self._append_log(job, f"tracking run_json={job.run_json}")
            self._start_heartbeat(config, job)
            if job.process.stdout:
                _stdout = job.process.stdout
                _lock = self._lock

                def _pipe_reader() -> None:
                    try:
                        for raw_line in _stdout:
                            line = raw_line.rstrip("\r\n")
                            if line.startswith(NEMO_EVENT_PREFIX):
                                try:
                                    event = json.loads(line[len(NEMO_EVENT_PREFIX):])
                                    with _lock:
                                        job.timeline.append(event)
                                        job.updated_at = datetime.now(timezone.utc).isoformat()
                                        job._tl_event_count = getattr(job, "_tl_event_count", 0) + 1
                                        if job._tl_event_count % _TIMELINE_PERSIST_BATCH == 0:
                                            self._persist_job(job)
                                except (json.JSONDecodeError, ValueError):
                                    pass
                            else:
                                self._append_log(job, line)
                    finally:
                        try:
                            _stdout.close()
                        except Exception:
                            pass

                _reader = threading.Thread(target=_pipe_reader, daemon=True, name=f"pipe-{job.job_id}")
                _reader.start()
                job.returncode = job.process.wait()
                # Force-close stdout so _pipe_reader unblocks if another
                # process is holding the write end of the pipe open.
                try:
                    _stdout.close()
                except Exception:
                    pass
                _reader.join(timeout=10.0)
            else:
                job.returncode = job.process.wait()
            if job.heartbeat_stop is not None:
                job.heartbeat_stop.set()
            self._join_heartbeat(job)
            if job.status in {"cancelled", "paused"}:
                self._append_log(job, f"job {job.status}")
                return
            self._set_status(job, "completed" if job.returncode == 0 else "failed")
            self._append_log(job, f"job finished returncode={job.returncode}")
        except OSError as error:
            if job.heartbeat_stop is not None:
                job.heartbeat_stop.set()
            self._join_heartbeat(job)
            self._set_status(job, "failed")
            job.error = str(error)
            self._append_log(job, str(error))
        finally:
            if _nemo_db and _nemo_url:
                try:
                    mcp_call_nemo_tool(
                        "store_conversation",
                        lifecycle_phase="close",
                        memory_db=_nemo_db,
                        mcp_url=_nemo_url,
                        content=f"Handoff job {job.job_id}: objective={str(job.payload.get('objective') or '')[:100]}, status={job.status}, returncode={job.returncode}",
                        role="assistant",
                        session_id=job.job_id,
                    )
                except Exception:
                    pass
            if job.run_thread is threading.current_thread():
                job.run_thread = None


class ApiRequestError(ValueError):
    def __init__(self, message: str, *, error_code: str = "invalid_request", status_code: int = 400) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code


def _bad_request(message: str, *, error_code: str = "invalid_request", status_code: int = 400) -> ApiRequestError:
    return ApiRequestError(message, error_code=error_code, status_code=status_code)


@dataclass(frozen=True, slots=True)
class MissionControlServerConfig:
    repo_path: Path
    runtimes_path: Path
    apply_results_path: Path
    run_results_path: Path
    memory_db: Path | None

    @property
    def job_snapshots_path(self) -> Path:
        return _resolve_under_repo(self.repo_path, DEFAULT_JOB_SNAPSHOTS)

    @classmethod
    def from_paths(
        cls,
        repo: str | Path = ".",
        runtimes: str | Path = ".nemo-runtimes",
        apply_results: str | Path = DEFAULT_APPLY_RESULTS,
        run_results: str | Path = DEFAULT_RUN_RESULTS,
        memory_db: str | Path | None = ".nemo-runtimes/nemo-memory.sqlite",
    ) -> "MissionControlServerConfig":
        repo_path = Path(repo).resolve()
        runtimes_path = _resolve_under_repo(repo_path, runtimes)
        apply_results_path = _resolve_under_repo(repo_path, apply_results)
        run_results_path = _resolve_under_repo(repo_path, run_results)
        resolved_memory_db = _resolve_under_repo(repo_path, memory_db) if memory_db else None
        return cls(repo_path, runtimes_path, apply_results_path, run_results_path, resolved_memory_db)

    def with_runtime_settings(self, settings: dict[str, object]) -> "MissionControlServerConfig":
        repo = Path(str(settings.get("repo_path") or self.repo_path)).resolve()
        runtimes = Path(str(settings.get("runtime_path") or self.runtimes_path))
        if not runtimes.is_absolute():
            runtimes = repo / runtimes
        memory_value = settings.get("memory_db")
        memory_db = _resolve_under_repo(repo, memory_value) if isinstance(memory_value, str) and memory_value else self.memory_db
        return MissionControlServerConfig(repo, runtimes, runtimes / "mission-control" / "apply-results", runtimes / "mission-control" / "runs", memory_db)


def _resolve_under_repo(repo: Path, path: str | Path | None) -> Path:
    candidate = Path(path or "")
    if candidate.is_absolute():
        return candidate
    return repo / candidate


def _settings_path(config: MissionControlServerConfig) -> Path:
    return config.runtimes_path / "mission-control" / "settings.json"


def _default_settings(config: MissionControlServerConfig) -> dict[str, object]:
    role_models = default_model_role_profile("")
    return {
        "repo_path": str(config.repo_path),
        "model_base_url": "http://127.0.0.1:1234/v1",
        "default_model": "",
        "model_roles": {
            "planner": role_models.planner,
            "editor": role_models.editor,
            "reviewer": role_models.reviewer,
            "summarizer": role_models.summarizer,
        },
        "provider": "subprocess",
        "memory_db": str(config.memory_db) if config.memory_db else "",
        "nemo_mcp_url": default_nemo_mcp_url(config.repo_path),
        "runtime_path": str(config.runtimes_path),
        "timeout_seconds": 300,
        "max_runtime_minutes": 240,
        "heartbeat_minutes": 30,
        "max_heartbeats": 8,
        "token_budget": 128000,
        "context_window_tokens": DEFAULT_CONTEXT_WINDOW_TOKENS,
        "chat_max_tokens": DEFAULT_CHAT_MAX_TOKENS,
        "image_gen_backend": "auto",
        "image_gen_url": "",
        "pause_after_minutes": None,
        "plan_minutes": 60,
        "execute_minutes": 120,
        "review_minutes": 60,
        "repair_time_limit_seconds": 7200,
        "validation_time_budget_seconds": 3600,
        "validation_escalation_mode": False,
        "real_validation": True,
        "validation_policy": "smoke",
        "nemo_required": config.memory_db is not None,
        "quality_core": "product/nemo_code_runtime",
        "recent_repos": [str(config.repo_path)],
        "browser_homepage": "https://github.com",
        "browser_last_url": "",
        "browser_history": [],
        "browser_search_query": "",
        "browser_search_history": [],
        "source_analytics": [],
        "chat_metrics": [],
        "extensions": [
            {"name": "git", "enabled": True, "version": "builtin"},
            {"name": "terminal", "enabled": True, "version": "builtin"},
            {"name": "browser", "enabled": True, "version": "builtin"},
            {"name": "nemo-memory", "enabled": True, "version": "builtin"},
        ],
    }


def _load_settings(config: MissionControlServerConfig) -> dict[str, object]:
    settings = _default_settings(config)
    path = _settings_path(config)
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                settings.update(loaded)
        except (json.JSONDecodeError, OSError):
            pass
    settings["repo_path"] = str(config.repo_path)
    settings["runtime_path"] = str(config.runtimes_path)
    settings["memory_db"] = str(config.memory_db) if config.memory_db else ""
    raw_mcp_url = settings.get("nemo_mcp_url")
    settings["nemo_mcp_url"] = _normalize_nemo_mcp_url(config, raw_mcp_url)
    recent = settings.get("recent_repos")
    if not isinstance(recent, list):
        recent = []
    settings["recent_repos"] = _recent_repos(tuple(str(item) for item in recent), str(config.repo_path))
    provider = str(settings.get("provider") or "subprocess")
    if provider not in {"subprocess", "fake"}:
        settings["provider"] = "subprocess"
    model_roles = settings.get("model_roles")
    if not isinstance(model_roles, dict):
        model_roles = {}
    fallback_model = str(settings.get("default_model") or "")
    normalized_roles: dict[str, str] = {}
    for role in MODEL_ROLES:
        raw_value = model_roles.get(role)
        if isinstance(raw_value, str) and raw_value.strip():
            normalized_roles[role] = raw_value.strip()
        else:
            normalized_roles[role] = fallback_model
    settings["model_roles"] = normalized_roles
    if settings.get("validation_policy") not in {"none", "smoke", "targeted", "full"}:
        settings["validation_policy"] = "smoke"
    return settings


def _normalize_nemo_mcp_url(config: MissionControlServerConfig, value: object) -> str:
    url = str(value).strip() if isinstance(value, str) else ""
    if (
        not os.environ.get("SPACE_CODE_NEMO_MCP_URL")
        and (not url or url == LEGACY_NEMO_SSE_URL)
        and discover_vscode_mcp_server("nemo", config.repo_path) is not None
    ):
        return VSCODE_STDIO_NEMO_URL
    return url


def _validate_model_roles_payload(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise _bad_request("model_roles must be an object", error_code="invalid_setting_value")
    normalized: dict[str, str] = {}
    for role in MODEL_ROLES:
        value = raw.get(role)
        if not isinstance(value, str) or not value.strip():
            raise _bad_request(f"model_roles.{role} must be a non-empty string", error_code="invalid_setting_value")
        normalized[role] = value.strip()
    unknown = [str(key) for key in raw.keys() if str(key) not in MODEL_ROLES]
    if unknown:
        raise _bad_request(f"unknown model_roles key(s): {', '.join(sorted(unknown))}", error_code="invalid_setting_value")
    return normalized


def _validate_provider_timeout(provider: str, timeout_seconds: float, *, error_code: str) -> None:
    limits = {
        "subprocess": (5.0, 1800.0),
        "fake": (1.0, 300.0),
    }
    minimum, maximum = limits.get(provider, (1.0, 600.0))
    if timeout_seconds < minimum or timeout_seconds > maximum:
        raise _bad_request(
            f"timeout_seconds must be between {int(minimum)} and {int(maximum)} for provider={provider}",
            error_code=error_code,
        )


def _save_settings(config: MissionControlServerConfig, settings: dict[str, object]) -> None:
    path = _settings_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2, sort_keys=True), encoding="utf-8")


def _recent_repos(existing: tuple[str, ...], repo: str) -> list[str]:
    seen: list[str] = []
    for item in (repo, *existing):
        if item and item not in seen:
            seen.append(item)
    return seen[:12]


def _is_git_repo(path: Path) -> bool:
    return (path / ".git").exists()


def _normalized_extensions(raw: object) -> list[dict[str, object]]:
    entries = raw if isinstance(raw, list) else []
    result: list[dict[str, object]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        result.append(
            {
                "name": name.strip().lower(),
                "enabled": bool(item.get("enabled", True)),
                "version": str(item.get("version") or "custom"),
            }
        )
    seen: set[str] = set()
    deduped: list[dict[str, object]] = []
    for item in result:
        key = str(item["name"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _trim_output(text: str, limit: int = 20_000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...truncated..."


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, object]) -> None:
    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    try:
        handler.send_response(status)
        handler.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:5173")
        handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        handler.send_header("Access-Control-Allow-Headers", "Content-Type")
        
        # Cache-Control headers
        if handler.path.startswith("/assets"):
            handler.send_header("Cache-Control", "public, max-age=2592000, immutable")
        else:
            handler.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
            handler.send_header("Pragma", "no-cache")
            handler.send_header("Expires", "0")

        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
    except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
        return


def _load_body(handler: BaseHTTPRequestHandler) -> dict[str, object]:
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    if length > 1_000_000:
        raise _bad_request("request body too large", error_code="request_body_too_large", status_code=413)
    payload = json.loads(handler.rfile.read(length).decode("utf-8"))
    if not isinstance(payload, dict):
        raise _bad_request("request body must be a JSON object", error_code="request_body_must_be_object")
    return payload


def _source_json(payload: dict[str, object]) -> str:
    source_json = payload.get("source_json")
    if not isinstance(source_json, str) or not source_json:
        raise _bad_request("source_json is required", error_code="missing_source_json")
    return source_json


def _decision_log_entry(payload: dict[str, object]) -> dict[str, object]:
    action = payload.get("action")
    status = payload.get("status")
    detail = payload.get("detail")
    if not isinstance(action, str) or not action.strip():
        raise _bad_request("action is required", error_code="missing_action")
    if not isinstance(status, str) or status not in {"planned", "success", "failed"}:
        raise _bad_request("status must be planned/success/failed", error_code="invalid_status")
    if not isinstance(detail, str):
        detail = ""
    ts = payload.get("ts")
    if not isinstance(ts, int):
        ts = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    return {
        "id": f"dec-{uuid4().hex[:10]}",
        "ts": ts,
        "action": action.strip(),
        "status": status,
        "detail": detail.strip(),
    }


def _decision_log_path(source_json: str) -> Path:
    selected = load_headless_result_json(source_json)
    run = selected.get("run", {}) if isinstance(selected, dict) else {}
    sandbox_path = run.get("sandbox_path") if isinstance(run, dict) else None
    if not isinstance(sandbox_path, str) or not sandbox_path:
        raise _bad_request("sandbox_path is missing for selected run", error_code="missing_sandbox_path")
    return Path(sandbox_path) / "decision-log.json"


def _apply_json(payload: dict[str, object]) -> str:
    apply_json = payload.get("apply_json")
    if not isinstance(apply_json, str) or not apply_json:
        raise _bad_request("apply_json is required", error_code="missing_apply_json")
    return apply_json


def _job_id(payload: dict[str, object]) -> str:
    job_id = payload.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        raise _bad_request("job_id is required", error_code="missing_job_id")
    return job_id


def _message(payload: dict[str, object]) -> str:
    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        raise _bad_request("message is required", error_code="missing_message")
    return message.strip()


def _route_chat_mode(message: str, source_json: object) -> str:
    text = message.lower()
    execution_terms = (
        "apply", "aplicar", "rollback", "merge", "diff", "patch", "run", "runs", "review", "revisa", "revisar",
        "hunk", "sandbox", "fix", "arregla", "corrige", "commit",
    )
    research_terms = (
        "investiga", "investigar", "research", "analyze", "analiza", "analizar", "buscar", "busca", "web", "fuentes", "sources",
        "compare", "comparar", "benchmark", "deep",
    )
    if isinstance(source_json, str) and source_json.strip():
        return "execution"
    if any(term in text for term in execution_terms):
        return "execution"
    if any(term in text for term in research_terms):
        return "research"
    return "chat"


def _chat_mode(payload: dict[str, object], message: str, source_json: object) -> str:
    value = payload.get("chat_mode")
    if value is None:
        return _route_chat_mode(message, source_json)
    if not isinstance(value, str) or not value.strip():
        raise _bad_request("chat_mode must be chat, research, deep-research, or execution", error_code="invalid_chat_mode")
    mode = value.strip().lower()
    if mode == "deep-research":
        return "research"
    if mode not in {"chat", "research", "execution", "plan"}:
        raise _bad_request("chat_mode must be chat, research, deep-research, execution, or plan", error_code="invalid_chat_mode")
    return mode


_MODE_CONTEXT_WINDOWS: dict[str, int] = {
    "chat": 10_000,
    "research": 32_768,
    "execution": 65_536,
    "plan": 16_384,
}

def _chat_mode_profile(mode: str) -> dict[str, int]:
    p = get_profile(ContextMode(mode) if mode in {m.value for m in ContextMode} else ContextMode.CHAT)
    return {
        "portfolio_budget": p.portfolio_budget,
        "search_limit": p.search_limit,
        "anticipate_limit": p.anticipate_limit,
        "context_chars": p.context_chars,
        "context_window_tokens": _MODE_CONTEXT_WINDOWS.get(mode, DEFAULT_CONTEXT_WINDOW_TOKENS),
    }


def _selected_nemo_tools(payload: dict[str, object]) -> set[str]:
    raw = payload.get("selected_nemo_tools")
    if not isinstance(raw, list):
        return set(_DEFAULT_SESSION_NEMO_TOOLS)
    selected = {
        str(item).strip()
        for item in raw
        if isinstance(item, str) and str(item).strip()
    }
    return selected or set(_DEFAULT_SESSION_NEMO_TOOLS)


def _extract_http_urls(text: str, *, limit: int = 3) -> list[str]:
    pattern = re.compile(r"https?://[^\s)\]>\"']+", re.IGNORECASE)
    found = [item.rstrip(".,;:!?") for item in pattern.findall(text)]
    unique: list[str] = []
    seen: set[str] = set()
    for item in found:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
        if len(unique) >= limit:
            break
    return unique


def _strip_html_text(html: str) -> str:
    cleaned = re.sub(r"<script\b[^>]*>.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<style\b[^>]*>.*?</style>", " ", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _read_url_source(url: str, *, timeout_seconds: int = 8, max_chars: int = 3000) -> dict[str, str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("invalid URL")

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "NEMO-Mission-Control/1.0 (+local)"
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        content_type = str(response.headers.get("Content-Type") or "").lower()
        raw = response.read(max(1024, max_chars * 3))

    text = raw.decode("utf-8", errors="replace")
    if "html" in content_type or "<html" in text.lower():
        title_match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.IGNORECASE | re.DOTALL)
        title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else (parsed.netloc or url)
        body = _strip_html_text(text)
    else:
        title = parsed.netloc or url
        body = re.sub(r"\s+", " ", text).strip()

    if len(body) > max_chars:
        body = body[:max_chars].rsplit(" ", 1)[0] + "..."

    return {
        "url": url,
        "title": title or url,
        "content": body,
    }


def _read_url_source_cached(url: str, *, timeout_seconds: int = 8, max_chars: int = 3000) -> tuple[dict[str, str], bool]:
    key = url.strip().lower()
    now = time.time()
    cached = _URL_SOURCE_CACHE.get(key)
    if isinstance(cached, dict):
        cached_at = float(cached.get("at") or 0.0)
        payload = cached.get("payload")
        if now - cached_at <= URL_SOURCE_CACHE_TTL_SECONDS and isinstance(payload, dict):
            return {str(k): str(v) for k, v in payload.items()}, True
    source = _read_url_source(url, timeout_seconds=timeout_seconds, max_chars=max_chars)
    _URL_SOURCE_CACHE[key] = {"at": now, "payload": dict(source)}
    return source, False


def _source_analytics_events(raw: object) -> list[dict[str, object]]:
    if not isinstance(raw, list):
        return []
    events: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str) or not url.strip():
            continue
        events.append(
            {
                "ts": str(item.get("ts") or datetime.now(timezone.utc).isoformat()),
                "url": url.strip(),
                "title": str(item.get("title") or url.strip()),
                "chat_mode": str(item.get("chat_mode") or "chat"),
                "cached": bool(item.get("cached", False)),
                "snippet_chars": int(item.get("snippet_chars") or 0),
            }
        )
    return events[-SOURCE_ANALYTICS_MAX_EVENTS:]


def _record_source_analytics(config: MissionControlServerConfig, *, chat_mode: str, source_items: list[dict[str, object]]) -> None:
    if not source_items:
        return
    settings = _load_settings(config)
    existing = _source_analytics_events(settings.get("source_analytics"))
    now = datetime.now(timezone.utc).isoformat()
    for item in source_items:
        url = item.get("url")
        if not isinstance(url, str) or not url.strip():
            continue
        snippet = str(item.get("snippet") or "")
        existing.append(
            {
                "ts": now,
                "url": url.strip(),
                "title": str(item.get("title") or url.strip()),
                "chat_mode": chat_mode,
                "cached": bool(item.get("cached", False)),
                "snippet_chars": len(snippet),
            }
        )
    settings["source_analytics"] = existing[-SOURCE_ANALYTICS_MAX_EVENTS:]
    _save_settings(config, settings)


def _source_analytics_summary(raw: object) -> dict[str, object]:
    events = _source_analytics_events(raw)
    total_reads = len(events)
    cache_hits = sum(1 for event in events if bool(event.get("cached")))
    by_url: dict[str, dict[str, object]] = {}
    by_mode: dict[str, int] = {}
    for event in events:
        url = str(event.get("url") or "").strip()
        if not url:
            continue
        title = str(event.get("title") or url)
        mode = str(event.get("chat_mode") or "chat")
        by_mode[mode] = by_mode.get(mode, 0) + 1
        bucket = by_url.get(url)
        if bucket is None:
            bucket = {
                "url": url,
                "title": title,
                "reads": 0,
                "cache_hits": 0,
                "snippet_sum": 0,
            }
            by_url[url] = bucket
        bucket["reads"] = int(bucket["reads"]) + 1
        if bool(event.get("cached")):
            bucket["cache_hits"] = int(bucket["cache_hits"]) + 1
        bucket["snippet_sum"] = int(bucket["snippet_sum"]) + int(event.get("snippet_chars") or 0)

    ranked = sorted(
        by_url.values(),
        key=lambda entry: (int(entry["reads"]), int(entry["cache_hits"])),
        reverse=True,
    )
    top_sources: list[dict[str, object]] = []
    for entry in ranked[:5]:
        reads = int(entry["reads"])
        cache = int(entry["cache_hits"])
        snippet_sum = int(entry["snippet_sum"])
        top_sources.append(
            {
                "url": str(entry["url"]),
                "title": str(entry["title"]),
                "reads": reads,
                "cache_hits": cache,
                "cache_hit_rate": (cache / reads) if reads else 0.0,
                "snippet_chars_avg": int(snippet_sum / reads) if reads else 0,
                "rank_score": round(reads + (cache * 0.25), 3),
            }
        )

    return {
        "total_reads": total_reads,
        "unique_urls": len(by_url),
        "cache_hits": cache_hits,
        "cache_hit_rate": (cache_hits / total_reads) if total_reads else 0.0,
        "by_mode": by_mode,
        "top_sources": top_sources,
    }


def _trim_context_summary(context_summary: str, *, max_chars: int) -> str:
    text = context_summary.strip()
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return text[:max_chars]

    def _priority(line: str) -> int:
        lowered = line.lower()
        if line.startswith(("Selected task:", "Objective:", "Mergeable:", "Changed files:", "Risk flags:")):
            return 0
        if "correction" in lowered or "preferen" in lowered:
            return 1
        if line.startswith(("NEMO context:", "Cognitive preload")):
            return 2
        return 3

    ranked = sorted(enumerate(lines), key=lambda item: (_priority(item[1]), item[0]))
    kept: list[str] = []
    used = 0
    for _, line in ranked:
        extra = len(line) + (1 if kept else 0)
        if used + extra > max_chars:
            continue
        kept.append(line)
        used += extra
    if not kept:
        kept.append(text[: max_chars - 24])
    kept = sorted(kept, key=lambda line: lines.index(line))
    omitted = max(0, len(lines) - len(kept))
    if omitted > 0:
        kept.append(f"[context trimmed: omitted {omitted} line(s)]")
    return "\n".join(kept)


def _require_nemo_mcp_url(payload: dict[str, object]) -> str:
    value = payload.get("nemo_mcp_url")
    if not isinstance(value, str) or not value.strip():
        raise _bad_request(
            "nemo_mcp_url is required for native NEMO MCP mode in agent chat",
            error_code="missing_nemo_mcp_url",
        )
    return value.strip()


def _file_path(payload: dict[str, object]) -> str:
    file_path = payload.get("file_path")
    if not isinstance(file_path, str) or not file_path:
        raise _bad_request("file_path is required", error_code="missing_file_path")
    return file_path.replace("\\", "/")


def _read_text_preview(path: str | None) -> str | None:
    if not path:
        return None
    candidate = Path(path)
    if not candidate.exists() or not candidate.is_file():
        return None
    return candidate.read_text(encoding="utf-8", errors="replace")


def _hash_path(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _split_lines(text: str | None) -> list[str]:
    if text is None:
        return []
    return text.splitlines()


def _diff_hunks(target_content: str | None, source_content: str | None, *, context: int = 3) -> list[dict[str, object]]:
    old_lines = _split_lines(target_content)
    new_lines = _split_lines(source_content)
    matcher = SequenceMatcher(None, old_lines, new_lines)
    hunks: list[dict[str, object]] = []
    for index, group in enumerate(matcher.get_grouped_opcodes(context), start=1):
        old_start = min((old_a for tag, old_a, old_b, new_a, new_b in group if tag != "insert"), default=group[0][1]) + 1
        new_start = min((new_a for tag, old_a, old_b, new_a, new_b in group if tag != "delete"), default=group[0][3]) + 1
        old_end = max((old_b for tag, old_a, old_b, new_a, new_b in group if tag != "insert"), default=old_start - 1)
        new_end = max((new_b for tag, old_a, old_b, new_a, new_b in group if tag != "delete"), default=new_start - 1)
        rows: list[dict[str, object]] = []
        for tag, old_a, old_b, new_a, new_b in group:
            if tag == "equal":
                for offset, line in enumerate(old_lines[old_a:old_b]):
                    rows.append({"kind": "context", "old_line": old_a + offset + 1, "new_line": new_a + offset + 1, "content": line})
            elif tag == "delete":
                for offset, line in enumerate(old_lines[old_a:old_b]):
                    rows.append({"kind": "delete", "old_line": old_a + offset + 1, "new_line": None, "content": line})
            elif tag == "insert":
                for offset, line in enumerate(new_lines[new_a:new_b]):
                    rows.append({"kind": "insert", "old_line": None, "new_line": new_a + offset + 1, "content": line})
            else:
                for offset, line in enumerate(old_lines[old_a:old_b]):
                    rows.append({"kind": "delete", "old_line": old_a + offset + 1, "new_line": None, "content": line})
                for offset, line in enumerate(new_lines[new_a:new_b]):
                    rows.append({"kind": "insert", "old_line": None, "new_line": new_a + offset + 1, "content": line})
        hunks.append(
            {
                "id": f"hunk-{index}",
                "old_start": old_start,
                "old_count": max(0, old_end - old_start + 1),
                "new_start": new_start,
                "new_count": max(0, new_end - new_start + 1),
                "rows": rows,
                "old_lines": old_lines[old_start - 1:old_end],
                "new_lines": new_lines[new_start - 1:new_end],
            }
        )
    if not hunks and target_content != source_content:
        hunks.append({"id": "hunk-1", "old_start": 1, "old_count": len(old_lines), "new_start": 1, "new_count": len(new_lines), "rows": [], "old_lines": old_lines, "new_lines": new_lines})
    return hunks


def _apply_hunk_selection(target_content: str | None, hunks: list[dict[str, object]], accepted_hunks: set[str]) -> str:
    next_lines = _split_lines(target_content)
    for hunk in reversed(hunks):
        if str(hunk["id"]) not in accepted_hunks:
            continue
        start = max(0, int(hunk["old_start"]) - 1)
        count = int(hunk["old_count"])
        replacement = [str(line) for line in hunk.get("new_lines", [])]
        next_lines[start:start + count] = replacement
    return "\n".join(next_lines) + ("\n" if next_lines else "")


def _safe_stem(value: str) -> str:
    return "".join(char if char.isalnum() or char in ("-", "_") else "-" for char in value).strip("-") or "run"


def _string_list(payload: dict[str, object], key: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
    value = payload.get(key)
    if value is None:
        return fallback
    if isinstance(value, str):
        items = tuple(line.strip() for line in value.splitlines() if line.strip())
        return items or fallback
    if isinstance(value, list):
        items = tuple(str(item).strip() for item in value if str(item).strip())
        return items or fallback
    raise _bad_request(f"{key} must be a string or list", error_code=f"invalid_{key}")


def _enforce_workspace_scope(config: MissionControlServerConfig, payload: dict[str, object]) -> dict[str, object]:
    updated = dict(payload)
    targets = _string_list(updated, "target_files", ())
    if not targets:
        return updated
    allow_external = bool(updated.get("explicit_external_permission") or updated.get("allow_external_paths"))
    normalized_targets: list[str] = []
    external_targets: list[str] = []
    for target in targets:
        raw = target.strip()
        if not raw:
            continue
        candidate = Path(raw)
        resolved = candidate.resolve() if candidate.is_absolute() else (config.repo_path / candidate).resolve()
        if config.repo_path == resolved or config.repo_path in resolved.parents:
            normalized_targets.append(resolved.relative_to(config.repo_path).as_posix())
        else:
            external_targets.append(raw)
    if external_targets and not allow_external:
        raise _bad_request(
            "external target files require explicit permission. Set explicit_external_permission=true to continue.",
            error_code="external_targets_require_permission",
            status_code=409,
        )
    if external_targets:
        normalized_targets.extend(external_targets)
    updated["target_files"] = list(dict.fromkeys(normalized_targets))
    return updated


def _objective(payload: dict[str, object]) -> str:
    objective = payload.get("objective")
    if not isinstance(objective, str) or not objective.strip():
        raise _bad_request("objective is required", error_code="missing_objective")
    return objective.strip()


def _handoff_prd_text(payload: dict[str, object]) -> str | None:
    value = payload.get("prd_text")
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _handoff_spec_mode(payload: dict[str, object]) -> str:
    value = payload.get("spec_mode")
    if not isinstance(value, str) or not value.strip():
        return "auto"
    mode = value.strip()
    if mode not in {"auto", "sdd"}:
        raise _bad_request("spec_mode must be auto or sdd", error_code="invalid_spec_mode")
    return mode


def _handoff_request(config: MissionControlServerConfig, payload: dict[str, object]) -> HandoffRequest:
    objective = _objective(payload)
    linked_prd = _handoff_prd_text(payload)
    target_files = tuple(_string_list(payload, "target_files", ()))
    return HandoffRequest(
        prd=linked_prd or objective,
        repo_path=str(config.repo_path),
        acceptance_criteria=_string_list(payload, "acceptance_criteria", ("implementation satisfies the objective",)),
        validation_commands=validation_commands_for_policy(str(payload.get("validation_policy") or "smoke"), _string_list(payload, "validation_commands", ()), target_files=target_files),
        objective_summary=objective,
        linked_prd=linked_prd,
        spec_mode=_handoff_spec_mode(payload),
    )


def _nemo_adapter(
    memory_db: Path | None,
    *,
    nemo_mcp_url: str | None = None,
    nemo_mcp_prefix: str = "nemo.",
) -> PersistentNemoAdapter | McpNemoAdapter | None:
    normalized_mcp_url = str(nemo_mcp_url or "").strip()
    if normalized_mcp_url:
        return McpNemoAdapter(normalized_mcp_url, tool_prefix=nemo_mcp_prefix)
    if memory_db is None:
        return None
    return PersistentNemoAdapter(PersistentMemoryStore(memory_db))


def _require_nemo_mcp_for_execution(
    *,
    config: MissionControlServerConfig,
    payload: dict[str, object],
    provider: str,
    memory_enabled: bool,
) -> str:
    if provider != "subprocess" or not memory_enabled:
        return ""
    mcp_url = str(payload.get("nemo_mcp_url") or "").strip()
    if not mcp_url:
        raise _bad_request(
            "nemo_mcp_url is required for subprocess handoff execution",
            error_code="missing_nemo_mcp_url",
        )
    probe = _probe_nemo_mcp_sse(mcp_url)
    if not probe.get("active"):
        status = str(probe.get("status") or "unknown")
        details = str(probe.get("error") or probe.get("first_line") or "")
        message = f"nemo_mcp_url must be reachable and active before subprocess handoff (status={status})"
        if details:
            message = f"{message}: {details}"
        raise _bad_request(message, error_code="nemo_mcp_unreachable")
    require_capabilities = bool(payload.get("require_nemo_mcp_capabilities", payload.get("nemo_required", True)))
    require_roundtrip = bool(payload.get("require_nemo_roundtrip", require_capabilities))
    if require_capabilities:
        capabilities = _probe_nemo_mcp_capabilities(
            config,
            mcp_url=mcp_url,
            include_roundtrip_probe=require_roundtrip,
        )
        if not capabilities.get("supports_core_context_reads"):
            raise _bad_request(
                "NEMO MCP is reachable but missing required context-read capabilities before handoff execution",
                error_code="nemo_mcp_capability_mismatch",
            )
        if require_roundtrip and not capabilities.get("supports_write_read_roundtrip"):
            raise _bad_request(
                "NEMO MCP write/read continuity probe failed before handoff execution",
                error_code="nemo_mcp_roundtrip_failed",
            )
    return mcp_url


def _provider_mode(payload: dict[str, object]) -> str:
    provider = str(payload.get("provider") or "subprocess")
    if provider not in {"subprocess", "fake"}:
        raise _bad_request("provider must be subprocess or fake", error_code="invalid_provider")
    return provider


def _workflow_mode(payload: dict[str, object]) -> str | None:
    value = payload.get("workflow_mode")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _bad_request("workflow_mode must be plan, build, or review", error_code="invalid_workflow_mode")
    mode = value.strip().lower()
    if mode not in {"plan", "build", "review"}:
        raise _bad_request("workflow_mode must be plan, build, or review", error_code="invalid_workflow_mode")
    return mode


def _enforce_workflow_policy(payload: dict[str, object], *, action: str, allowed_modes: tuple[str, ...]) -> None:
    mode = _workflow_mode(payload)
    if mode is None:
        return
    if mode in allowed_modes:
        return
    allowed_label = ", ".join(allowed_modes)
    raise _bad_request(
        f"{action} is not allowed in workflow_mode={mode}; allowed mode(s): {allowed_label}",
        error_code="workflow_policy_violation",
    )


def _is_self_improvement_request(message: str) -> bool:
    text = message.lower()
    self_terms = (
        "automejor",
        "auto mejora",
        "auto-mejora",
        "self improve",
        "self-improve",
        "mejorate",
        "mejórate",
        "improve yourself",
    )
    capability_terms = (
        "programar",
        "code",
        "coding",
        "agent",
        "agente",
        "interfaz",
        "interface",
        "mission control",
    )
    return any(term in text for term in self_terms) and any(term in text for term in capability_terms)


def _timeout_seconds(payload: dict[str, object]) -> float:
    value = payload.get("timeout_seconds", 30.0)
    try:
        timeout = float(value)
    except (TypeError, ValueError) as error:
        raise _bad_request("timeout_seconds must be numeric", error_code="invalid_timeout_seconds") from error
    if timeout <= 0:
        raise _bad_request("timeout_seconds must be positive", error_code="invalid_timeout_seconds")
    provider = _provider_mode(payload)
    _validate_provider_timeout(provider, timeout, error_code="invalid_timeout_seconds")
    return timeout


def _chat_base_url(payload: dict[str, object]) -> str:
    value = payload.get("base_url") or payload.get("model_base_url") or "http://127.0.0.1:1234/v1"
    if not isinstance(value, str) or not value.strip():
        raise _bad_request("model_base_url is required", error_code="invalid_model_base_url")
    return value.strip().rstrip("/")


def _resolve_lmstudio_model(base_url: str, *, api_key: str = "lm-studio", default_model: str = "") -> str:
    """Return the best available chat model.

    Strategy (in order of preference):
    1. Use /api/v0/models (LM Studio management API): prefer state=loaded, type=llm|vlm,
       sorted by loaded_context_length descending so the most capable loaded model wins.
    2. Fall back to /v1/models with Authorization header (works for remote APIs like NVIDIA NIM).
    3. Fall back to default_model if provided (for remote endpoints without model discovery).
    Embedding/reranker models are always excluded.
    """
    _SKIP = re.compile(r"embed|rerank|bge|nomic", re.I)
    _CHAT_TYPES = {"llm", "vlm"}
    base = base_url.rstrip("/")
    # Management API lives at the root, not under /v1
    mgmt_base = re.sub(r"/v\d+$", "", base)
    # Strategy 1: LM Studio management API with state info
    try:
        req = urllib.request.Request(f"{mgmt_base}/api/v0/models", method="GET")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=3) as r:
            data = json.loads(r.read().decode())
        candidates = [
            m for m in data.get("data", [])
            if m.get("type") in _CHAT_TYPES
            and m.get("state") == "loaded"
            and not _SKIP.search(m.get("id", ""))
        ]
        if candidates:
            candidates.sort(key=lambda m: int(m.get("loaded_context_length") or 0), reverse=True)
            return str(candidates[0]["id"])
    except Exception:
        pass
    # Strategy 2: /v1/models with Authorization (works for NVIDIA NIM and other remote APIs)
    _UUID_ONLY = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
    try:
        req = urllib.request.Request(f"{base}/models", method="GET")
        req.add_header("Authorization", f"Bearer {api_key}")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode())
        models = [
            m["id"] for m in data.get("data", [])
            if not _SKIP.search(m.get("id", "")) and not _UUID_ONLY.match(m.get("id", ""))
        ]
        # Prefer named models (contain "/") — NIM public models are "org/name" format
        named = [m for m in models if "/" in m]
        if named:
            return named[0]
        if models:
            return models[0]
    except Exception:
        pass
    # Strategy 3: explicit default_model for remote endpoints without discovery
    return default_model


_VLM_KEYWORDS = re.compile(r"vision|multimodal|vl\b|vlm\b|-vl-|-vlm-|fuyu|llava|intern.?vl|qwen.*vl|phi.*visual|phi.*multi", re.I)

def _resolve_vlm_model(base_url: str, *, api_key: str = "lm-studio") -> str | None:
    """Return the best available VLM.

    Strategy 1: LM Studio management API (type=vlm, state=loaded).
    Strategy 2: OpenAI-compatible /v1/models filtered by vision keyword (for remote APIs).
    Returns None if no vision model is found.
    """
    _SKIP = re.compile(r"embed|rerank|bge|nomic|guard|safety|nemoretriever|nv-embedqa", re.I)
    base = base_url.rstrip("/")
    mgmt_base = re.sub(r"/v\d+$", "", base)
    # Strategy 1: LM Studio management API
    try:
        req = urllib.request.Request(f"{mgmt_base}/api/v0/models", method="GET")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=3) as r:
            data = json.loads(r.read().decode())
        candidates = [
            m for m in data.get("data", [])
            if m.get("type") == "vlm"
            and m.get("state") == "loaded"
            and not _SKIP.search(m.get("id", ""))
        ]
        if candidates:
            candidates.sort(key=lambda m: int(m.get("loaded_context_length") or 0), reverse=True)
            return str(candidates[0]["id"])
    except Exception:
        pass
    # Strategy 2: OpenAI-compatible /v1/models — filter by vision keywords (NVIDIA NIM, etc.)
    try:
        req = urllib.request.Request(f"{base}/models", method="GET")
        req.add_header("Authorization", f"Bearer {api_key}")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode())
        candidates = [
            m["id"] for m in data.get("data", [])
            if _VLM_KEYWORDS.search(m.get("id", ""))
            and not _SKIP.search(m.get("id", ""))
        ]
        # Prefer smaller/faster vision models (they tend to have lower parameter counts in name)
        def _vlm_priority(mid: str) -> int:
            for tok in ("11b", "8b", "4b", "2b", "7b", "12b"):
                if tok in mid.lower():
                    return int(tok[:-1])
            return 999
        candidates.sort(key=_vlm_priority)
        if candidates:
            return candidates[0]
    except Exception:
        pass
    return None


def _chat_model(payload: dict[str, object]) -> str:
    _UUID_ONLY = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
    # Explicit model from payload (user just selected it — trust even if UUID)
    value = str(payload.get("model") or "").strip()
    if not value:
        # default_model from settings may be a stale UUID NIM private endpoint — skip it
        default = str(payload.get("default_model") or "").strip()
        value = default if default and not _UUID_ONLY.match(default) else ""
    if not value:
        api_key = str(payload.get("api_key") or os.environ.get("LMSTUDIO_API_KEY") or "lm-studio")
        value = _resolve_lmstudio_model(_chat_base_url(payload), api_key=api_key)
    if not value:
        raise _bad_request("No model configured and LM Studio has no chat models loaded", error_code="invalid_default_model")
    return value


def _positive_int(value: object, fallback: int, *, minimum: int = 1, maximum: int | None = None) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        parsed = fallback
    parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(parsed, maximum)
    return parsed


def _redact_secrets(text: str) -> str:
    value = str(text)
    # Redact Bearer tokens (Authorization headers, inline JSON)
    value = re.sub(r'Bearer\s+[A-Za-z0-9\-_\.]{8,}', 'Bearer ***', value)
    # Redact api_key / password JSON fields
    value = re.sub(r'"(api_key|password|secret|token)"\s*:\s*"[^"]*"', r'"\1": "***"', value, flags=re.I)
    # Redact credentials embedded in URLs (https://user:pass@host)
    value = re.sub(r'://[^:@/\s]+:[^@/\s]+@', '://***:***@', value)
    # Redact known env secrets by value
    for env_name in ("LMSTUDIO_API_KEY", "OPENAI_API_KEY", "NVIDIA_API_KEY"):
        secret = os.environ.get(env_name)
        if secret and len(secret) > 4:
            value = value.replace(secret, "***")
    return value


def _agent_context_summary(
    selected: dict[str, Any] | None,
    changed_files: tuple[str, ...],
    risk_flags: tuple[str, ...],
    mergeable: bool,
    cognitive_preload: str = "",
    *,
    max_context_chars: int = 2400,
) -> str:
    task = selected.get("task") if isinstance(selected, dict) and isinstance(selected.get("task"), dict) else None
    run = selected.get("run") if isinstance(selected, dict) and isinstance(selected.get("run"), dict) else None
    portfolio = selected.get("portfolio") if isinstance(selected, dict) and isinstance(selected.get("portfolio"), dict) else None
    packet = assemble_context(
        task=task,
        run=run,
        portfolio=portfolio,
        cognitive_preload=cognitive_preload,
        changed_files=changed_files,
        risk_flags=risk_flags,
        mergeable=mergeable,
        max_chars=max_context_chars,
    )
    return packet.text


def _nemo_bootstrap_packet(tool_calls: list[dict[str, object]], *, max_chars: int = 1600) -> str:
    """Compact the real NEMO bootstrap results into a prompt-safe packet."""
    lines = ["NEMO bootstrap packet:"]
    interesting = (
        "nemo_memory.prime_context",
        "nemo_memory.build_context_portfolio",
        "nemo_memory.search_memories",
        "nemo_memory.anticipate",
    )
    for tool_name in interesting:
        for call in reversed(tool_calls):
            if str(call.get("name") or "") != tool_name:
                continue
            status = str(call.get("status") or "unknown")
            summary = str(call.get("summary") or "").strip()
            if summary:
                lines.append(f"- {tool_name}: {status} — {summary}")
            else:
                lines.append(f"- {tool_name}: {status}")
            break
    packet = "\n".join(lines)
    return _trim_output(packet, max_chars)


def _verified_nemo_tool_list() -> str:
    return ", ".join(sorted(contract.name for contract in NEMO_TOOL_REGISTRY))


def _probe_arguments_for_tool(tool_name: str) -> dict[str, Any]:
    probes: dict[str, dict[str, Any]] = {
        "prime_context": {"topic": "tool-scan"},
        "context_bootstrap": {"topic": "tool-scan", "task": "tool scan", "limit": 1, "token_budget": 128},
        "build_context_portfolio": {"task": "tool scan", "topic": "tool-scan", "token_budget": 128},
        "expand_context_evidence": {"handle": "probe-missing-handle"},
        "get_context_portfolio_stats": {},
        "compare_context_strategies": {"task": "tool scan", "topic": "tool-scan", "token_budget": 128},
        "refresh_context_portfolio": {"task": "tool scan", "topic": "tool-scan", "token_budget": 128},
        "anticipate": {"task": "tool scan", "limit": 1},
        "detect_redundancy": {"limit": 5},
        "memory_chronicle": {"limit": 5},
        "salience_score": {"content": "tool scan", "task": "tool scan"},
        "get_recent_context": {"limit": 3},
        "get_current_time": {},
        "get_environment_info": {},
        "get_weather_open_meteo": {},
        "get_system_health": {},
        "get_active_reminders": {"limit": 5},
        "get_completed_reminders": {"limit": 5},
        "get_recent_appointments": {"limit": 5},
        "get_upcoming_appointments": {"limit": 5},
        "search_memories": {"query": "tool", "limit": 1},
    }
    return probes.get(tool_name, {})


def _first_allowed_phase(tool_name: str) -> NemoLifecyclePhase:
    for phase in NemoLifecyclePhase:
        if tool_allowed_in_lifecycle(phase, tool_name):
            return phase
    return NemoLifecyclePhase.REVIEW


def _runtime_verified_nemo_tools(payload: dict[str, object]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    mcp_url = str(payload.get("nemo_mcp_url") or "").strip()
    if mcp_url:
        declared_read_only = tuple(sorted(contract.name for contract in NEMO_TOOL_REGISTRY if contract.risk == NemoToolRisk.READ_ONLY))
        declared_write_or_destructive = tuple(
            sorted(
                contract.name
                for contract in NEMO_TOOL_REGISTRY
                if contract.risk in {NemoToolRisk.MEMORY_WRITE, NemoToolRisk.SCHEDULING_WRITE, NemoToolRisk.DESTRUCTIVE}
            )
        )
        return declared_read_only, declared_write_or_destructive

    now = time.time()
    cache_at = float(_NEMO_TOOL_SCAN_CACHE.get("at", 0.0) or 0.0)
    if now - cache_at <= NEMO_TOOL_SCAN_TTL_SECONDS:
        cached_verified = tuple(_NEMO_TOOL_SCAN_CACHE.get("verified_read_only", ()) or ())
        cached_declared = tuple(_NEMO_TOOL_SCAN_CACHE.get("declared_write_or_destructive", ()) or ())
        return cached_verified, cached_declared

    memory_db = str(payload.get("memory_db") or ".nemo-runtimes/nemo-memory.sqlite")
    adapter = PersistentNemoAdapter(PersistentMemoryStore(Path(memory_db)))

    declared_write_or_destructive = sorted(
        contract.name
        for contract in NEMO_TOOL_REGISTRY
        if contract.risk in {NemoToolRisk.MEMORY_WRITE, NemoToolRisk.SCHEDULING_WRITE, NemoToolRisk.DESTRUCTIVE}
    )

    verified_read_only: list[str] = []
    for contract in NEMO_TOOL_REGISTRY:
        if contract.risk != NemoToolRisk.READ_ONLY:
            continue
        phase = _first_allowed_phase(contract.name)
        try:
            _, result = adapter.call(phase, contract.name, **_probe_arguments_for_tool(contract.name))
            if result.ok:
                verified_read_only.append(contract.name)
        except Exception:
            continue

    verified_tuple = tuple(sorted(set(verified_read_only)))
    declared_tuple = tuple(declared_write_or_destructive)
    _NEMO_TOOL_SCAN_CACHE["at"] = now
    _NEMO_TOOL_SCAN_CACHE["verified_read_only"] = verified_tuple
    _NEMO_TOOL_SCAN_CACHE["declared_write_or_destructive"] = declared_tuple
    return verified_tuple, declared_tuple


def _lmstudio_chat_completion(payload: dict[str, object], user_message: str, context_summary: str, history: list[dict[str, str]] | None = None) -> str:
    verified_tools = _verified_nemo_tool_list()
    runtime_read_only, declared_write_or_destructive = _runtime_verified_nemo_tools(payload)
    native_mcp_url = str(payload.get("nemo_mcp_url") or "").strip()
    native_mode = "enabled" if native_mcp_url else "disabled"
    runtime_read_text = ", ".join(runtime_read_only) if runtime_read_only else "none"
    runtime_write_text = ", ".join(declared_write_or_destructive) if declared_write_or_destructive else "none"
    body = {
        "model": _chat_model(payload),
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are the Space Code Mission Control coding agent for the local-first Space Code platform. "
                    "Space Code is the software-engineering product (planning, coding, testing, review, apply). "
                    "NEMO MCP is the memory/context plane used by this product; it is not the product itself. "
                    "Do not describe yourself as a generic messaging system. "
                    "Answer in the user's language. Be concise, direct, and operational — never ask for confirmation before acting. "
                    "IMPORTANT: The Mission Control server bootstrapped NEMO context before calling you and included the real results below. "
                    "Treat that packet as authoritative memory for this response. Do NOT claim you personally called the tools, but do use the bootstrapped context directly. "
                    "When the user asks you to save, store, or remember something, confirm it is already saved (the server did it). "
                    "Do not claim you applied code unless an explicit apply action did it. "
                    "Never invent MCP/NEMO tool names or capabilities outside the verified catalog below. "
                    "Never answer with only a raw tool name such as get_current_time, search_memories, or context_bootstrap; explain the actual answer or next action in natural language.\n\n"
                    "ROUTING RULES — FOLLOW EXACTLY:\n"
                    "- Web search, URL navigation, web scraping, login, form filling, any website interaction: ALWAYS use browser_task with the real target URL. Never write a Python/requests script for this.\n"
                    "- Source file changes (create/edit .py, .ts, .js, .tsx, .go, .rs, etc.): ALWAYS use handoff_start. Never write source code inline.\n"
                    "- Standalone scripts, data visualizations, standalone programs: use plan_generate.\n"
                    "- Visual artifacts (HTML page, SVG, Mermaid diagram, React component, image prompt): generate inline using the artifact fences below.\n"
                    "- Conversation, questions, status queries: respond directly in text.\n"
                    "CRITICAL: NEVER ask '¿Quieres ejecutar...?' or 'Do you want me to...?' — trigger the tool IMMEDIATELY. The user asked for the action, not a proposal.\n"
                    "BREVITY RULE: Keep responses under 80 words. When triggering a tool, embed the JSON and add ONE short sentence. No pseudocode, no lists."
                ),
            },
            {
                "role": "system",
                "content": (
                    "ARTIFACT STUDIO OUTPUT CONTRACT:\n"
                    "When the user asks for a chart, dashboard, visualization, diagram, interface mockup, visual report, image prompt, or other renderable artifact, produce a typed fenced code block so Mission Control can render and version it.\n\n"
                    "Supported artifact fences (for visual/renderable content ONLY — NOT for source files):\n"
                    "- ```html_artifact for complete self-contained HTML documents with inline CSS/JS.\n"
                    "- ```svg_artifact for SVG with viewBox and xmlns.\n"
                    "- ```mermaid for diagrams.\n"
                    "- ```react_artifact for self-contained React components exposing function App().\n"
                    "- ```image_request for JSON image-generation prompts.\n\n"
                    "IMPORTANT: These artifact fences are ONLY for renderable artifacts. "
                    "For source files (.py, .ts, .js, etc.) use handoff_start instead — never write them inline. "
                    "For images, emit image_request JSON with prompt, negative_prompt, size, style, steps, and cfg when useful; Mission Control can send it to a local image backend. "
                    "Optionally put a title marker as the first line, for example <!-- ARTIFACT:Metrics Dashboard:html -->.\n"
                    "Keep artifact code self-contained and compatible with a sandboxed preview."
                ),
            },
            {"role": "system", "content": f"[SERVER-SIDE ONLY — YOU CANNOT CALL THESE] NEMO MCP tools already executed by the backend before this response: {verified_tools}. These are informational only. Never embed NEMO tool names in your response."},
            {"role": "system", "content": f"NEMO MCP native mode: {native_mode}. URL: {native_mcp_url or 'not configured'}"},
            {"role": "system", "content": f"[SERVER-SIDE ONLY] Runtime-verified NEMO READ tools (executed by backend, not you): {runtime_read_text}"},
            {"role": "system", "content": f"[SERVER-SIDE ONLY] NEMO WRITE tools (executed by backend, not you): {runtime_write_text}"},
            {"role": "system", "content": _AGENT_TOOL_CATALOG},
            {"role": "system", "content": context_summary},
            *[{"role": turn["role"], "content": turn["content"]} for turn in (history or [])[-10:]],
            {"role": "user", "content": user_message},
        ],
        "temperature": float(payload.get("chat_temperature", payload.get("temperature", 0.2))),
        "max_tokens": _chat_max_tokens(payload),
        "stream": False,
        "enable_thinking": bool(payload.get("enable_thinking", False)),
    }
    data = json.dumps(body).encode("utf-8")
    _api_key = str(payload.get("api_key") or os.environ.get("LMSTUDIO_API_KEY") or "lm-studio")
    request = urllib.request.Request(
        f"{_chat_base_url(payload)}/chat/completions",
        data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {_api_key}"},
        method="POST",
    )
    timeout = min(max(_timeout_seconds(payload), 1.0), 300.0)
    try:
        with _LLM_SEM:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        details = _redact_secrets(error.read().decode("utf-8", errors="replace"))
        raise ValueError(f"LM Studio chat failed: HTTP {error.code} {details[:400]}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise ValueError(f"LM Studio chat failed: {_redact_secrets(str(error))}") from error
    choices = response_payload.get("choices") if isinstance(response_payload, dict) else None
    if not isinstance(choices, list) or not choices:
        raise ValueError("LM Studio chat failed: response had no choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise ValueError("LM Studio chat failed: invalid choice")
    response_message = first.get("message")
    if not isinstance(response_message, dict) or not isinstance(response_message.get("content"), str):
        raise ValueError("LM Studio chat failed: response message had no content")
    return response_message["content"].strip() or "The model returned an empty response."


def _chat_max_tokens(payload: dict[str, object]) -> int:
    budget = _positive_int(payload.get("token_budget"), 128000, minimum=4096, maximum=512000)
    fallback = min(max(budget // 4, DEFAULT_CHAT_MAX_TOKENS), MAX_CHAT_MAX_TOKENS)
    value = payload.get("max_tokens", payload.get("chat_max_tokens", os.environ.get("NEMO_CHAT_MAX_TOKENS", fallback)))
    return _positive_int(value, fallback, minimum=1024, maximum=MAX_CHAT_MAX_TOKENS)


def _agent_context_char_budget(payload: dict[str, object], mode_context_chars: int, mode_default: int = DEFAULT_CONTEXT_WINDOW_TOKENS) -> int:
    context_window = _positive_int(
        payload.get("context_window_tokens", os.environ.get("NEMO_CONTEXT_WINDOW_TOKENS", mode_default)),
        mode_default,
        minimum=8192,
        maximum=512000,
    )
    context_window = min(context_window, mode_default)  # cap to mode ceiling even when settings bleeds in
    output_tokens = _chat_max_tokens(payload)
    reserved_tokens = max(4096, output_tokens + 2048)
    available_tokens = max(2048, context_window - reserved_tokens)
    dynamic_chars = available_tokens * 4
    return max(mode_context_chars, min(dynamic_chars, 192000))


def api_generate_image(config: MissionControlServerConfig, payload: dict[str, object]) -> dict[str, object]:
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        raise _bad_request("prompt is required", error_code="missing_prompt")
    negative_prompt = str(payload.get("negative_prompt") or "").strip()
    size = str(payload.get("size") or "1024x1024")
    steps = _positive_int(payload.get("steps"), 28, minimum=1, maximum=120)
    cfg = float(payload.get("cfg") or 7.0)
    style = str(payload.get("style") or "").strip()

    width, height = 1024, 1024
    try:
        width_text, height_text = size.lower().split("x", 1)
        width, height = int(width_text), int(height_text)
    except (ValueError, AttributeError):
        pass
    width = max(256, min(width, 2048))
    height = max(256, min(height, 2048))
    if style:
        prompt = f"({style}), {prompt}"

    settings = _load_settings(config)
    image_gen_backend = str(settings.get("image_gen_backend") or payload.get("image_gen_backend") or "auto").strip().lower()
    image_gen_url = str(settings.get("image_gen_url") or payload.get("image_gen_url") or "").strip()

    def _write_generated_image(image_bytes: bytes) -> dict[str, object]:
        artifacts_dir = Path(config.repo_path) / ".nemo-runtimes" / "mission-control" / "artifacts" / "images"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        image_name = f"gen-{uuid4().hex[:8]}.png"
        image_path = artifacts_dir / image_name
        image_path.write_bytes(image_bytes)
        return {"ok": True, "image_path": str(image_path), "image_url": f"/api/artifacts/image/{image_name}", "prompt": prompt, "size": f"{width}x{height}"}

    def _try_automatic1111(base_url: str) -> dict[str, object]:
        import base64 as b64

        body = json.dumps({
            "prompt": prompt,
            "negative_prompt": negative_prompt or "blurry, low quality, distorted",
            "width": width,
            "height": height,
            "steps": steps,
            "cfg_scale": cfg,
        }).encode("utf-8")
        request = urllib.request.Request(f"{base_url.rstrip('/')}/sdapi/v1/txt2img", data=body, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=180) as response:
            result = json.loads(response.read().decode("utf-8"))
        images = result.get("images", []) if isinstance(result, dict) else []
        if not images:
            raise ValueError("No images returned from AUTOMATIC1111")
        return _write_generated_image(b64.b64decode(images[0]))

    def _try_comfyui(base_url: str) -> dict[str, object]:
        workflow = {
            "3": {"class_type": "KSampler", "inputs": {"seed": int(time.time()), "steps": steps, "cfg": cfg, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0, "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
            "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}},
            "5": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": negative_prompt or "blurry, low quality", "clip": ["4", 1]}},
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
            "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "nemo_gen", "images": ["8", 0]}},
        }
        queue_body = json.dumps({"prompt": workflow}).encode("utf-8")
        request = urllib.request.Request(f"{base_url.rstrip('/')}/prompt", data=queue_body, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=10) as response:
            queue_result = json.loads(response.read().decode("utf-8"))
        prompt_id = queue_result.get("prompt_id") if isinstance(queue_result, dict) else None
        if not prompt_id:
            raise ValueError("ComfyUI did not return a prompt_id")
        deadline = time.time() + 180
        while time.time() < deadline:
            time.sleep(1)
            with urllib.request.urlopen(f"{base_url.rstrip('/')}/history/{prompt_id}", timeout=5) as response:
                history = json.loads(response.read().decode("utf-8"))
            if prompt_id not in history:
                continue
            outputs = history[prompt_id].get("outputs", {})
            for node_output in outputs.values():
                if "images" not in node_output:
                    continue
                image_info = node_output["images"][0]
                image_url = f"{base_url.rstrip('/')}/view?filename={quote_plus(image_info['filename'])}&subfolder={quote_plus(image_info.get('subfolder', ''))}&type={quote_plus(image_info.get('type', 'output'))}"
                with urllib.request.urlopen(image_url, timeout=30) as image_response:
                    return _write_generated_image(image_response.read())
        raise ValueError("ComfyUI generation timed out after 180 seconds")

    if image_gen_backend in {"automatic1111", "a1111"}:
        backends_to_try = [("a1111", image_gen_url or "http://localhost:7860")]
    elif image_gen_backend == "comfyui":
        backends_to_try = [("comfyui", image_gen_url or "http://localhost:8188")]
    else:
        backends_to_try = [("a1111", image_gen_url or "http://localhost:7860"), ("comfyui", image_gen_url or "http://localhost:8188")]

    last_error = ""
    for backend_name, backend_url in backends_to_try:
        try:
            check_path = "sdapi/v1/sd-models" if backend_name == "a1111" else "system_stats"
            with urllib.request.urlopen(f"{backend_url.rstrip('/')}/{check_path}", timeout=3):
                pass
            return _try_automatic1111(backend_url) if backend_name == "a1111" else _try_comfyui(backend_url)
        except Exception as error:  # noqa: BLE001
            last_error = f"{backend_name} at {backend_url}: {error}"
            continue
    raise _bad_request(
        f"No image generation backend available. Tried: {', '.join(name for name, _ in backends_to_try)}. Last error: {last_error}",
        error_code="image_gen_unavailable",
    )


# ---------------------------------------------------------------------------
# Credential Vault API
# ---------------------------------------------------------------------------

_LOOPBACK = {"::1", "::ffff:127.0.0.1"}


def _vault_db(config: MissionControlServerConfig) -> Path:
    return config.runtimes_path / "mission-control" / "vault.db"


def api_vault_list(config: MissionControlServerConfig) -> dict:
    from nemo_coding_platform.credential_vault import vault_list
    return {"credentials": vault_list(_vault_db(config))}


def api_vault_create(config: MissionControlServerConfig, payload: dict) -> dict:
    from nemo_coding_platform.credential_vault import vault_create
    alias = str(payload.get("alias") or "").strip()
    username = str(payload.get("username") or "")
    password = str(payload.get("password") or "")
    if not alias:
        raise _bad_request("alias is required", error_code="missing_alias")
    if not username or not password:
        raise _bad_request("username and password are required", error_code="missing_credentials")
    try:
        result = vault_create(
            _vault_db(config),
            alias,
            username,
            password,
            str(payload.get("url_pattern") or ""),
            str(payload.get("notes") or ""),
        )
    except Exception as exc:
        if "UNIQUE" in str(exc).upper():
            raise _bad_request(f"Alias '{alias}' already exists", error_code="duplicate_alias") from exc
        raise
    return result


def api_vault_update(config: MissionControlServerConfig, payload: dict) -> dict:
    from nemo_coding_platform.credential_vault import vault_update
    cred_id = str(payload.get("id") or "").strip()
    if not cred_id:
        raise _bad_request("id is required", error_code="missing_id")
    fields = {k: payload[k] for k in ("alias", "username", "password", "url_pattern", "notes") if k in payload}
    if "alias" in fields:
        fields["alias"] = str(fields["alias"]).strip()
        if not fields["alias"]:
            raise _bad_request("alias cannot be empty", error_code="missing_alias")
    if "username" in fields and not str(fields["username"]).strip():
        raise _bad_request("username cannot be empty", error_code="missing_credentials")
    if "password" in fields and not str(fields["password"]).strip():
        raise _bad_request("password cannot be empty", error_code="missing_credentials")
    try:
        return vault_update(_vault_db(config), cred_id, **fields)
    except KeyError:
        raise _bad_request(f"Credential {cred_id} not found", error_code="not_found", status_code=404)
    except Exception as exc:
        if "UNIQUE" in str(exc).upper():
            raise _bad_request("Alias already exists", error_code="duplicate_alias") from exc
        raise


def api_vault_delete(config: MissionControlServerConfig, payload: dict) -> dict:
    from nemo_coding_platform.credential_vault import vault_delete, vault_list
    cred_id = str(payload.get("id") or "").strip()
    if not cred_id:
        # Allow deleting by alias as a convenience
        alias = str(payload.get("alias") or "").strip()
        if not alias:
            raise _bad_request("id or alias is required", error_code="missing_id")
        creds = vault_list(_vault_db(config))
        match = next((c for c in creds if c["alias"] == alias), None)
        if match is None:
            raise _bad_request(f"Credential alias '{alias}' not found", error_code="not_found", status_code=404)
        cred_id = match["id"]
    try:
        vault_delete(_vault_db(config), cred_id)
    except KeyError:
        raise _bad_request(f"Credential {cred_id} not found", error_code="not_found", status_code=404)
    return {"ok": True}


def api_vault_lookup(config: MissionControlServerConfig, payload: dict, client_address: str) -> dict:
    """Internal-only: decrypts and returns credentials. Only callable from 127.0.0.1."""
    if not (client_address.startswith("127.") or client_address in _LOOPBACK):
        raise _bad_request("Forbidden", error_code="forbidden", status_code=403)
    from nemo_coding_platform.credential_vault import vault_lookup
    alias = str(payload.get("alias") or "").strip()
    if not alias:
        raise _bad_request("alias is required", error_code="missing_alias")
    try:
        return vault_lookup(_vault_db(config), alias)
    except KeyError:
        raise _bad_request(f"Alias '{alias}' not found", error_code="not_found", status_code=404)


# ---------------------------------------------------------------------------
# Browser Agent API
# ---------------------------------------------------------------------------

def api_browser_confirm(payload: dict) -> dict:
    from nemo_coding_platform.browser_agent import confirm_action
    session_id = str(payload.get("session_id") or "")
    approved = bool(payload.get("approved", False))
    if not session_id:
        raise _bad_request("session_id is required", error_code="missing_session_id")
    found = confirm_action(session_id, approved)
    if not found:
        raise _bad_request(f"Session {session_id} not found", error_code="not_found", status_code=404)
    return {"ok": True}


def api_browser_cancel(payload: dict) -> dict:
    from nemo_coding_platform.browser_agent import cancel_session
    session_id = str(payload.get("session_id") or "")
    if not session_id:
        raise _bad_request("session_id is required", error_code="missing_session_id")
    cancel_session(session_id)
    return {"ok": True}


def api_browser_sessions(config: MissionControlServerConfig) -> dict:
    from nemo_coding_platform.browser_agent import get_sessions_info
    return {"sessions": get_sessions_info()}


_MODELS_SKIP = re.compile(
    r"embed|rerank|bge|nomic|guard|safety|nemoretriever|nv-embedqa|content\.safety|topic\.control"
    r"|whisper|neva|fuyu|deplot|clip|ocdrnet|grounding|segmentation|vila|reward|parse|translate"
    r"|gliner|detect|cosmos-reason|ising-calibration|synthetic-video|ai-synthetic",
    re.I,
)
_THINKING_MODEL = re.compile(
    r"deepseek.r1|deepseek.v4|qwq|qwen.*think|nemotron.*ultra|nemotron.*super|nemotron.*reasoning"
    r"|nemotron.*omni.*reasoning|cosmos.reason|seed.*instruct|kimi|glm.5",
    re.I,
)
_NO_TEMPERATURE = re.compile(r"\bo1\b|\bo3\b|\bo4\b", re.I)  # o1-style reasoning_effort models


def _model_caps(model_id: str) -> dict[str, object]:
    """Infer model capabilities from its name."""
    thinking = bool(_THINKING_MODEL.search(model_id))
    no_temp = bool(_NO_TEMPERATURE.search(model_id))
    return {
        "thinking": thinking,
        "temperature": not no_temp,
        "reasoning_effort": no_temp,
    }


def _fetch_lmstudio_models(base_url: str) -> list[dict[str, object]]:
    models: list[dict[str, object]] = []
    seen: set[str] = set()
    # LM Studio management API — richer metadata (type, state, loaded vs available)
    try:
        mgmt_base = re.sub(r"/v\d+$", "", base_url)
        req = urllib.request.Request(f"{mgmt_base}/api/v0/models", method="GET")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode())
        for m in data.get("data", []):
            mid = str(m.get("id") or "").strip()
            if not mid or mid in seen or _MODELS_SKIP.search(mid):
                continue
            if m.get("type") not in {"llm", "vlm"}:
                continue
            seen.add(mid)
            models.append({"id": mid, "type": str(m.get("type") or "llm"), "state": str(m.get("state") or ""), "caps": _model_caps(mid)})
        if models:
            return models
    except Exception:
        pass
    # Fallback: OpenAI-compatible /v1/models
    try:
        req = urllib.request.Request(f"{base_url}/models", method="GET")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode())
        for m in data.get("data", []):
            mid = str(m.get("id") or "").strip()
            if not mid or mid in seen or _MODELS_SKIP.search(mid):
                continue
            seen.add(mid)
            models.append({"id": mid, "type": "llm", "state": "loaded", "caps": _model_caps(mid)})
    except Exception:
        pass
    return models


def _fetch_nvidia_nim_models(api_key: str) -> list[dict[str, object]]:
    _NIM_BASE = "https://integrate.api.nvidia.com/v1"
    models: list[dict[str, object]] = []
    seen: set[str] = set()
    if not api_key or not api_key.startswith("nvapi-"):
        return models
    try:
        req = urllib.request.Request(f"{_NIM_BASE}/models", method="GET")
        req.add_header("Authorization", f"Bearer {api_key}")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())
        for m in data.get("data", []):
            mid = str(m.get("id") or "").strip()
            if not mid or mid in seen or _MODELS_SKIP.search(mid):
                continue
            seen.add(mid)
            models.append({"id": mid, "type": "llm", "state": "available", "caps": _model_caps(mid)})
    except Exception:
        pass
    models.sort(key=lambda m: m["id"])
    return models


def api_models(config: MissionControlServerConfig) -> dict[str, object]:
    """Return available models from all configured providers (Local LM Studio + NVIDIA NIM)."""
    settings = _load_settings(config)
    local_base = "http://127.0.0.1:1234/v1"
    api_key = str(settings.get("api_key") or os.environ.get("LMSTUDIO_API_KEY") or "lm-studio")
    current_model = str(settings.get("default_model") or "")
    current_base = str(settings.get("model_base_url") or local_base).rstrip("/")

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        local_fut = pool.submit(_fetch_lmstudio_models, local_base)
        nvidia_fut = pool.submit(_fetch_nvidia_nim_models, api_key)
        local_models = local_fut.result()
        nvidia_models = nvidia_fut.result()

    _NIM_BASE = "https://integrate.api.nvidia.com/v1"
    current_provider = "nvidia" if current_base.startswith("https://integrate.api.nvidia.com") else "local"
    providers = [
        {"id": "local", "label": "Local — LM Studio", "base_url": local_base, "models": local_models, "available": bool(local_models)},
        {"id": "nvidia", "label": "NVIDIA NIM", "base_url": _NIM_BASE, "models": nvidia_models, "available": bool(nvidia_models)},
    ]
    # Legacy flat list for backwards compat with existing CommandDock fetch
    all_models = local_models + nvidia_models
    return {"providers": providers, "models": all_models, "current": current_model, "current_provider": current_provider}


def _raw_tool_name_fallback(response: str, message: str) -> str | None:
    raw = response.strip().strip("` ")
    if not raw:
        return None
    known_tools = {contract.name for contract in NEMO_TOOL_REGISTRY}
    known_tools.update({f"nemo_memory.{name}" for name in known_tools})
    known_tools.update({"get_current_time", "get_system_health", "web_search", "browser_search"})
    if raw not in known_tools:
        return None
    if len(raw.split()) != 1:
        return None
    lowered = message.casefold()
    if lowered in {"ping", "hola", "hello", "test"} or "diagnostico" in lowered or "diagnóstico" in lowered:
        return (
            "Estoy operativo. NEMO MCP ya cargó contexto y guardó esta interacción; "
            "el modelo respondió con un nombre de herramienta crudo, así que Mission Control lo normalizó para no bloquear el chat."
        )
    return (
        "Estoy operativo, pero el modelo devolvió un nombre de herramienta crudo en vez de una respuesta natural. "
        "NEMO MCP siguió activo y la conversación fue registrada; reformulo la salida para continuar sin bloquear el chat."
    )


def _galaxy_canvas_html() -> str:
        return """\
<!-- ARTIFACT:Galaxy Spiral — Space Code:html -->
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#000814;overflow:hidden;width:100vw;height:100vh}
canvas{display:block}
.hud{position:absolute;top:16px;left:50%;transform:translateX(-50%);text-align:center;pointer-events:none}
h1{font:700 14px/1 monospace;letter-spacing:.22em;color:rgba(155,190,255,.82);text-transform:uppercase;margin-bottom:5px}
p{font:9px/1 monospace;letter-spacing:.28em;color:rgba(100,145,215,.48)}
</style>
</head>
<body>
<canvas id="c"></canvas>
<div class="hud"><h1>SPACE CODE</h1><p>INTELLIGENT AGENT CONSTELLATION</p></div>
<script>
const cv=document.getElementById('c'),X=cv.getContext('2d');
let W,H,cx,cy,R;
function resize(){W=cv.width=innerWidth;H=cv.height=innerHeight;cx=W/2;cy=H/2;R=Math.min(W,H)*.45}
addEventListener('resize',resize);resize();

const BGS=Array.from({length:500},()=>({
  x:(Math.random()-.5)*W*2.2,y:(Math.random()-.5)*H*2.2,
  r:Math.random()*.7+.08,a:Math.random()*.28+.04,ph:Math.random()*6.28
}));

const SS=[];
for(let arm=0;arm<2;arm++){
  for(let i=0;i<800;i++){
    const th=Math.pow(Math.random(),.5)*5+.4;
    const r=.16*Math.exp(.27*th)*R;
    const base=th+arm*Math.PI;
    const sp=Math.sqrt(r)*.55;
    const da=(Math.random()-.5)*sp/r;
    const dr=(Math.random()-.5)*sp*1.8;
    const t=th/5.4;
    const h=220+arm*38+t*55,s=Math.round(28+t*58),l=Math.round(48+t*42);
    SS.push({
      x:Math.cos(base+da)*(r+dr),
      y:Math.sin(base+da)*(r+dr)*.38,
      sz:.12+Math.random()*1.2+t*.5,
      al:(0.2+Math.random()*.8)*(0.28+t*.72),
      col:'hsl('+h+','+s+'%,'+l+'%)',
      ph:Math.random()*6.28,ps:Math.random()*.038+.004
    });
  }
}

const COR=Array.from({length:320},()=>{
  const a=Math.random()*6.28,d=Math.pow(Math.random(),1.7)*65;
  return{x:Math.cos(a)*d,y:Math.sin(a)*d*.38,r:Math.random()*2.2+.1,a:Math.random()*.88+.1};
});

let rot=0,frame=0;
function draw(){
  frame++;rot+=.00065;
  X.fillStyle='rgba(0,6,22,.2)';X.fillRect(0,0,W,H);
  X.save();X.translate(cx,cy);X.rotate(rot);

  for(const s of BGS){
    const a=s.a*(0.55+0.45*Math.sin(frame*.016+s.ph));
    X.beginPath();X.arc(s.x,s.y,s.r,0,6.28);
    X.fillStyle='rgba(185,205,255,'+a.toFixed(3)+')';X.fill();
  }

  let g=X.createRadialGradient(0,0,0,0,0,R*.22);
  g.addColorStop(0,'rgba(165,135,255,.22)');g.addColorStop(.5,'rgba(65,45,175,.08)');g.addColorStop(1,'rgba(0,0,0,0)');
  X.fillStyle=g;X.beginPath();X.arc(0,0,R*.22,0,6.28);X.fill();
  g=X.createRadialGradient(-R*.04,-R*.02,0,-R*.04,-R*.02,R*.17);
  g.addColorStop(0,'rgba(55,115,255,.11)');g.addColorStop(1,'rgba(0,0,0,0)');
  X.fillStyle=g;X.beginPath();X.arc(-R*.04,-R*.02,R*.17,0,6.28);X.fill();

  for(const s of COR){
    const a=s.a*(0.5+0.5*Math.sin(frame*.025+s.x*.07));
    X.beginPath();X.arc(s.x,s.y,s.r,0,6.28);
    X.fillStyle='rgba(218,212,255,'+a.toFixed(3)+')';X.fill();
  }

  for(const s of SS){
    s.ph+=s.ps;
    const a=s.al*(0.58+0.42*Math.sin(s.ph));
    X.beginPath();X.arc(s.x,s.y,s.sz,0,6.28);
    X.fillStyle=s.col.replace('hsl(','hsla(').replace(')',','+a.toFixed(3)+')');
    X.fill();
  }

  X.restore();requestAnimationFrame(draw);
}
draw();
</script>
</body>
</html>"""


def _is_artifact_request(message: str) -> bool:
        lowered = message.casefold()
        markers = (
                "artifact",
                "artefacto",
                "html_artifact",
                "svg_artifact",
                "react_artifact",
                "image_request",
                "diagrama",
                "diagram",
                "dashboard",
                "visualizacion",
                "visualización",
                "mockup",
                "renderizable",
                # visual / impression intent
                "visual",
                "sorprendeme",
                "impress",
                "chart",
                "grafica",
                "gráfica",
                "animacion",
                "animación",
                "animation",
                "graph",
                "plot",
                "galaxia",
                "galaxy",
                "espiral",
                "spiral",
                "beautiful",
                "bonito",
                "bello",
        )
        return any(marker in lowered for marker in markers)


def _has_typed_artifact_fence(response: str) -> bool:
        return bool(re.search(r"```(?:html_artifact|svg_artifact|react_artifact|image_request|mermaid)\b", response or "", flags=re.IGNORECASE))


_VISUAL_KEYWORDS = (
        "galaxy", "galaxia", "spiral", "espiral", "sorprendeme", "impress",
        "beautiful", "bonito", "bello", "animacion", "animación", "animation",
        "chart", "graph", "plot", "grafica", "gráfica", "stars", "estrellas",
        "space", "cosmos", "visual",
)


def _artifact_fallback_response(message: str, tool_calls: list[dict[str, object]]) -> str:
        lowered = message.casefold()
        if any(k in lowered for k in _VISUAL_KEYWORDS):
                return (
                        "Aquí tienes una visualización animada generada en el Artifact Studio de Space Code.\n\n"
                        "```html_artifact\n" + _galaxy_canvas_html() + "\n```"
                )

        completed_nemo = [
                str(item.get("name") or item.get("tool_name") or "")
                for item in tool_calls
                if str(item.get("name") or "").startswith("nemo_memory.") and str(item.get("status") or "") == "completed"
        ]
        completed_nemo = [name for name in completed_nemo if name]
        completed_text = ", ".join(completed_nemo) or "nemo_memory.context_bootstrap"
        escaped_completed = html.escape(completed_text)
        escaped_request = html.escape(message[:320])
        rows = "\n".join(
                f"<li><span>{html.escape(str(item.get('name') or item.get('tool_name') or 'tool'))}</span><b>{html.escape(str(item.get('status') or 'unknown'))}</b></li>"
                for item in tool_calls[:10]
        )
        return f"""NEMO MCP fue usado por el backend con estas llamadas reales completadas: {completed_text}.

```html_artifact
<!-- ARTIFACT:Space Code NEMO MCP Verification:html -->
<!doctype html>
<html lang="es">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Space Code Operational Flow</title>
    <style>
        :root {{ color-scheme: dark; font-family: Inter, Segoe UI, system-ui, sans-serif; background: #050907; }}
        * {{ box-sizing: border-box; }}
        body {{ margin: 0; min-height: 100vh; background: #050907; color: #e9fff8; }}
        main {{ min-height: 100vh; display: grid; grid-template-rows: auto 1fr auto; gap: 22px; padding: clamp(18px, 4vw, 44px); }}
        header {{ display: flex; align-items: end; justify-content: space-between; gap: 18px; border-bottom: 1px solid #12382f; padding-bottom: 18px; }}
        h1 {{ margin: 0; font-size: clamp(28px, 5vw, 58px); letter-spacing: 0; color: #70ffd8; }}
        .status {{ color: #9aff6c; font-weight: 700; text-transform: uppercase; font-size: 13px; }}
        .grid {{ display: grid; grid-template-columns: repeat(4, minmax(150px, 1fr)); gap: 14px; align-content: center; }}
        .node {{ min-height: 150px; border: 1px solid #1b5b50; border-radius: 8px; background: linear-gradient(180deg, #0b1714, #07100d); padding: 18px; position: relative; box-shadow: 0 0 0 1px #041a15 inset; }}
        .node strong {{ display: block; color: #8ffff0; font-size: 20px; margin-bottom: 10px; }}
        .node p {{ margin: 0; color: #b9d8cf; line-height: 1.45; }}
        .node::after {{ content: '->'; position: absolute; right: -18px; top: 50%; transform: translateY(-50%); color: #55ff99; font-weight: 900; }}
        .node:last-child::after {{ content: ''; }}
        .trace {{ display: grid; grid-template-columns: 1.1fr .9fr; gap: 16px; }}
        section {{ border: 1px solid #12382f; border-radius: 8px; padding: 16px; background: #07110f; }}
        h2 {{ margin: 0 0 12px; color: #55ff99; font-size: 16px; letter-spacing: 0; }}
        ul {{ list-style: none; padding: 0; margin: 0; display: grid; gap: 8px; }}
        li {{ display: flex; justify-content: space-between; gap: 12px; border-bottom: 1px solid #0f2b25; padding-bottom: 8px; color: #c7eee5; }}
        li b {{ color: #9aff6c; }}
        code {{ color: #70ffd8; overflow-wrap: anywhere; }}
        footer {{ color: #7fb8aa; font-size: 13px; }}
        @media (max-width: 860px) {{ .grid, .trace {{ grid-template-columns: 1fr; }} .node::after {{ content: 'down'; right: 18px; top: auto; bottom: -18px; transform: none; }} }}
    </style>
</head>
<body>
    <main>
        <header>
            <div><div class="status">Operativo con NEMO MCP real</div><h1>Space Code Verification</h1></div>
            <div><code>{escaped_completed}</code></div>
        </header>
        <div class="grid" aria-label="flujo operacional">
            <article class="node"><strong>Space Code</strong><p>Recibe la solicitud y coordina el agente local.</p></article>
            <article class="node"><strong>NEMO MCP</strong><p>Carga contexto, portfolio y persiste la conversacion.</p></article>
            <article class="node"><strong>Artifact Studio</strong><p>Convierte el bloque typed fence en una vista renderizable.</p></article>
            <article class="node"><strong>Usuario</strong><p>Inspecciona respuesta, trazas y artifact en una sola superficie.</p></article>
        </div>
        <div class="trace">
            <section><h2>Tool Trace</h2><ul>{rows}</ul></section>
            <section><h2>Solicitud verificada</h2><p><code>{escaped_request}</code></p></section>
        </div>
        <footer>Fallback deterministico activado solo porque el modelo local no emitio un artifact fence valido.</footer>
    </main>
</body>
</html>
```
"""


def _write_apply_memory(result: MergeApplyResult, memory_db: Path | None) -> None:
    if memory_db is None:
        return
    adapter = PersistentNemoAdapter(PersistentMemoryStore(memory_db))
    adapter.call(
        NemoLifecyclePhase.REVIEW,
        "store_conversation",
        summary=(
            f"Space Code apply completed task={result.task_id} "
            f"run={result.run_id} applied_files={','.join(result.applied_files)}"
        ),
        topic="Space Code Review Gate",
        tags=(result.task_id, result.run_id, "spacecode", "apply", "review_to_main"),
        atom_type=MemoryAtomType.DECISION.value,
        source_scope="spacecode",
        importance=9,
    )


def _memory_atom_payload(atom: Any) -> dict[str, object]:
    return {
        "id": atom.id,
        "type": atom.atom.atom_type.value,
        "content": atom.atom.content,
        "source_scope": atom.atom.source_scope,
        "topic": atom.topic,
        "tags": list(atom.tags),
        "importance": atom.importance,
        "evidence_handle": atom.atom.evidence_handle,
        "access_count": atom.access_count,
        "useful_count": atom.useful_count,
        "not_useful_count": atom.not_useful_count,
        "created_at": atom.created_at,
        "last_accessed_at": atom.last_accessed_at,
    }


def _evidence_payload(evidence: Any) -> dict[str, object]:
    return {
        "handle": evidence.handle,
        "compact_claim": evidence.compact_claim,
        "source_task_id": evidence.source_task_id,
        "source_run_id": evidence.source_run_id,
        "content_hash": evidence.content_hash,
        "created_at": evidence.created_at,
        "expires_at": evidence.expires_at,
        "retrieval_count": evidence.retrieval_count,
    }


def _selected_nemo_payload(payload: dict[str, object]) -> dict[str, Any] | None:
    source_json = payload.get("source_json")
    if not isinstance(source_json, str) or not source_json:
        return None
    selected = load_headless_result_json(source_json)
    task = selected.get("task", {}) if isinstance(selected.get("task"), dict) else {}
    run = selected.get("run", {}) if isinstance(selected.get("run"), dict) else {}
    return {
        "source_json": source_json,
        "task_id": task.get("id"),
        "run_id": run.get("id"),
        "objective": task.get("objective"),
        "portfolio": selected.get("portfolio") if isinstance(selected.get("portfolio"), dict) else None,
        "memory_traces": selected.get("memory_traces") if isinstance(selected.get("memory_traces"), list) else [],
    }


def _hidden_run_source_json(settings: dict[str, object]) -> set[str]:
    raw = settings.get("hidden_run_source_json")
    if not isinstance(raw, list):
        return set()
    return {str(item) for item in raw if isinstance(item, str) and item}


def _run_timestamp_from_state_run(run: dict[str, object]) -> float | None:
    source_json = run.get("source_json")
    if isinstance(source_json, str) and source_json:
        try:
            path = Path(source_json)
            if path.exists():
                return float(path.stat().st_mtime)
        except OSError:
            pass

    for key in ("run_id", "task_id", "source_json"):
        value = run.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
        match = re.search(r"(\d{10,13})", value)
        if match:
            epoch = int(match.group(1))
            return float(epoch * 1000 if len(match.group(1)) == 10 else epoch) / 1000.0
    return None


def api_state(config: MissionControlServerConfig, jobs: HandoffJobManager | None = None) -> dict[str, object]:
    settings = _load_settings(config)
    recent = tuple(str(item) for item in settings.get("recent_repos", []) if isinstance(item, str))
    state = build_mission_control_state(config.repo_path, config.runtimes_path, settings=settings, recent_repos=recent)
    hidden_source_json = _hidden_run_source_json(settings)
    if hidden_source_json:
        runs = state.get("runs")
        if isinstance(runs, list):
            state["runs"] = [
                run
                for run in runs
                if not (isinstance(run, dict) and isinstance(run.get("source_json"), str) and run.get("source_json") in hidden_source_json)
            ]
        approval_queue = state.get("approval_queue")
        if isinstance(approval_queue, list):
            state["approval_queue"] = [
                run
                for run in approval_queue
                if not (isinstance(run, dict) and isinstance(run.get("source_json"), str) and run.get("source_json") in hidden_source_json)
            ]
    state["jobs"] = jobs.list() if jobs is not None else []
    return state


def api_runs_cleanup(server: "MissionControlHttpServer", payload: dict[str, object]) -> dict[str, object]:
    mode = str(payload.get("mode") or "old").strip().lower()
    if mode not in {"old", "all"}:
        raise _bad_request("mode must be one of: old, all", error_code="invalid_mode")

    older_than_hours = float(payload.get("older_than_hours") or 24)
    keep_latest = int(payload.get("keep_latest") or 8)
    if older_than_hours < 1:
        older_than_hours = 1
    if keep_latest < 0:
        keep_latest = 0

    state = api_state(server.config, server.jobs)
    runs = state.get("runs") if isinstance(state.get("runs"), list) else []
    run_entries = [run for run in runs if isinstance(run, dict)]
    if not run_entries:
        return {
            "ok": True,
            "mode": mode,
            "removed_count": 0,
            "removed_source_json": [],
            "state": state,
        }

    removable: list[str] = []
    if mode == "all":
        removable = [str(run.get("source_json")) for run in run_entries if isinstance(run.get("source_json"), str)]
    else:
        now_ts = datetime.now(timezone.utc).timestamp()
        stamped: list[tuple[dict[str, object], float]] = []
        unstamped: list[dict[str, object]] = []
        for run in run_entries:
            ts = _run_timestamp_from_state_run(run)
            if ts is None:
                unstamped.append(run)
            else:
                stamped.append((run, ts))

        threshold_seconds = older_than_hours * 3600
        removable_by_age = {
            str(run.get("source_json"))
            for run, ts in stamped
            if (now_ts - ts) > threshold_seconds and isinstance(run.get("source_json"), str)
        }

        runs_by_recency = sorted(stamped, key=lambda item: item[1], reverse=True)
        ranked_sources = [str(run.get("source_json")) for run, _ in runs_by_recency if isinstance(run.get("source_json"), str)]
        ranked_sources.extend(str(run.get("source_json")) for run in unstamped if isinstance(run.get("source_json"), str))
        removable_by_count = set(ranked_sources[keep_latest:]) if len(ranked_sources) > keep_latest else set()

        removable = sorted(removable_by_age.union(removable_by_count))

    removable_set = {item for item in removable if item}
    if not removable_set:
        return {
            "ok": True,
            "mode": mode,
            "removed_count": 0,
            "removed_source_json": [],
            "state": state,
        }

    settings = _load_settings(server.config)
    existing_hidden = _hidden_run_source_json(settings)
    merged_hidden = sorted(existing_hidden.union(removable_set))
    settings["hidden_run_source_json"] = merged_hidden
    _save_settings(server.config, settings)

    return {
        "ok": True,
        "mode": mode,
        "removed_count": len(removable_set),
        "removed_source_json": sorted(removable_set),
        "state": api_state(server.config, server.jobs),
    }


def api_settings(server: "MissionControlHttpServer", payload: dict[str, object]) -> dict[str, object]:
    settings = _load_settings(server.config)
    allowed = {
        "model_base_url",
        "default_model",
        "model_roles",
        "provider",
        "memory_db",
        "nemo_mcp_url",
        "runtime_path",
        "timeout_seconds",
        "max_runtime_minutes",
        "heartbeat_minutes",
        "max_heartbeats",
        "token_budget",
        "context_window_tokens",
        "chat_max_tokens",
        "image_gen_backend",
        "image_gen_url",
        "pause_after_minutes",
        "plan_minutes",
        "execute_minutes",
        "review_minutes",
        "repair_time_limit_seconds",
        "validation_time_budget_seconds",
        "validation_escalation_mode",
        "real_validation",
        "validation_policy",
        "workflow_mode",
        "quality_core",
    }
    unknown = {key for key in payload if key not in allowed and key != "repo_path"}
    if unknown:
        raise _bad_request(f"unknown setting key(s): {', '.join(sorted(unknown))}", error_code="invalid_setting_key")
    if "provider" in payload:
        _provider_mode(payload)
    if "workflow_mode" in payload:
        _workflow_mode(payload)
    if "validation_policy" in payload and payload["validation_policy"] not in {"none", "smoke", "targeted", "full"}:
        raise _bad_request("validation_policy must be none, smoke, targeted, or full", error_code="invalid_validation_policy")
    for _numeric_key in (
        "timeout_seconds",
        "max_runtime_minutes",
        "heartbeat_minutes",
        "max_heartbeats",
        "token_budget",
        "context_window_tokens",
        "chat_max_tokens",
        "plan_minutes",
        "execute_minutes",
        "review_minutes",
        "repair_time_limit_seconds",
        "validation_time_budget_seconds",
    ):
        if _numeric_key in payload:
            try:
                _numeric_val = float(payload[_numeric_key])  # type: ignore[arg-type]
            except (TypeError, ValueError):
                raise _bad_request(f"{_numeric_key} must be numeric", error_code="invalid_setting_value")
            if _numeric_val <= 0:
                raise _bad_request(f"{_numeric_key} must be positive", error_code="invalid_setting_value")
    effective_provider = str(payload.get("provider") or settings.get("provider") or "subprocess")
    if effective_provider not in {"subprocess", "fake"}:
        raise _bad_request("provider must be subprocess or fake", error_code="invalid_provider")
    effective_timeout = float(payload.get("timeout_seconds") or settings.get("timeout_seconds") or 300.0)
    _validate_provider_timeout(effective_provider, effective_timeout, error_code="invalid_setting_value")
    if "pause_after_minutes" in payload and payload.get("pause_after_minutes") is not None:
        try:
            pause_minutes = float(payload["pause_after_minutes"])
        except (TypeError, ValueError):
            raise _bad_request("pause_after_minutes must be numeric", error_code="invalid_setting_value")
        if pause_minutes <= 0:
            raise _bad_request("pause_after_minutes must be positive", error_code="invalid_setting_value")
    if "validation_escalation_mode" in payload and not isinstance(payload.get("validation_escalation_mode"), bool):
        raise _bad_request("validation_escalation_mode must be boolean", error_code="invalid_setting_value")
    if "real_validation" in payload and not isinstance(payload.get("real_validation"), bool):
        raise _bad_request("real_validation must be boolean", error_code="invalid_setting_value")
    if "image_gen_backend" in payload and str(payload.get("image_gen_backend") or "").strip().lower() not in {"auto", "automatic1111", "a1111", "comfyui"}:
        raise _bad_request("image_gen_backend must be auto, automatic1111, a1111, or comfyui", error_code="invalid_setting_value")
    if "model_roles" in payload:
        settings["model_roles"] = _validate_model_roles_payload(payload.get("model_roles"))
    elif "default_model" in payload:
        default_model = str(payload.get("default_model") or "").strip()
        if default_model:
            settings["model_roles"] = {role: default_model for role in MODEL_ROLES}
    for key in allowed:
        if key in payload:
            settings[key] = payload[key]
    settings["nemo_mcp_url"] = _normalize_nemo_mcp_url(server.config, settings.get("nemo_mcp_url"))
    repo_path = str(payload.get("repo_path") or server.config.repo_path)
    settings["recent_repos"] = _recent_repos(tuple(str(item) for item in settings.get("recent_repos", [])), repo_path)
    next_config = server.config.with_runtime_settings({**settings, "repo_path": repo_path})
    settings["repo_path"] = str(next_config.repo_path)
    settings["runtime_path"] = str(next_config.runtimes_path)
    settings["memory_db"] = str(next_config.memory_db) if next_config.memory_db else ""
    _save_settings(next_config, settings)
    server.config = next_config
    return {"ok": True, "settings": settings, "state": api_state(server.config, server.jobs)}


def api_repo_open(server: "MissionControlHttpServer", payload: dict[str, object]) -> dict[str, object]:
    repo_path = payload.get("repo_path")
    if not isinstance(repo_path, str) or not repo_path.strip():
        raise ValueError("repo_path is required")
    repo = Path(repo_path).resolve()
    if not repo.exists() or not repo.is_dir():
        raise FileNotFoundError(str(repo))
    if not _is_git_repo(repo):
        raise ValueError("selected path is not a git repository")
    settings = _load_settings(server.config)
    settings["recent_repos"] = _recent_repos(tuple(str(item) for item in settings.get("recent_repos", [])), str(repo))
    next_config = server.config.with_runtime_settings({**settings, "repo_path": str(repo)})
    _save_settings(next_config, {**settings, "repo_path": str(repo), "runtime_path": str(next_config.runtimes_path), "memory_db": str(next_config.memory_db) if next_config.memory_db else ""})
    server.config = next_config
    return {"ok": True, "repo": {"path": str(repo), "is_git_repo": True}, "state": api_state(server.config, server.jobs)}


def api_repo_clone(server: "MissionControlHttpServer", payload: dict[str, object]) -> dict[str, object]:
    url = payload.get("url")
    destination = payload.get("destination")
    if not isinstance(url, str) or not url.strip():
        raise _bad_request("url is required", error_code="missing_clone_url")
    if not isinstance(destination, str) or not destination.strip():
        raise _bad_request("destination is required", error_code="missing_clone_destination")
    if any(char in url for char in ("\n", "\r", "\x00")):
        raise _bad_request("url contains invalid characters", error_code="invalid_clone_url")
    target = Path(destination).resolve()
    if target.exists() and any(target.iterdir()):
        raise ValueError("destination already exists and is not empty")
    target.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(["git", "clone", url, str(target)], cwd=server.config.repo_path, text=True, capture_output=True, timeout=600)
    if completed.returncode != 0:
        raise ValueError(completed.stderr.strip() or completed.stdout.strip() or "git clone failed")
    return api_repo_open(server, {"repo_path": str(target)})


def api_repo_pick_folder(server: "MissionControlHttpServer", payload: dict[str, object]) -> dict[str, object]:
    if os.name != "nt":
        raise _bad_request("folder picker is only supported on Windows", error_code="folder_picker_unsupported")
    picker_script = (
        "Add-Type -AssemblyName System.Windows.Forms | Out-Null; "
        "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog; "
        "$dialog.Description = 'Select workspace folder'; "
        "$dialog.UseDescriptionForTitle = $true; "
        "$dialog.ShowNewFolderButton = $true; "
        "$result = $dialog.ShowDialog(); "
        "if ($result -eq [System.Windows.Forms.DialogResult]::OK -and $dialog.SelectedPath) { Write-Output $dialog.SelectedPath }"
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-STA", "-Command", picker_script],
        text=True,
        capture_output=True,
        timeout=120,
        cwd=server.config.repo_path,
    )
    if completed.returncode != 0:
        raise ValueError(completed.stderr.strip() or completed.stdout.strip() or "folder picker failed")
    selected_path = ""
    if completed.stdout:
        lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
        if lines:
            selected_path = lines[-1]
    return {"ok": True, "path": selected_path or None}


def api_terminal_run(config: MissionControlServerConfig, payload: dict[str, object]) -> dict[str, object]:
    command = payload.get("command")
    if not isinstance(command, str) or not command.strip():
        raise _bad_request("command is required", error_code="missing_terminal_command")
    timeout_raw = payload.get("timeout_seconds")
    try:
        timeout_seconds = int(timeout_raw) if timeout_raw is not None else 30
    except (TypeError, ValueError):
        raise _bad_request("timeout_seconds must be numeric", error_code="invalid_terminal_timeout")
    timeout_seconds = max(1, min(timeout_seconds, 300))
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=config.repo_path,
            text=True,
            capture_output=True,
            shell=True,
            timeout=timeout_seconds,
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        return {
            "ok": completed.returncode == 0,
            "command": command,
            "cwd": str(config.repo_path),
            "exit_code": completed.returncode,
            "duration_ms": duration_ms,
            "stdout": _trim_output(completed.stdout or ""),
            "stderr": _trim_output(completed.stderr or ""),
        }
    except subprocess.TimeoutExpired as error:
        duration_ms = int((time.perf_counter() - started) * 1000)
        return {
            "ok": False,
            "command": command,
            "cwd": str(config.repo_path),
            "exit_code": None,
            "duration_ms": duration_ms,
            "stdout": _trim_output(error.stdout or ""),
            "stderr": _trim_output(error.stderr or ""),
            "error": "terminal command timed out",
        }


def api_browser_state(config: MissionControlServerConfig) -> dict[str, object]:
    settings = _load_settings(config)
    history = settings.get("browser_history")
    if not isinstance(history, list):
        history = []
    search_history = settings.get("browser_search_history")
    if not isinstance(search_history, list):
        search_history = []
    return {
        "ok": True,
        "homepage": str(settings.get("browser_homepage") or ""),
        "last_url": str(settings.get("browser_last_url") or ""),
        "history": [str(item) for item in history if isinstance(item, str)][:20],
        "search_query": str(settings.get("browser_search_query") or ""),
        "search_history": [str(item) for item in search_history if isinstance(item, str)][:20],
    }


def api_browser_open(server: "MissionControlHttpServer", payload: dict[str, object]) -> dict[str, object]:
    url = payload.get("url")
    if not isinstance(url, str) or not url.strip():
        raise _bad_request("url is required", error_code="missing_browser_url")
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise _bad_request("url must be a valid http(s) URL", error_code="invalid_browser_url")
    settings = _load_settings(server.config)
    current_history = settings.get("browser_history")
    history = [str(item) for item in current_history if isinstance(item, str)] if isinstance(current_history, list) else []
    next_history = [url.strip(), *[item for item in history if item != url.strip()]]
    settings["browser_last_url"] = url.strip()
    settings["browser_history"] = next_history[:20]
    _save_settings(server.config, settings)
    opened = False
    try:
        opened = bool(webbrowser.open(url.strip(), new=0, autoraise=False))
    except Exception:
        opened = False
    return {
        "ok": True,
        "url": url.strip(),
        "opened": opened,
        "history": settings["browser_history"],
    }


def _playwright_browser_search(query: str, *, max_results: int, timeout_seconds: int) -> dict[str, object]:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except Exception as error:  # pragma: no cover - depends on local environment
        raise ValueError(
            "Playwright no esta disponible. Instala con: pip install playwright ; python -m playwright install chromium"
        ) from error

    search_url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
    deadline_ms = max(5_000, min(timeout_seconds * 1000, 60_000))

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1280, "height": 720})
            page = context.new_page()
            page.goto(search_url, wait_until="domcontentloaded", timeout=deadline_ms)
            page.wait_for_selector("a.result__a, .result__title a, article h2 a", timeout=deadline_ms)
            anchors = page.query_selector_all("a.result__a, .result__title a, article h2 a")

            results: list[dict[str, str]] = []
            seen_urls: set[str] = set()
            for anchor in anchors:
                if len(results) >= max_results:
                    break
                title = (anchor.inner_text() or "").strip()
                href = (anchor.get_attribute("href") or "").strip()
                if not href or href in seen_urls:
                    continue
                snippet = (
                    anchor.evaluate(
                        """
                        (el) => {
                          const root = el.closest('.result') || el.closest('article') || el.parentElement;
                          if (!root) return '';
                          const node = root.querySelector('.result__snippet, .result-snippet, .snippet, p');
                          return node ? node.textContent || '' : '';
                        }
                        """
                    )
                    or ""
                ).strip()
                if not title:
                    title = href
                results.append({"title": title, "url": href, "snippet": snippet})
                seen_urls.add(href)

            context.close()
            browser.close()
            return {"engine": "playwright-chromium", "search_url": search_url, "results": results}
    except PlaywrightTimeoutError as error:
        raise ValueError(f"La busqueda web excedio el tiempo limite ({timeout_seconds}s)") from error
    except Exception as error:
        raise ValueError(f"No se pudo completar la busqueda con Playwright Chromium: {error}") from error


def api_browser_search(server: "MissionControlHttpServer", payload: dict[str, object]) -> dict[str, object]:
    query = payload.get("query")
    if not isinstance(query, str) or not query.strip():
        raise _bad_request("query is required", error_code="missing_browser_query")
    query_value = query.strip()

    max_results_raw = payload.get("max_results")
    timeout_raw = payload.get("timeout_seconds")
    try:
        max_results = int(max_results_raw) if max_results_raw is not None else 6
    except (TypeError, ValueError):
        raise _bad_request("max_results must be numeric", error_code="invalid_browser_search_options")
    try:
        timeout_seconds = int(timeout_raw) if timeout_raw is not None else 20
    except (TypeError, ValueError):
        raise _bad_request("timeout_seconds must be numeric", error_code="invalid_browser_search_options")

    max_results = max(1, min(max_results, 10))
    timeout_seconds = max(5, min(timeout_seconds, 60))

    result = _playwright_browser_search(query_value, max_results=max_results, timeout_seconds=timeout_seconds)
    entries = result.get("results") if isinstance(result.get("results"), list) else []
    normalized_results: list[dict[str, str]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        snippet = str(item.get("snippet") or "").strip()
        if not url:
            continue
        normalized_results.append({"title": title or url, "url": url, "snippet": snippet})

    search_url = str(result.get("search_url") or "")
    settings = _load_settings(server.config)
    current_search_history = settings.get("browser_search_history")
    search_history = [str(item) for item in current_search_history if isinstance(item, str)] if isinstance(current_search_history, list) else []
    current_history = settings.get("browser_history")
    history = [str(item) for item in current_history if isinstance(item, str)] if isinstance(current_history, list) else []
    settings["browser_search_query"] = query_value
    settings["browser_search_history"] = [query_value, *[item for item in search_history if item != query_value]][:20]
    if search_url:
        settings["browser_last_url"] = search_url
        settings["browser_history"] = [search_url, *[item for item in history if item != search_url]][:20]
    _save_settings(server.config, settings)

    return {
        "ok": True,
        "query": query_value,
        "engine": str(result.get("engine") or "playwright-chromium"),
        "search_url": search_url,
        "results": normalized_results,
        "history": settings.get("browser_history", []),
        "search_history": settings.get("browser_search_history", []),
    }


def api_extensions_get(config: MissionControlServerConfig) -> dict[str, object]:
    settings = _load_settings(config)
    extensions = _normalized_extensions(settings.get("extensions"))
    return {"ok": True, "extensions": extensions}


def api_extensions_update(server: "MissionControlHttpServer", payload: dict[str, object]) -> dict[str, object]:
    action = str(payload.get("action") or "toggle")
    name_raw = payload.get("name")
    name = str(name_raw).strip().lower() if isinstance(name_raw, str) else ""
    if action not in {"toggle", "register", "remove"}:
        raise _bad_request("action must be toggle/register/remove", error_code="invalid_extensions_action")
    if not name:
        raise _bad_request("name is required", error_code="missing_extension_name")
    settings = _load_settings(server.config)
    extensions = _normalized_extensions(settings.get("extensions"))
    by_name = {str(item["name"]): item for item in extensions}
    if action == "remove":
        by_name.pop(name, None)
    elif action == "register":
        by_name[name] = {
            "name": name,
            "enabled": bool(payload.get("enabled", True)),
            "version": str(payload.get("version") or "custom"),
        }
    else:
        current = by_name.get(name)
        enabled_raw = payload.get("enabled")
        enabled = bool(enabled_raw) if enabled_raw is not None else not bool(current and current.get("enabled"))
        if current is None:
            by_name[name] = {"name": name, "enabled": enabled, "version": "custom"}
        else:
            current["enabled"] = enabled
            by_name[name] = current
    settings["extensions"] = sorted(by_name.values(), key=lambda item: str(item["name"]))
    _save_settings(server.config, settings)
    return {"ok": True, "extensions": settings["extensions"]}


def _ensure_git_repo(config: MissionControlServerConfig) -> None:
    if not _is_git_repo(config.repo_path):
        raise _bad_request("current workspace is not a git repository", error_code="not_git_repo")


def _run_git(config: MissionControlServerConfig, args: list[str], *, timeout: int = 60, check: bool = True) -> subprocess.CompletedProcess[str]:
    _ensure_git_repo(config)
    completed = subprocess.run(
        ["git", *args],
        cwd=config.repo_path,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if check and completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or f"git {' '.join(args)} failed"
        raise ValueError(message)
    return completed


def api_git_status(config: MissionControlServerConfig) -> dict[str, object]:
    branch_info = _run_git(config, ["status", "--porcelain=v1", "--branch"], timeout=30)
    lines = [line for line in branch_info.stdout.splitlines() if line]
    branch = ""
    ahead = 0
    behind = 0
    entries: list[dict[str, object]] = []
    for line in lines:
        if line.startswith("##"):
            head = line[2:].strip()
            branch = head.split("...")[0].strip() if head else ""
            if "ahead " in line:
                try:
                    ahead = int(line.split("ahead ", 1)[1].split("]", 1)[0].split(",", 1)[0])
                except (TypeError, ValueError, IndexError):
                    ahead = 0
            if "behind " in line:
                try:
                    behind = int(line.split("behind ", 1)[1].split("]", 1)[0].split(",", 1)[0])
                except (TypeError, ValueError, IndexError):
                    behind = 0
            continue
        if len(line) < 4:
            continue
        xy = line[:2]
        path = line[3:]
        original_path = None
        if " -> " in path:
            original_path, path = path.split(" -> ", 1)
        entries.append(
            {
                "xy": xy,
                "path": path,
                "original_path": original_path,
                "staged": xy[0] != " ",
                "unstaged": xy[1] != " ",
            }
        )
    return {
        "ok": True,
        "repo_path": str(config.repo_path),
        "branch": branch,
        "ahead": ahead,
        "behind": behind,
        "entries": entries,
    }


def api_git_diff(config: MissionControlServerConfig, payload: dict[str, object]) -> dict[str, object]:
    staged = bool(payload.get("staged", False))
    path = payload.get("path")
    args = ["diff", "--staged"] if staged else ["diff"]
    if isinstance(path, str) and path.strip():
        args.extend(["--", path.strip()])
    completed = _run_git(config, args, timeout=60, check=False)
    return {
        "ok": completed.returncode == 0,
        "staged": staged,
        "path": path if isinstance(path, str) else "",
        "diff": _trim_output(completed.stdout or "", limit=120_000),
        "stderr": _trim_output(completed.stderr or "", limit=12_000),
    }


def api_git_stage(config: MissionControlServerConfig, payload: dict[str, object]) -> dict[str, object]:
    path = payload.get("path")
    if not isinstance(path, str) or not path.strip():
        raise _bad_request("path is required", error_code="missing_git_path")
    stage = bool(payload.get("stage", True))
    if stage:
        _run_git(config, ["add", "--", path.strip()], timeout=30)
    else:
        _run_git(config, ["restore", "--staged", "--", path.strip()], timeout=30)
    return {"ok": True, "path": path.strip(), "staged": stage, "status": api_git_status(config)}


def api_git_commit(config: MissionControlServerConfig, payload: dict[str, object]) -> dict[str, object]:
    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        raise _bad_request("message is required", error_code="missing_commit_message")
    completed = _run_git(config, ["commit", "-m", message.strip()], timeout=90, check=False)
    return {
        "ok": completed.returncode == 0,
        "message": message.strip(),
        "stdout": _trim_output(completed.stdout or "", limit=20_000),
        "stderr": _trim_output(completed.stderr or "", limit=20_000),
        "exit_code": completed.returncode,
        "status": api_git_status(config),
    }


def api_git_branches(config: MissionControlServerConfig) -> dict[str, object]:
    completed = _run_git(config, ["branch", "--list"], timeout=30)
    branches: list[dict[str, object]] = []
    current = ""
    for raw in completed.stdout.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        is_current = line.startswith("*")
        name = line[1:].strip() if is_current else line.strip()
        if is_current:
            current = name
        branches.append({"name": name, "current": is_current})
    return {"ok": True, "current": current, "branches": branches}


def api_git_remotes(config: MissionControlServerConfig) -> dict[str, object]:
    completed = _run_git(config, ["remote", "-v"], timeout=30, check=False)
    remotes: dict[str, dict[str, str]] = {}
    for raw in completed.stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        name = parts[0]
        url = parts[1]
        mode = parts[2].strip("()")
        current = remotes.get(name, {"name": name, "fetch": "", "push": ""})
        if mode == "fetch":
            current["fetch"] = url
        elif mode == "push":
            current["push"] = url
        remotes[name] = current
    upstream = ""
    upstream_branch = ""
    upstream_cmd = _run_git(config, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], timeout=30, check=False)
    if upstream_cmd.returncode == 0:
        upstream_ref = (upstream_cmd.stdout or "").strip()
        if "/" in upstream_ref:
            upstream, upstream_branch = upstream_ref.split("/", 1)
    return {
        "ok": True,
        "remotes": sorted(remotes.values(), key=lambda item: item["name"]),
        "default_remote": upstream or ("origin" if "origin" in remotes else ""),
        "default_branch": upstream_branch,
    }


def _extract_hunk_patch(diff_text: str, hunk_header: str) -> str | None:
    if not diff_text.strip() or not hunk_header.strip():
        return None
    lines = diff_text.splitlines(keepends=True)
    header_lines: list[str] = []
    hunks: list[list[str]] = []
    current_hunk: list[str] | None = None
    for line in lines:
        if line.startswith("@@ "):
            if current_hunk is not None:
                hunks.append(current_hunk)
            current_hunk = [line]
            continue
        if current_hunk is None:
            header_lines.append(line)
        else:
            current_hunk.append(line)
    if current_hunk is not None:
        hunks.append(current_hunk)
    target = hunk_header.strip()
    for hunk in hunks:
        if not hunk:
            continue
        if hunk[0].strip() != target:
            continue
        patch = "".join(header_lines + hunk)
        if not patch.endswith("\n"):
            patch += "\n"
        return patch
    return None


def api_git_stage_hunk(config: MissionControlServerConfig, payload: dict[str, object]) -> dict[str, object]:
    path = payload.get("path")
    hunk_header = payload.get("hunk_header")
    if not isinstance(path, str) or not path.strip():
        raise _bad_request("path is required", error_code="missing_git_path")
    if not isinstance(hunk_header, str) or not hunk_header.strip():
        raise _bad_request("hunk_header is required", error_code="missing_git_hunk")
    stage = bool(payload.get("stage", True))
    diff_completed = _run_git(
        config,
        ["diff", "--staged", "--", path.strip()] if not stage else ["diff", "--", path.strip()],
        timeout=60,
        check=False,
    )
    patch = _extract_hunk_patch(diff_completed.stdout or "", hunk_header)
    if not patch:
        raise _bad_request("requested hunk was not found in current diff", error_code="missing_git_hunk")
    apply_args = ["git", "apply", "--cached", "--recount", "--whitespace=nowarn", "-"]
    if not stage:
        apply_args.insert(3, "-R")
    applied = subprocess.run(
        apply_args,
        cwd=config.repo_path,
        text=True,
        input=patch,
        capture_output=True,
        timeout=45,
    )
    if applied.returncode != 0:
        message = applied.stderr.strip() or applied.stdout.strip() or "git apply failed for selected hunk"
        raise ValueError(message)
    return {
        "ok": True,
        "path": path.strip(),
        "hunk_header": hunk_header.strip(),
        "staged": stage,
        "status": api_git_status(config),
    }


def api_git_checkout(config: MissionControlServerConfig, payload: dict[str, object]) -> dict[str, object]:
    branch = payload.get("branch")
    if not isinstance(branch, str) or not branch.strip():
        raise _bad_request("branch is required", error_code="missing_git_branch")
    create = bool(payload.get("create", False))
    args = ["checkout", "-b", branch.strip()] if create else ["checkout", branch.strip()]
    completed = _run_git(config, args, timeout=45, check=False)
    return {
        "ok": completed.returncode == 0,
        "branch": branch.strip(),
        "created": create,
        "stdout": _trim_output(completed.stdout or "", limit=12_000),
        "stderr": _trim_output(completed.stderr or "", limit=12_000),
        "exit_code": completed.returncode,
        "branches": api_git_branches(config),
    }


def api_git_sync(config: MissionControlServerConfig, payload: dict[str, object]) -> dict[str, object]:
    direction = payload.get("direction")
    if direction not in {"pull", "push"}:
        raise _bad_request("direction must be pull or push", error_code="invalid_git_direction")
    rebase = bool(payload.get("rebase", False))
    remote = payload.get("remote")
    branch = payload.get("branch")
    args = ["pull", "--rebase"] if direction == "pull" and rebase else [str(direction)]
    if isinstance(remote, str) and remote.strip():
        args.append(remote.strip())
        if isinstance(branch, str) and branch.strip():
            args.append(branch.strip())
    completed = _run_git(config, args, timeout=180, check=False)
    return {
        "ok": completed.returncode == 0,
        "direction": direction,
        "rebase": rebase,
        "remote": remote if isinstance(remote, str) else "",
        "branch": branch if isinstance(branch, str) else "",
        "stdout": _trim_output(completed.stdout or "", limit=40_000),
        "stderr": _trim_output(completed.stderr or "", limit=40_000),
        "exit_code": completed.returncode,
        "status": api_git_status(config),
    }


def api_applies(config: MissionControlServerConfig) -> dict[str, object]:
    items: list[dict[str, object]] = []
    if config.apply_results_path.exists():
        for path in sorted(config.apply_results_path.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            items.append({"path": str(path), "created_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(), **payload})
    return {"ok": True, "applies": items[:30]}


def _parse_iso_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _parse_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def api_orphan_jobs(server: "MissionControlHttpServer", payload: dict[str, object]) -> dict[str, object]:
    dry_run = bool(payload.get("dry_run", True))
    max_age_minutes = int(payload.get("max_age_minutes") or 30)
    grace_seconds = max(60, max_age_minutes * 60)
    in_memory = server.jobs.find_orphans(grace_seconds=grace_seconds)
    snapshot_orphans: list[dict[str, object]] = []
    now = time.time()
    for snapshot in sorted(server.config.runtimes_path.rglob("*job*.json")):
        if not snapshot.is_file():
            continue
        try:
            data = json.loads(snapshot.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        run_json = data.get("run_json") if isinstance(data, dict) else None
        if not isinstance(run_json, str) or not run_json:
            continue
        run_path = Path(run_json)
        if not run_path.is_absolute():
            run_path = (server.config.repo_path / run_path).resolve()
        if run_path.exists():
            continue
        age_seconds = int(now - snapshot.stat().st_mtime)
        if age_seconds < grace_seconds:
            continue
        snapshot_orphans.append(
            {
                "kind": "snapshot",
                "path": str(snapshot),
                "run_json": str(run_path),
                "age_seconds": age_seconds,
                "reason": "run_json_missing",
            }
        )
    marked_jobs: list[str] = []
    deleted_snapshots: list[str] = []
    if not dry_run:
        for item in in_memory:
            job_id = str(item.get("job_id") or "")
            if not job_id:
                continue
            server.jobs.mark_orphaned(job_id, reason="marked orphaned by Mission Control cleanup")
            marked_jobs.append(job_id)
        for item in snapshot_orphans:
            path = Path(str(item.get("path") or ""))
            if not path:
                continue
            path.unlink(missing_ok=True)
            deleted_snapshots.append(str(path))
    return {
        "ok": True,
        "dry_run": dry_run,
        "max_age_minutes": max_age_minutes,
        "orphans": in_memory + snapshot_orphans,
        "summary": {
            "in_memory": len(in_memory),
            "snapshots": len(snapshot_orphans),
            "total": len(in_memory) + len(snapshot_orphans),
            "marked_jobs": marked_jobs,
            "deleted_snapshots": deleted_snapshots,
        },
    }


def api_kpis(config: MissionControlServerConfig) -> dict[str, object]:
    state = api_state(config)
    runs = state.get("runs", []) if isinstance(state.get("runs"), list) else []
    applies = api_applies(config).get("applies", [])
    applies = applies if isinstance(applies, list) else []

    total_runs = len(runs)
    blocked_runs = sum(1 for run in runs if isinstance(run, dict) and str(run.get("review_status")) == "blocked")
    ready_runs = sum(1 for run in runs if isinstance(run, dict) and str(run.get("review_status")) == "awaiting_review")

    run_mtime_by_id: dict[tuple[str, str], datetime] = {}
    for run in runs:
        if not isinstance(run, dict):
            continue
        source_json = run.get("source_json")
        task_id = str(run.get("task_id") or "")
        run_id = str(run.get("run_id") or "")
        if not isinstance(source_json, str) or not source_json or not task_id or not run_id:
            continue
        try:
            mtime = datetime.fromtimestamp(Path(source_json).stat().st_mtime, timezone.utc)
        except OSError:
            continue
        run_mtime_by_id[(task_id, run_id)] = mtime

    auto_applies = 0
    lead_times_minutes: list[float] = []
    for item in applies:
        if not isinstance(item, dict):
            continue
        if bool(item.get("auto_applied")):
            auto_applies += 1
        task_id = str(item.get("task_id") or "")
        run_id = str(item.get("run_id") or "")
        apply_at = _parse_iso_utc(item.get("created_at"))
        run_at = run_mtime_by_id.get((task_id, run_id))
        if run_at and apply_at:
            delta = (apply_at - run_at).total_seconds() / 60.0
            if delta >= 0:
                lead_times_minutes.append(delta)

    apply_count = len(applies)
    apply_success_rate = round((apply_count / total_runs), 3) if total_runs else 0.0
    blocked_rate = round((blocked_runs / total_runs), 3) if total_runs else 0.0
    auto_apply_rate = round((auto_applies / apply_count), 3) if apply_count else 0.0
    avg_lead_time_minutes = round((sum(lead_times_minutes) / len(lead_times_minutes)), 2) if lead_times_minutes else None

    return {
        "ok": True,
        "kpis": {
            "total_runs": total_runs,
            "ready_runs": ready_runs,
            "blocked_runs": blocked_runs,
            "apply_count": apply_count,
            "apply_success_rate": apply_success_rate,
            "blocked_rate": blocked_rate,
            "auto_apply_rate": auto_apply_rate,
            "avg_run_to_apply_minutes": avg_lead_time_minutes,
        },
    }


def api_cleanup(config: MissionControlServerConfig, payload: dict[str, object]) -> dict[str, object]:
    dry_run = bool(payload.get("dry_run", True))
    max_age_days = int(payload.get("max_age_days") or 7)
    cutoff = datetime.now(timezone.utc).timestamp() - (max_age_days * 86400)
    roots = (config.apply_results_path / "partial-backups", config.runtimes_path / "mission-control" / "tmp")
    candidates: list[str] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.stat().st_mtime < cutoff:
                candidates.append(str(path))
    if not dry_run:
        for item in candidates:
            Path(item).unlink(missing_ok=True)
    return {"ok": True, "dry_run": dry_run, "deleted": [] if dry_run else candidates, "candidates": candidates, "max_age_days": max_age_days}


def _probe_nemo_mcp_sse(mcp_url: str, timeout_seconds: float = 2.5) -> dict[str, object]:
    target = str(mcp_url or "").strip()
    if not target:
        return {
            "configured": False,
            "active": False,
            "status": "not_configured",
            "url": "",
        }
    if target.lower() == VSCODE_STDIO_NEMO_URL:
        server = discover_vscode_mcp_server("nemo")
        if server is None:
            return {
                "configured": True,
                "active": False,
                "status": "stdio_config_missing",
                "url": target,
                "transport": "vscode_stdio",
            }
        return {
            "configured": True,
            "active": True,
            "status": "active",
            "url": target,
            "transport": "vscode_stdio",
            "server_name": server.name,
            "config_source": server.source_path,
            "command": server.command,
            "args": list(server.args),
        }

    started = time.perf_counter()
    request = urllib.request.Request(target, headers={"Accept": "text/event-stream"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            latency_ms = round((time.perf_counter() - started) * 1000, 1)
            content_type = str(response.headers.get("Content-Type") or "")
            first_line = response.readline(256).decode("utf-8", errors="replace").strip()
            looks_sse = "text/event-stream" in content_type.lower() or first_line.startswith("event:") or first_line.startswith("data:")
            return {
                "configured": True,
                "active": bool(looks_sse),
                "status": "active" if looks_sse else "unexpected_response",
                "url": target,
                "http_status": int(response.getcode() or 0),
                "content_type": content_type,
                "first_line": first_line[:160],
                "latency_ms": latency_ms,
            }
    except urllib.error.HTTPError as error:
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return {
            "configured": True,
            "active": False,
            "status": "http_error",
            "url": target,
            "http_status": int(error.code),
            "error": str(error),
            "latency_ms": latency_ms,
        }
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return {
            "configured": True,
            "active": False,
            "status": "unreachable",
            "url": target,
            "error": str(error),
            "latency_ms": latency_ms,
        }


def _probe_nemo_mcp_capabilities(
    config: MissionControlServerConfig,
    *,
    mcp_url: str,
    include_roundtrip_probe: bool = False,
) -> dict[str, object]:
    cache_key = (str(config.memory_db), str(mcp_url or "").strip(), bool(include_roundtrip_probe))
    cached_payload = _NEMO_MCP_CAPABILITY_CACHE.get("payload")
    if (
        not include_roundtrip_probe
        and _NEMO_MCP_CAPABILITY_CACHE.get("key") == cache_key
        and isinstance(cached_payload, dict)
        and time.time() - float(_NEMO_MCP_CAPABILITY_CACHE.get("at") or 0.0) < 300.0
    ):
        return dict(cached_payload)

    if config.memory_db is None:
        return {
            "enabled": False,
            "supports_context_bootstrap": False,
            "supports_prime_context": False,
            "supports_search_memories": False,
            "supports_core_context_reads": False,
            "supports_write_read_roundtrip": False,
            "roundtrip_probe_executed": False,
            "errors": ["memory_db_disabled"],
        }

    errors: list[str] = []
    is_vscode_stdio_lightweight = str(mcp_url or "").strip().lower() == VSCODE_STDIO_NEMO_URL and not include_roundtrip_probe

    if is_vscode_stdio_lightweight:
        available_tool_names = {contract.name for contract in NEMO_TOOL_REGISTRY}
        supports_context_bootstrap = "context_bootstrap" in available_tool_names
        supports_prime_context = "prime_context" in available_tool_names
        supports_search_memories = "search_memories" in available_tool_names
        supports_core_context_reads = supports_context_bootstrap and supports_prime_context and supports_search_memories
        payload = {
            "enabled": True,
            "supports_context_bootstrap": supports_context_bootstrap,
            "supports_prime_context": supports_prime_context,
            "supports_search_memories": supports_search_memories,
            "supports_core_context_reads": supports_core_context_reads,
            "supports_write_read_roundtrip": False,
            "roundtrip_probe_executed": False,
            "errors": [] if supports_core_context_reads else ["vscode_stdio_catalog_missing_core_tools"],
            "probe_mode": "vscode_stdio_catalog",
        }
        _NEMO_MCP_CAPABILITY_CACHE.update({"at": time.time(), "key": cache_key, "payload": payload})
        return payload

    bootstrap_raw = mcp_call_nemo_tool(
        "context_bootstrap",
        lifecycle_phase="start",
        memory_db=str(config.memory_db),
        mcp_url=mcp_url,
        task="mission control capability probe",
        topic="Mission Control conversation",
        token_budget=256,
        limit=5,
    )
    bootstrap_payload = _normalize_nemo_tool_payload(bootstrap_raw)
    # Empty memory/context is still a successful capability probe. A fresh or
    # irrelevant task can legitimately return chars=0/tokens=0 while proving the
    # tool exists and is callable.
    supports_context_bootstrap = bool(bootstrap_raw.get("ok"))
    if not supports_context_bootstrap:
        errors.append("context_bootstrap_unavailable")

    if str(mcp_url or "").strip().lower() == VSCODE_STDIO_NEMO_URL and not include_roundtrip_probe:
        prime_context_payload = bootstrap_payload.get("prime_context") if isinstance(bootstrap_payload.get("prime_context"), dict) else {}
        supports_prime_context = supports_context_bootstrap and (bool(prime_context_payload) or isinstance(bootstrap_payload.get("context"), str))
        if not supports_prime_context:
            supports_prime_context = supports_context_bootstrap
        supports_search_memories = True
        supports_core_context_reads = supports_context_bootstrap and supports_prime_context and supports_search_memories
        payload = {
            "enabled": True,
            "supports_context_bootstrap": supports_context_bootstrap,
            "supports_prime_context": supports_prime_context,
            "supports_search_memories": supports_search_memories,
            "supports_core_context_reads": supports_core_context_reads,
            "supports_write_read_roundtrip": False,
            "roundtrip_probe_executed": False,
            "errors": errors,
            "probe_mode": "vscode_stdio_lightweight",
        }
        _NEMO_MCP_CAPABILITY_CACHE.update({"at": time.time(), "key": cache_key, "payload": payload})
        return payload

    prime_raw = mcp_call_nemo_tool(
        "prime_context",
        lifecycle_phase="start",
        memory_db=str(config.memory_db),
        mcp_url=mcp_url,
        topic="Mission Control conversation",
    )
    prime_payload = _normalize_nemo_tool_payload(prime_raw)
    supports_prime_context = bool(prime_raw.get("ok")) and (
        isinstance(prime_payload.get("memories"), list)
        or isinstance(prime_payload.get("context"), str)
    )
    if not supports_prime_context:
        errors.append("prime_context_unavailable")

    search_raw = mcp_call_nemo_tool(
        "search_memories",
        lifecycle_phase="review",
        memory_db=str(config.memory_db),
        mcp_url=mcp_url,
        query="mission control capability probe",
        limit=3,
    )
    search_payload = _normalize_nemo_tool_payload(search_raw)
    supports_search_memories = bool(search_raw.get("ok")) and (
        isinstance(search_payload.get("memories"), list)
        or isinstance(search_payload.get("results"), list)
    )
    if not supports_search_memories:
        errors.append("search_memories_unavailable")

    supports_write_read_roundtrip = False
    roundtrip_executed = False
    if include_roundtrip_probe:
        roundtrip_executed = True
        marker = f"mc-capability-probe-{uuid4().hex[:8]}"
        write_raw = mcp_call_nemo_tool(
            "store_conversation",
            lifecycle_phase="close",
            memory_db=str(config.memory_db),
            mcp_url=mcp_url,
            content=f"capability probe marker {marker}",
            role="assistant",
            topic=marker,
            tags=("mission-control", "capability-probe"),
        )
        write_ok = bool(write_raw.get("ok"))
        if not write_ok:
            errors.append("roundtrip_write_failed")
        else:
            prime_marker_raw = mcp_call_nemo_tool(
                "prime_context",
                lifecycle_phase="start",
                memory_db=str(config.memory_db),
                mcp_url=mcp_url,
                topic=marker,
            )
            prime_marker_payload = _normalize_nemo_tool_payload(prime_marker_raw)
            search_marker_raw = mcp_call_nemo_tool(
                "search_memories",
                lifecycle_phase="review",
                memory_db=str(config.memory_db),
                mcp_url=mcp_url,
                query=marker,
                limit=5,
            )
            search_marker_payload = _normalize_nemo_tool_payload(search_marker_raw)

            context_text = str(prime_marker_payload.get("context") or "")
            memories = search_marker_payload.get("memories") if isinstance(search_marker_payload.get("memories"), list) else []
            supports_write_read_roundtrip = marker in context_text or len(memories) > 0
            if not supports_write_read_roundtrip:
                errors.append("roundtrip_read_failed")

    supports_core_context_reads = supports_context_bootstrap and supports_prime_context and supports_search_memories
    payload = {
        "enabled": True,
        "supports_context_bootstrap": supports_context_bootstrap,
        "supports_prime_context": supports_prime_context,
        "supports_search_memories": supports_search_memories,
        "supports_core_context_reads": supports_core_context_reads,
        "supports_write_read_roundtrip": supports_write_read_roundtrip,
        "roundtrip_probe_executed": roundtrip_executed,
        "errors": errors,
    }
    if not include_roundtrip_probe:
        _NEMO_MCP_CAPABILITY_CACHE.update({"at": time.time(), "key": cache_key, "payload": payload})
    return payload


def api_nemo_mcp_status(config: MissionControlServerConfig, payload: dict[str, object] | None = None) -> dict[str, object]:
    settings = _load_settings(config)
    payload_data = payload if isinstance(payload, dict) else {}
    payload_url = payload_data.get("nemo_mcp_url")
    if isinstance(payload_url, str) and payload_url.strip():
        mcp_url = payload_url.strip()
    else:
        mcp_url = str(settings.get("nemo_mcp_url") or "").strip()
    probe = _probe_nemo_mcp_sse(mcp_url)
    include_capability_probe = bool(payload_data.get("include_capability_probe", True))
    include_roundtrip_probe = bool(payload_data.get("include_roundtrip_probe", False))
    capabilities = (
        _probe_nemo_mcp_capabilities(
            config,
            mcp_url=mcp_url,
            include_roundtrip_probe=include_roundtrip_probe,
        )
        if probe.get("active") and include_capability_probe
        else {
            "enabled": False,
            "supports_context_bootstrap": False,
            "supports_prime_context": False,
            "supports_search_memories": False,
            "supports_core_context_reads": False,
            "supports_write_read_roundtrip": False,
            "roundtrip_probe_executed": False,
            "errors": ["probe_not_active_or_disabled"],
        }
    )
    selected_tools = _selected_nemo_tools(payload_data)
    available_tools = sorted(contract.name for contract in NEMO_TOOL_REGISTRY)
    return {
        "ok": True,
        **probe,
        "capabilities": capabilities,
        "available_tools": available_tools,
        "selected_tools": sorted(selected_tools),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def api_nemo_risk_map(config: MissionControlServerConfig, payload: dict[str, object] | None = None) -> dict[str, object]:
    payload_data = payload if isinstance(payload, dict) else {}
    file_or_module = str(payload_data.get("file_or_module") or "")
    risk_category = str(payload_data.get("risk_category") or "")
    limit = int(payload_data.get("limit") or 20)
    if config.memory_db is None:
        return {"ok": True, "patterns": [], "count": 0, "enabled": False}
    patterns = query_self_mod_risk_patterns(
        config.memory_db,
        file_or_module=file_or_module,
        risk_category=risk_category,
        limit=limit,
    )
    return {"ok": True, "enabled": True, **patterns}


def api_nemo_cognitive_stats(config: MissionControlServerConfig, payload: dict[str, object] | None = None) -> dict[str, object]:
    payload_data = payload if isinstance(payload, dict) else {}
    selected = _selected_nemo_payload(payload_data)
    run_kpis = api_kpis(config).get("kpis", {})
    if config.memory_db is None:
        return {
            "ok": True,
            "enabled": False,
            "selected_run": selected,
            "context_portfolio": selected.get("portfolio") if selected else None,
            "health": {"enabled": False, "status": "disabled", "db_path": None},
            "run_kpis": run_kpis,
            "memory_kpis": {
                "atom_count": 0,
                "correction_count": 0,
                "evidence_count": 0,
                "feedback_count": 0,
                "useful_feedback_count": 0,
                "not_useful_feedback_count": 0,
                "useful_feedback_rate": None,
                "portfolio_tokens": None,
                "portfolio_budget": None,
                "portfolio_utilization": None,
            },
        }

    store = PersistentMemoryStore(config.memory_db)
    stats = store.stats()
    corrections = store.search_atoms(atom_types=(MemoryAtomType.CORRECTION,), limit=250)
    feedback = store.list_feedback(limit=50)
    useful_feedback_count = sum(1 for item in feedback if bool(item.get("was_useful")))
    not_useful_feedback_count = sum(1 for item in feedback if item.get("was_useful") is False)
    useful_feedback_rate = round(useful_feedback_count / len(feedback), 3) if feedback else None

    portfolio = selected.get("portfolio") if selected else None
    portfolio_tokens = None
    portfolio_budget = None
    portfolio_utilization = None
    if isinstance(portfolio, dict):
        token_value = portfolio.get("estimated_tokens")
        budget_value = portfolio.get("token_budget")
        if isinstance(token_value, (int, float)):
            portfolio_tokens = int(token_value)
        if isinstance(budget_value, (int, float)) and int(budget_value) > 0:
            portfolio_budget = int(budget_value)
        if portfolio_tokens is not None and portfolio_budget is not None and portfolio_budget > 0:
            portfolio_utilization = round(portfolio_tokens / portfolio_budget, 3)

    return {
        "ok": True,
        "enabled": True,
        "selected_run": selected,
        "context_portfolio": portfolio,
        "health": {"enabled": True, "status": "ok", "db_path": str(config.memory_db), **stats},
        "run_kpis": run_kpis,
        "memory_kpis": {
            "atom_count": stats["atom_count"],
            "correction_count": len(corrections),
            "evidence_count": stats["evidence_count"],
            "feedback_count": stats["feedback_count"],
            "useful_feedback_count": useful_feedback_count,
            "not_useful_feedback_count": not_useful_feedback_count,
            "useful_feedback_rate": useful_feedback_rate,
            "portfolio_tokens": portfolio_tokens,
            "portfolio_budget": portfolio_budget,
            "portfolio_utilization": portfolio_utilization,
        },
    }


def api_search(config: MissionControlServerConfig, payload: dict[str, object]) -> dict[str, object]:
    """POST /api/search — layered search across NEMO memory, portfolio, and URL sources."""
    query = str(payload.get("query") or "").strip()
    if not query:
        raise ApiRequestError("query is required", error_code="invalid_request", status_code=400)

    limit: int = min(int(payload.get("limit") or 5), 20)
    token_budget: int = min(int(payload.get("token_budget") or 600), 4000)
    topic: str = str(payload.get("topic") or "search")
    urls: list[str] = [str(u) for u in (payload.get("urls") or []) if isinstance(u, str) and u.strip()]
    layers = resolve_layers(payload.get("layers"))

    tool_calls: list[dict[str, object]] = []

    def nemo_call(tool_name: str, **kwargs: Any) -> dict[str, Any]:
        return _nemo_chat_tool_call(config, tool_calls, tool_name, **kwargs)

    url_read_fn = None
    if urls:
        def url_read_fn(url: str) -> tuple[dict[str, Any], bool]:  # type: ignore[misc]
            return _read_url_source_cached(url)

    response = run_layered_search(
        query,
        limit=limit,
        layers=layers,
        nemo_call=nemo_call if config.memory_db is not None else None,
        token_budget=token_budget,
        topic=topic,
        urls=urls,
        url_read_fn=url_read_fn,
    )

    return {
        "ok": True,
        "query": query,
        **response.as_dict(),
        "llm_context": response.llm_context,
        "layers_used": [layer.name for layer in layers],
        "tool_calls": tool_calls,
    }


def api_nemo(config: MissionControlServerConfig, payload: dict[str, object]) -> dict[str, object]:
    selected = _selected_nemo_payload(payload)
    if config.memory_db is None:
        return {
            "ok": True,
            "selected_run": selected,
            "health": {"enabled": False, "status": "disabled", "db_path": None},
            "context_portfolio": selected.get("portfolio") if selected else None,
            "memory_traces": selected.get("memory_traces") if selected else [],
            "used_memories": [],
            "corrections": [],
            "evidence": [],
            "feedback": [],
        }
    store = PersistentMemoryStore(config.memory_db)
    stats = store.stats()
    portfolio = selected.get("portfolio") if selected else None
    memory_atom_ids: list[str] = []
    if isinstance(portfolio, dict):
        raw_ids = portfolio.get("memory_atom_ids")
        if isinstance(raw_ids, list):
            memory_atom_ids = [str(item) for item in raw_ids]
    used_memories = store.list_atoms_by_id(tuple(memory_atom_ids)) if memory_atom_ids else ()
    corrections = store.search_atoms(atom_types=(MemoryAtomType.CORRECTION,), limit=12)
    evidence = store.list_evidence(limit=12)
    feedback = store.list_feedback(limit=12)
    if portfolio is None:
        adapter = PersistentNemoAdapter(store)
        _, portfolio_result = adapter.call(
            NemoLifecyclePhase.PLAN,
            "build_context_portfolio",
            task=(selected or {}).get("objective") or "Mission Control operational memory",
            topic="Mission Control",
            token_budget=900,
            limit=50,
        )
        portfolio = portfolio_result.payload
    return {
        "ok": True,
        "selected_run": selected,
        "health": {
            "enabled": True,
            "status": "ok",
            "db_path": str(config.memory_db),
            **stats,
        },
        "context_portfolio": portfolio,
        "memory_traces": selected.get("memory_traces") if selected else [],
        "used_memories": [_memory_atom_payload(atom) for atom in used_memories],
        "corrections": [_memory_atom_payload(atom) for atom in corrections],
        "evidence": [_evidence_payload(item) for item in evidence],
        "feedback": list(feedback),
    }


def api_nemo_tool(config: MissionControlServerConfig, payload: dict[str, object]) -> dict[str, object]:
    tool_name = str(payload.get("tool_name") or "").strip()
    if not tool_name or not re.fullmatch(r"[A-Za-z0-9_.-]+", tool_name):
        raise _bad_request("tool_name is required and must be a valid NEMO MCP tool name", error_code="invalid_nemo_tool")
    arguments = payload.get("arguments")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise _bad_request("arguments must be an object", error_code="invalid_nemo_tool_arguments")
    settings = _load_settings(config)
    raw_mcp_url = payload.get("nemo_mcp_url") or settings.get("nemo_mcp_url")
    nemo_mcp_url = _normalize_nemo_mcp_url(config, raw_mcp_url)
    tool_calls: list[dict[str, object]] = []
    result = _nemo_chat_tool_call(
        config,
        tool_calls,
        tool_name,
        lifecycle_phase=str(payload.get("lifecycle_phase") or "plan"),
        nemo_mcp_url=nemo_mcp_url,
        **arguments,
    )
    return {"ok": True, "tool_name": tool_name, "nemo_mcp_url": nemo_mcp_url, "result": result, "tool_calls": tool_calls}


def api_self_mod_insights(config: MissionControlServerConfig, payload: dict[str, object]) -> dict[str, object]:
    source_json = _source_json(payload)
    trajectory = self_mod_trajectory(source_json)
    impact = self_mod_impact(source_json)
    query = str(payload.get("query") or trajectory.get("objective") or "")
    similar = {"query": query, "runs": [], "count": 0}
    if config.memory_db is not None:
        similar = self_mod_similar_runs(config.memory_db, query, limit=int(payload.get("limit") or 5))
    return {"ok": True, "trajectory": trajectory, "impact": impact, "similar_runs": similar}


def api_eval(config: MissionControlServerConfig, payload: dict[str, object]) -> dict[str, object]:
    result = load_headless_result_json(_source_json(payload))
    readiness = score_persisted_result(result if isinstance(result, dict) else vars(result))
    spec10 = score_spec10_lite(result if isinstance(result, dict) else vars(result))
    return {
        "ok": True,
        "grade": readiness.grade,
        "score": round(readiness.score, 3),
        "validation_passed": readiness.validation_passed,
        "has_checkpoint": readiness.has_checkpoint,
        "has_review_package": readiness.has_review_package,
        "memory_writeback_present": readiness.memory_writeback_present,
        "mutation_present": readiness.mutation_present,
        "reasons": list(readiness.reasons),
        "spec10_grade": spec10["grade"],
        "spec10_score": spec10["score"],
        "spec10_dimensions": spec10["dimensions"],
        "production_checklist": spec10["checklist"],
    }


def api_review(config: MissionControlServerConfig, payload: dict[str, object]) -> dict[str, object]:
    _enforce_workflow_policy(payload, action="review", allowed_modes=("review",))
    plan = build_merge_plan(load_headless_result_json(_source_json(payload)))
    return {"ok": plan.mergeable, "plan": plan.to_dict()}


def api_file(config: MissionControlServerConfig, payload: dict[str, object]) -> dict[str, object]:
    file_path = _file_path(payload)
    plan = build_merge_plan(load_headless_result_json(_source_json(payload)))
    match = next((item for item in plan.files if item.path == file_path), None)
    if match is None:
        raise FileNotFoundError(file_path)
    source_content = _read_text_preview(match.source_path)
    target_content = _read_text_preview(match.target_path)
    file_risks: list[str] = []
    if not match.source_exists:
        file_risks.append("missing_source_file")
    current_target_hash = _hash_path(Path(match.target_path))
    if current_target_hash != match.target_hash:
        file_risks.append("target_changed_since_review")
    return {
        "ok": True,
        "file_path": match.path,
        "operation": match.operation,
        "source_path": match.source_path,
        "target_path": match.target_path,
        "source_content": source_content,
        "target_content": target_content,
        "source_hash": match.source_hash,
        "target_hash": match.target_hash,
        "mergeable": plan.mergeable,
        "risk_flags": list(plan.risk_flags),
        "file_risk_flags": file_risks,
        "hunks": _diff_hunks(target_content, source_content),
    }


def api_apply(config: MissionControlServerConfig, payload: dict[str, object]) -> dict[str, object]:
    _enforce_workflow_policy(payload, action="apply", allowed_modes=("review",))
    source_json = _source_json(payload)
    persisted = load_headless_result_json(source_json)
    plan = build_merge_plan(persisted)
    approve_review = bool(payload.get("approve_review"))
    autonomy_profile = str(payload.get("autonomy_profile") or "manual")
    if not approve_review and autonomy_profile == "trusted":
        spec10 = score_spec10_lite(persisted)
        dimensions = spec10.get("dimensions", {}) if isinstance(spec10, dict) else {}
        traceability = float(dimensions.get("traceability", 0.0)) if isinstance(dimensions, dict) else 0.0
        reasons: list[str] = []
        if float(spec10.get("score", 0.0)) < 0.85:
            reasons.append(f"trusted_requires_spec10_min_0.85:{spec10.get('score', 0.0)}")
        if traceability < 1.0:
            reasons.append("trusted_requires_full_traceability")
        if reasons:
            raise PermissionError(
                "trusted auto-apply blocked: "
                + ", ".join(reasons)
                + ". Suggested action: run Review, address checklist gaps, then retry trusted auto-apply or use manual approval."
            )
    ui_change_detected = any(_is_mission_control_ui_path(item.path) for item in plan.files)
    dist_snapshot = _snapshot_frontend_dist(config, task_id=plan.task_id, run_id=plan.run_id) if ui_change_detected else None
    result = apply_merge_plan(plan, approve_review=approve_review, autonomy_profile=autonomy_profile)
    config.apply_results_path.mkdir(parents=True, exist_ok=True)
    apply_json = config.apply_results_path / f"{_safe_stem(result.task_id)}-{_safe_stem(result.run_id)}.json"
    apply_json.write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    _write_apply_memory(result, config.memory_db)
    frontend = {
        "ui_change_detected": ui_change_detected,
        "dist_snapshot": dist_snapshot,
        "hot_reload": _trigger_frontend_hot_reload(config) if ui_change_detected else {"triggered": False, "reason": "no_ui_changes"},
    }
    return {"ok": True, "apply_json": str(apply_json), "result": result.to_dict(), "state": api_state(config), "frontend": frontend}


def api_apply_selection(config: MissionControlServerConfig, payload: dict[str, object]) -> dict[str, object]:
    _enforce_workflow_policy(payload, action="apply_selection", allowed_modes=("review",))
    if not bool(payload.get("approve_review")):
        raise PermissionError("review approval required before applying selected changes")
    accepted_files_payload = payload.get("accepted_files", [])
    accepted_hunks_payload = payload.get("accepted_hunks", {})
    has_files = isinstance(accepted_files_payload, list) and len(accepted_files_payload) > 0
    has_hunks = isinstance(accepted_hunks_payload, dict) and any(isinstance(v, list) and len(v) > 0 for v in accepted_hunks_payload.values())
    if not has_files and not has_hunks:
        raise _bad_request("no files or hunks selected for apply", error_code="no_selection")
    plan = build_merge_plan(load_headless_result_json(_source_json(payload)))
    if not plan.mergeable:
        raise PermissionError(f"merge plan is not mergeable: {', '.join(plan.risk_flags)}")
    accepted_files = set(str(item) for item in accepted_files_payload) if isinstance(accepted_files_payload, list) else set()
    accepted_hunks = accepted_hunks_payload if isinstance(accepted_hunks_payload, dict) else {}
    applied: list[str] = []
    created_files: list[str] = []
    updated_files: list[str] = []
    backup_files: list[str] = []
    dist_snapshot: str | None = None
    backup_root = config.apply_results_path / "partial-backups" / f"{_safe_stem(plan.task_id)}-{_safe_stem(plan.run_id)}"
    for item in plan.files:
        source = Path(item.source_path)
        target = Path(item.target_path)
        if _hash_path(target) != item.target_hash:
            raise PermissionError(f"target changed since merge plan was built: {item.path}")
        source_content = _read_text_preview(item.source_path)
        target_content = _read_text_preview(item.target_path)
        hunks = _diff_hunks(target_content, source_content)
        selected_hunks = set(str(value) for value in accepted_hunks.get(item.path, [])) if isinstance(accepted_hunks.get(item.path), list) else set()
        if item.path in accepted_files:
            selected_hunks = set(str(hunk["id"]) for hunk in hunks)
        if not selected_hunks:
            continue
        next_content = _apply_hunk_selection(target_content, hunks, selected_hunks)
        if next_content == (target_content or ""):
            continue
        if dist_snapshot is None and _is_mission_control_ui_path(item.path):
            dist_snapshot = _snapshot_frontend_dist(config, task_id=plan.task_id, run_id=f"{plan.run_id}-selection")
        if target.exists():
            backup_target = backup_root / item.path
            backup_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(target, backup_target)
            backup_files.append(item.path)
            updated_files.append(item.path)
        else:
            created_files.append(item.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(next_content, encoding="utf-8")
        applied.append(item.path)
    if not applied:
        raise ValueError("no selected hunks to apply")
    result = MergeApplyResult(plan.task_id, plan.run_id, plan.repo_path, tuple(applied), tuple(created_files), tuple(updated_files), True, str(backup_root) if backup_files else None, tuple(backup_files))
    config.apply_results_path.mkdir(parents=True, exist_ok=True)
    apply_json = config.apply_results_path / f"{_safe_stem(result.task_id)}-{_safe_stem(result.run_id)}-selection.json"
    apply_json.write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    _write_apply_memory(result, config.memory_db)
    ui_change_detected = any(_is_mission_control_ui_path(path) for path in applied)
    frontend = {
        "ui_change_detected": ui_change_detected,
        "dist_snapshot": dist_snapshot,
        "hot_reload": _trigger_frontend_hot_reload(config) if ui_change_detected else {"triggered": False, "reason": "no_ui_changes"},
    }
    return {"ok": True, "apply_json": str(apply_json), "result": result.to_dict(), "state": api_state(config), "frontend": frontend}


def api_handoff(config: MissionControlServerConfig, payload: dict[str, object]) -> dict[str, object]:
    payload = _enforce_workspace_scope(config, {**_load_settings(config), **payload})
    _enforce_workflow_policy(payload, action="handoff", allowed_modes=("build",))
    provider_mode = _provider_mode(payload)
    nemo_mcp_url = _require_nemo_mcp_for_execution(config=config, payload=payload, provider=provider_mode, memory_enabled=config.memory_db is not None)
    run_suffix = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    short_id = uuid4().hex[:8]
    task_id = f"mc-task-{run_suffix}-{short_id}"
    run_id = f"mc-run-{run_suffix}-{short_id}"
    target_files = _string_list(payload, "target_files", ())
    request = _handoff_request(config, payload)
    result = execute_long_handoff_supervisor(
        request,
        budget=LongHandoffBudget(
            max_runtime_minutes=int(payload.get("max_runtime_minutes") or 240),
            heartbeat_minutes=int(payload.get("heartbeat_minutes") or 30),
            max_heartbeats=int(payload.get("max_heartbeats") or 8),
            token_budget=int(payload.get("token_budget") or 64000),
            pause_after_minutes=int(payload.get("pause_after_minutes")) if payload.get("pause_after_minutes") is not None else None,
            plan_minutes=int(payload.get("plan_minutes") or 60),
            execute_minutes=int(payload.get("execute_minutes") or 120),
            review_minutes=int(payload.get("review_minutes") or 60),
        ),
        task_id=task_id,
        run_id=run_id,
        validation_cwd="runtime",
        provider_mode=provider_mode,
        timeout_seconds=_timeout_seconds(payload),
        target_files=target_files,
        validation_policy=str(payload.get("validation_policy") or "smoke"),
        real_validation=bool(payload.get("real_validation", True)),
        nemo_adapter=_nemo_adapter(
            config.memory_db,
            nemo_mcp_url=nemo_mcp_url,
            nemo_mcp_prefix=str(payload.get("nemo_mcp_prefix") or "nemo."),
        ),
        repair_time_limit_seconds=float(payload.get("repair_time_limit_seconds")) if payload.get("repair_time_limit_seconds") is not None else None,
        validation_time_budget_seconds=float(payload.get("validation_time_budget_seconds")) if payload.get("validation_time_budget_seconds") is not None else None,
        validation_escalation_mode=bool(payload.get("validation_escalation_mode")),
    )
    config.run_results_path.mkdir(parents=True, exist_ok=True)
    result_json = config.run_results_path / f"{task_id}-{run_id}.json"
    save_headless_result_json(result, result_json)
    return {"ok": True, "run_json": str(result_json), "summary": result.to_summary_dict(), "state": api_state(config)}


def api_handoff_start(server: "MissionControlHttpServer", payload: dict[str, object]) -> dict[str, object]:
    merged_payload = _enforce_workspace_scope(server.config, {**_load_settings(server.config), **payload})
    _enforce_workflow_policy(merged_payload, action="handoff_start", allowed_modes=("build",))
    provider_mode = _provider_mode(merged_payload)
    _require_nemo_mcp_for_execution(config=server.config, payload=merged_payload, provider=provider_mode, memory_enabled=server.config.memory_db is not None)
    job = server.jobs.start(server.config, merged_payload)
    return {"ok": True, "job": job.to_dict()}


def api_jobs(server: "MissionControlHttpServer") -> dict[str, object]:
    return {"ok": True, "jobs": server.jobs.list()}


def api_job(server: "MissionControlHttpServer", payload: dict[str, object]) -> dict[str, object]:
    return {"ok": True, "job": server.jobs.get(_job_id(payload)).to_dict(), "state": api_state(server.config, server.jobs)}


def api_job_cancel(server: "MissionControlHttpServer", payload: dict[str, object]) -> dict[str, object]:
    return {"ok": True, "job": server.jobs.cancel(_job_id(payload)).to_dict()}


def api_job_pause(server: "MissionControlHttpServer", payload: dict[str, object]) -> dict[str, object]:
    return {"ok": True, "job": server.jobs.pause(_job_id(payload)).to_dict()}


def api_job_resume(server: "MissionControlHttpServer", payload: dict[str, object]) -> dict[str, object]:
    return {"ok": True, "job": server.jobs.resume(server.config, _job_id(payload)).to_dict()}


def _signals_from_job(job: HandoffJob, since: int = 0) -> list[dict[str, object]]:
    signals: list[dict[str, object]] = []
    for sequence, line in enumerate(job.logs, start=1):
        if sequence <= since:
            continue
        normalized = line.lower()
        if "heartbeat" in normalized:
            kind = "HEARTBEAT"
        elif "resumed" in normalized:
            kind = "RESUMED"
        elif "pause" in normalized or "paused" in normalized:
            kind = "PAUSED"
        elif "escalation" in normalized:
            kind = "ESCALATION"
        else:
            continue
        signals.append({"sequence": sequence, "kind": kind, "summary": line})
    return signals


def api_job_signal(server: "MissionControlHttpServer", payload: dict[str, object]) -> dict[str, object]:
    job = server.jobs.get(_job_id(payload))
    raw_since = payload.get("since", 0)
    try:
        since = int(raw_since)
    except (TypeError, ValueError):
        since = 0
    since = max(0, since)
    signals = _signals_from_job(job, since)
    next_since = max([since] + [int(item["sequence"]) for item in signals])
    return {
        "ok": True,
        "job_id": job.job_id,
        "status": job.status,
        "signals": signals,
        "next_since": next_since,
    }


def api_self_modify_start(server: "MissionControlHttpServer", payload: dict[str, object]) -> dict[str, object]:
    scoped_payload = _enforce_workspace_scope(server.config, payload)
    _enforce_workflow_policy(scoped_payload, action="self_modify_start", allowed_modes=("build",))
    job = server.jobs.start_self_modify(server.config, scoped_payload)
    return {"ok": True, "job": job.to_dict()}


def _nemo_chat_tool_call(
    config: MissionControlServerConfig,
    tool_calls: list[dict[str, object]],
    tool_name: str,
    *,
    lifecycle_phase: str | None = None,
    nemo_mcp_url: str | None = None,
    allowed_tools: set[str] | None = None,
    **arguments: Any,
) -> dict[str, Any]:
    canonical_name = f"nemo_memory.{tool_name}"
    alias_name = f"spacecode.{tool_name}"
    if isinstance(allowed_tools, set) and tool_name not in allowed_tools:
        tool_calls.append(
            {
                "id": f"tool-{uuid4().hex[:8]}",
                "name": canonical_name,
                "tool_name": tool_name,
                "alias_name": alias_name,
                "status": "skipped",
                "summary": "Tool disabled for this chat session by MCP tool selector.",
            }
        )
        return {}
    if config.memory_db is None:
        tool_calls.append(
            {
                "id": f"tool-{uuid4().hex[:8]}",
                "name": canonical_name,
                "tool_name": tool_name,
                "alias_name": alias_name,
                "status": "skipped",
                "summary": "NEMO memory database is disabled for this Mission Control session.",
            }
        )
        return {}
    result = mcp_call_nemo_tool(
        tool_name,
        lifecycle_phase=lifecycle_phase,
        memory_db=str(config.memory_db),
        mcp_url=(nemo_mcp_url or ""),
        approve_review=bool((nemo_mcp_url or "").strip()),
        **arguments,
    )
    ok = bool(result.get("ok"))
    payload = _normalize_nemo_tool_payload(result)
    tool_calls.append(
        {
            "id": f"tool-{uuid4().hex[:8]}",
            "name": canonical_name,
            "tool_name": tool_name,
            "alias_name": alias_name,
            "status": "completed" if ok else "failed",
            "summary": _nemo_tool_summary(tool_name, payload if ok else result),
        }
    )
    return payload if ok else {}


def _normalize_nemo_tool_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Unwrap payload shapes returned by native MCP wrappers.

    Expected inputs include:
    - {"payload": {...tool-data...}}
    - {"payload": {"result": {...tool-data...}}}
    - {"payload": {"result": {"payload": {...tool-data...}}}}
    """
    outer = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    if not outer:
        return {}

    nested_result = outer.get("result") if isinstance(outer.get("result"), dict) else None
    if nested_result is None:
        return outer

    nested_payload = nested_result.get("payload") if isinstance(nested_result.get("payload"), dict) else None
    if nested_payload is not None:
        return nested_payload
    return nested_result


def _nemo_tool_summary(tool_name: str, payload: dict[str, Any]) -> str:
    if tool_name == "context_bootstrap":
        prime_payload = payload.get("prime_context") if isinstance(payload.get("prime_context"), dict) else {}
        context = str(payload.get("context") or prime_payload.get("context") or "")
        portfolio = payload.get("portfolio") if isinstance(payload.get("portfolio"), dict) else {}
        if not portfolio and isinstance(payload.get("context_portfolio"), dict):
            portfolio = payload["context_portfolio"]
        estimated_tokens = portfolio.get("estimated_tokens", 0) if isinstance(portfolio, dict) else 0
        return f"Bootstrapped context chars={len(context)} portfolio_tokens={estimated_tokens}."
    if tool_name == "prime_context":
        context = str(payload.get("context") or "")
        memories = payload.get("memories") if isinstance(payload.get("memories"), list) else []
        return f"Loaded Mission Control memory context chars={len(context)} memories={len(memories)}."
    if tool_name == "build_context_portfolio":
        return f"Built context portfolio tokens={payload.get('estimated_tokens', 0)} evidence={len(payload.get('evidence_handles', []))}."
    if tool_name == "search_memories":
        memories = _memories_from_nemo_payload(payload)
        user_name = ""
        if isinstance(memories, list) and memories:
            first = memories[0]
            if isinstance(first, dict):
                raw_name = first.get("user_name")
                if isinstance(raw_name, str) and raw_name.strip():
                    user_name = raw_name.strip()
        if not user_name:
            user_name = _extract_user_name_from_memories(memories)
        suffix = f" user_name={user_name}." if user_name else "."
        return f"Searched memory; matches={len(memories)}{suffix}"
    if tool_name == "anticipate":
        items = payload.get("memories") or payload.get("anticipated") or []
        return f"Anticipated {len(items)} relevant reflexion(s) and risk pattern(s) for this task."
    if tool_name == "store_conversation":
        return f"Stored conversation memory atom={payload.get('atom_id', 'unknown')}."
    if "error" in payload:
        return str(payload.get("error"))
    return "NEMO tool completed."


# Trace helpers delegated to core.trace
_build_agent_trace_event = build_trace_event
_append_agent_trace_from_tool_calls = append_tool_call_traces


def _mission_control_ui_targets(repo_path: Path) -> list[str]:
    src_root = repo_path / "apps" / "mission-control" / "src"
    if not src_root.exists():
        return ["apps/mission-control/src/styles.css", "apps/mission-control/src/main.tsx"]
    targets: list[str] = []
    for path in sorted(src_root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".css", ".ts", ".tsx"}:
            continue
        targets.append(path.relative_to(repo_path).as_posix())
    return targets or ["apps/mission-control/src/styles.css", "apps/mission-control/src/main.tsx"]


def _is_mission_control_ui_path(relative_path: str) -> bool:
    normalized = relative_path.replace("\\", "/")
    return normalized.startswith("apps/mission-control/src/") or normalized in {
        "apps/mission-control/index.html",
        "apps/mission-control/package.json",
        "apps/mission-control/vite.config.ts",
    }


def _snapshot_frontend_dist(config: MissionControlServerConfig, *, task_id: str, run_id: str) -> str | None:
    dist_root = config.repo_path / "apps" / "mission-control" / "dist"
    if not dist_root.exists() or not dist_root.is_dir():
        return None
    snapshot_root = config.apply_results_path / "dist-snapshots" / f"{_safe_stem(task_id)}-{_safe_stem(run_id)}"
    target = snapshot_root / "dist"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(dist_root, target, dirs_exist_ok=True)
    return str(target)


def _trigger_frontend_hot_reload(config: MissionControlServerConfig) -> dict[str, object]:
    marker = config.repo_path / "apps" / "mission-control" / "src" / "vite-env.d.ts"
    if not marker.exists():
        return {"triggered": False, "reason": "marker_missing"}
    now = time.time()
    os.utime(marker, (now, now))
    return {"triggered": True, "marker": str(marker), "ts": int(now)}


def _is_memory_store_request(message: str) -> bool:
    text = message.lower().strip()
    if re.search(r"\b(?:recuerdas|recuérdas|remember\s+what|do\s+you\s+remember)\b", text):
        return False
    store_patterns = (
        r"\bguarda(?:lo|me)?\b",
        r"\bguárda(?:lo|me)?\b",
        r"\bguardar(?:lo)?\b",
        r"\brecuerda\s+(?:que|esto|esta|este|mi|mis|la|el|lo)\b",
        r"\brecuérdalo\b",
        r"\brecuérdame\b",
        r"\balmacena(?:lo)?\b",
        r"\balmacénalo\b",
        r"\balmacenar\b",
        r"\b(?:save|store|remember)\b",
    )
    return any(re.search(pattern, text) for pattern in store_patterns)


def _is_nemo_memory_lookup_request(message: str) -> bool:
    if _is_identity_query(message):
        return True
    if _is_project_query(message):
        return True
    text = message.lower()
    memory_surface = any(term in text for term in ("nemo", "mcp", "memoria", "memory", "contexto"))
    lookup_intent = any(
        term in text
        for term in (
            "revisa", "busca", "consulta", "recupera", "retrieve", "search",
            "tienes informacion", "tienes información", "hay informacion", "hay información",
            "que sabes", "qué sabes", "informacion sobre", "información sobre",
            "recuerdas", "recuérdas", "te acuerdas", "dime lo que sepas", "dime que sabes", "dime qué sabes",
        )
    )
    question_intent = bool(re.search(r"\b(?:que|qué|what)\s+(?:es|is)\b", text))
    natural_memory_question = bool(
        re.search(
            r"\b(?:recuerdas|recuérdas|te\s+acuerdas|dime\s+lo\s+que\s+sepas|dime\s+(?:que|qué)\s+sabes|(?:que|qué)\s+sabes|what\s+do\s+you\s+know|do\s+you\s+remember)\b",
            text,
        )
    )
    return (memory_surface and (lookup_intent or question_intent)) or natural_memory_question


def _extract_nemo_lookup_query(message: str) -> str:
    text = message.strip()
    stopwords = {"nemo", "mcp", "memoria", "memorias", "memory", "contexto", "busca", "revisa", "consulta", "que", "qué", "dime", "recuerdas"}
    for pattern in (
        r"\b(?:recuerdas|recuérdas)\s+([^,.;!?]+)",
        r"\bte\s+acuerdas\s+de\s+([^,.;!?]+)",
        r"\b(?:dime\s+lo\s+que\s+sepas\s+de|dime\s+(?:que|qué)\s+sabes\s+de|(?:que|qué)\s+sabes\s+de|what\s+do\s+you\s+know\s+about)\s+([^,.;!?]+)",
        r"\b(?:que|qué|what)\s+(?:es|is)\s+([^,.;!?]+)",
        r"\b(?:sobre|acerca de|for|about)\s+([^,.;!?]+)",
        r"\b(?:query|busca|search)\s+(?:en\s+)?(?:nemo\s+)?(?:mcp\s+)?(?:que\s+es\s+|qué\s+es\s+)?([^,.;!?]+)",
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            candidate = _clean_nemo_lookup_query(match.group(1))
            if candidate.lower() not in stopwords:
                return candidate
    for candidate in re.findall(r"\b[A-Z][A-Z0-9_.:-]{2,}\b", text):
        if candidate.lower() not in stopwords:
            return candidate
    return text


def _clean_nemo_lookup_query(value: str) -> str:
    cleaned = value.strip().strip(".,;:!?()[]{}\"'")
    cleaned = re.sub(
        r"\b(?:en\s+)?(?:nemo|mcp|memoria|memorias|memory|contexto)\b.*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\b(?:las|los|la|el|un|una)\b\s*$", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip().strip(".,;:!?()[]{}\"'")


def _is_project_query(message: str) -> bool:
    text = message.lower()
    return any(term in text for term in ("proyecto", "proyectos", "project", "projects", "repos", "repositorios"))


def _is_identity_query(message: str) -> bool:
    text = message.lower()
    if "nombre" in text and any(marker in text for marker in ("?", "cual es", "cuál es", "como me", "cómo me")):
        return True
    return any(
        phrase in text
        for phrase in (
            "cual es mi nombre",
            "cuál es mi nombre",
            "como me llamo",
            "cómo me llamo",
            "what is my name",
            "my name",
        )
    )


def _extract_declared_user_name(message: str) -> str:
    text = message.strip()
    patterns = (
        r"(?:mi nombre es|me llamo|my name is)\s+(.+)$",
        r"(?:guarda que mi nombre es|guardar que mi nombre es)\s+(.+)$",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return _clean_declared_user_name(match.group(1))
    return ""


def _clean_declared_user_name(value: str) -> str:
    cleaned = value.strip().strip(".!,;:")
    cleaned = re.sub(r"\s*\(\d{4}-\d{2}-\d{2}\)\s*$", "", cleaned)
    cleaned = re.split(r"\s+(?:tengo|edad|age|years?\s+old|años?)\b", cleaned, maxsplit=1, flags=re.IGNORECASE)[0]
    cleaned = re.split(
        r"\s+(?:guarda|guardalo|guárdalo|guardar|guardarlo|save|store|remember|recuerda|en\s+nemo|en\s+mcp|en\s+memoria)\b",
        cleaned,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    return cleaned.strip().strip(".!,;:")


def _extract_user_name_from_memories(memories: object) -> str:
    if not isinstance(memories, list):
        return ""
    for item in memories:
        if not isinstance(item, dict):
            continue
        user_name = item.get("user_name")
        if isinstance(user_name, str) and user_name.strip():
            return user_name.strip()
        for field_name in ("content", "summary", "text"):
            value = item.get(field_name)
            if not isinstance(value, str):
                continue
            json_name = re.search(r"[\"']?user_name[\"']?\s*[:=]\s*[\"']([^\"']+)[\"']", value, flags=re.IGNORECASE)
            if json_name:
                return _clean_declared_user_name(json_name.group(1))
            match = re.search(
                r"(?:mi nombre es|me llamo|my name is|el usuario se llama|usuario[:\s]+(?:es\s+)?)\s*([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)*)",
                value,
                flags=re.IGNORECASE,
            )
            if match:
                return _clean_declared_user_name(match.group(1))
    return ""


def _bootstrap_context_text(bootstrap_payload: dict[str, Any]) -> str:
    """Flatten the context_bootstrap payload to a searchable text string."""
    # NEMO REST format: prime_context.memories (list of strings)
    prime = bootstrap_payload.get("prime_context")
    if isinstance(prime, dict):
        mems = prime.get("memories")
        if isinstance(mems, list) and mems:
            return "\n".join(str(m) for m in mems if m)
    # NEMO stdio / legacy format: context (str or dict with memories list)
    ctx = bootstrap_payload.get("context")
    if isinstance(ctx, str):
        return ctx
    if isinstance(ctx, dict):
        parts: list[str] = []
        if isinstance(ctx.get("memories"), list):
            parts.extend(str(m) for m in ctx["memories"] if m)
        if isinstance(ctx.get("context"), str) and ctx["context"]:
            parts.append(ctx["context"])
        return "\n".join(parts)
    return ""


def _extract_name_from_bootstrap(bootstrap_payload: dict[str, Any]) -> str:
    text = _bootstrap_context_text(bootstrap_payload)
    if not text:
        return ""
    for pattern, use_group_0 in (
        (r"[\"']?user_name[\"']?\s*[:=]\s*[\"']([^\"']{2,60})[\"']", False),
        (r"(?:mi nombre es|me llamo|my name is|el usuario se llama|nombre(?:\s+del)?\s+usuario[^:]*:\s*)([A-ZÁÉÍÓÚÑ][a-záéíóúñ]{1,}(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]{1,})*)", False),
        (r"\bGabriel\b", True),
    ):
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return _clean_declared_user_name(m.group(0) if use_group_0 else m.group(1))
    return ""


def _unique_memories_from_payloads(payloads: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    memories: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _, payload in payloads:
        for item in _memories_from_nemo_payload(payload):
            if not isinstance(item, dict):
                continue
            key = str(item.get("id") or item.get("memory_id") or item.get("content") or item)
            if key in seen:
                continue
            seen.add(key)
            memories.append(item)
    return memories


def _memories_from_nemo_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    raw_memories = payload.get("memories")
    if isinstance(raw_memories, list):
        return [item if isinstance(item, dict) else {"content": str(item), "type": "memory"} for item in raw_memories if isinstance(item, (dict, str))]
    raw_results = payload.get("results")
    memories: list[dict[str, Any]] = []
    if isinstance(raw_results, list):
        for item in raw_results:
            if isinstance(item, str):
                memories.append({"content": item, "type": "memory"})
                continue
            if not isinstance(item, dict):
                continue
            data = item.get("data")
            if isinstance(data, dict):
                merged = dict(data)
                if "type" not in merged and isinstance(item.get("type"), str):
                    merged["type"] = item["type"]
                if "similarity_score" not in merged and "similarity_score" in item:
                    merged["similarity_score"] = item.get("similarity_score")
                memories.append(merged)
            else:
                memories.append(item)
    return memories


def _verified_nemo_memory_response(message: str, payloads: list[tuple[str, dict[str, Any]]]) -> str:
    if not _is_nemo_memory_lookup_request(message):
        return ""
    memories = _unique_memories_from_payloads(payloads)
    if _is_identity_query(message):
        known_user_name = _extract_user_name_from_memories(memories)
        project_memories = _project_memories_from_payloads(payloads) if _is_project_query(message) else []
        if known_user_name:
            lines = [f"Según NEMO MCP real, tu nombre es {known_user_name}."]
            if _is_project_query(message):
                if project_memories:
                    lines.append("También encontré estos proyectos o contextos de proyecto en NEMO MCP real:")
                    for index, memory in enumerate(project_memories[:5], start=1):
                        lines.append(f"{index}. {_trim_output(_memory_content(memory), 240)}")
                else:
                    lines.append("No encontré proyectos guardados de forma útil en NEMO MCP real.")
            related = [
                memory
                for memory in memories
                if _is_identity_memory(memory)
                and not _is_discarded_memory(memory)
                and not _is_echo_memory(memory, message)
                and not _is_chat_lookup_echo(memory)
                and not _is_lookup_prompt_echo(memory)
            ]
            if related:
                lines.append("También recuperé estas memorias relacionadas:")
                for index, memory in enumerate(related[:3], start=1):
                    lines.append(f"{index}. {_trim_output(_memory_content(memory), 220)}")
            return "\n".join(lines)
        if project_memories:
            lines = ["No encontré una memoria de identidad con tu nombre en NEMO.", "Pero sí encontré estos proyectos o contextos de proyecto:"]
            for index, memory in enumerate(project_memories[:5], start=1):
                lines.append(f"{index}. {_trim_output(_memory_content(memory), 240)}")
            return "\n".join(lines)
        return ""

    lookup_query = _extract_nemo_lookup_query(message)
    useful_memories = [
        memory
        for memory in memories
        if not _is_discarded_memory(memory)
        and not _is_echo_memory(memory, message)
        and not _is_chat_lookup_echo(memory)
        and not _is_lookup_prompt_echo(memory)
        and _memory_matches_lookup(memory, lookup_query)
    ]
    if not useful_memories:
        return ""

    lines = [f"Sí. Consulté NEMO MCP real por {lookup_query!r} y encontré {len(useful_memories)} memoria(s) verificadas:"]
    for index, memory in enumerate(useful_memories[:5], start=1):
        content = _memory_content(memory)
        memory_id = str(memory.get("id") or memory.get("memory_id") or "sin-id")
        memory_type = str(memory.get("type") or memory.get("memory_type") or "memory")
        lines.append(f"{index}. [{memory_type} {memory_id}] {_trim_output(content, 260)}")
    return "\n".join(lines)


def _memory_content(memory: dict[str, Any]) -> str:
    data = memory.get("data")
    if isinstance(data, dict):
        nested = _memory_content(data)
        if nested:
            return nested
    for field_name in ("content", "summary", "text", "compact_claim"):
        value = memory.get(field_name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return json.dumps(memory, ensure_ascii=False, sort_keys=True)


def _is_echo_memory(memory: dict[str, Any], message: str) -> bool:
    content = _memory_content(memory).lower()
    normalized_message = message.strip().lower()
    if content == normalized_message:
        return True
    return (
        content.startswith("space code chat user request:")
        or content.startswith("mission control chat user request:")
    ) and normalized_message in content


def _is_discarded_memory(memory: dict[str, Any]) -> bool:
    tags = memory.get("tags")
    tag_text = " ".join(str(tag).lower() for tag in tags) if isinstance(tags, list) else ""
    content = _memory_content(memory).lower()
    topic = str(memory.get("topic") or "").lower()
    return (
        "discarded" in tag_text
        or "test-cleanup" in tag_text
        or "ignore" in tag_text
        or topic.startswith("spacecode cleanup")
        or content.startswith("test cleanup / ignore")
    )


def _is_chat_lookup_echo(memory: dict[str, Any]) -> bool:
    content = _memory_content(memory).lower().strip()
    if not (content.startswith("space code chat user request:") or content.startswith("mission control chat user request:")):
        return False
    request = content.split(":", 1)[1].strip() if ":" in content else content
    return not _is_memory_store_request(request)


def _is_lookup_prompt_echo(memory: dict[str, Any]) -> bool:
    content = _memory_content(memory).lower().strip()
    return bool(
        re.match(
            r"^(?:recuerdas|recuérdas|dime\s+lo\s+que\s+sepas\s+de|dime\s+(?:que|qué)\s+sabes\s+de|(?:que|qué)\s+sabes\s+de)\b",
            content,
        )
    )


def _project_memories_from_payloads(payloads: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    project_memories: list[dict[str, Any]] = []
    seen: set[str] = set()
    for query, payload in payloads:
        query_text = query.lower()
        if not any(term in query_text for term in ("proyecto", "proyectos", "project", "projects", "repo", "repositorio")):
            continue
        for memory in _memories_from_nemo_payload(payload):
            if not isinstance(memory, dict):
                continue
            if _is_discarded_memory(memory) or _is_chat_lookup_echo(memory) or _is_lookup_prompt_echo(memory):
                continue
            content = _memory_content(memory).lower()
            memory_type = str(memory.get("type") or memory.get("memory_type") or "").lower()
            if "project" not in memory_type and not any(term in content for term in ("proyecto", "project", "repo", "repositorio")):
                continue
            key = str(memory.get("id") or memory.get("memory_id") or memory.get("content") or memory)
            if key in seen:
                continue
            seen.add(key)
            project_memories.append(memory)
    return project_memories


def _memory_matches_lookup(memory: dict[str, Any], lookup_query: str) -> bool:
    query = lookup_query.strip().lower()
    if not query:
        return True
    content = _memory_content(memory).lower()
    return query in content


def _is_identity_memory(memory: dict[str, Any]) -> bool:
    if isinstance(memory.get("user_name"), str) and str(memory.get("user_name")).strip():
        return True
    content = _memory_content(memory).lower()
    return any(marker in content for marker in ("user_name", "mi nombre es", "me llamo", "my name is"))


def _is_self_interface_request(message: str) -> bool:
    text = message.lower()
    self_terms = (
        "tu propia",
        "tus ",
        "propia interfaz",
        "propia interfase",
        "automejora",
        "self",
        "mission control",
        "interfaz",
        "interfase",
        "interface",
        "ui",
        "ux",
    )
    ui_terms = (
        "color",
        "colores",
        "amarillo",
        "dorado",
        "gold",
        "yellow",
        "negro",
        "black",
        "layout",
        "diseno",
        "panel",
        "boton",
        "botones",
        "menu",
        "pantalla",
        "vista",
        "componente",
        "componentes",
        "estilo",
        "estilos",
    )
    change_terms = ("cambia", "cambiar", "ajusta", "modifica", "mejora", "mejorar", "replace", "sustituye")
    return any(term in text for term in self_terms) and any(term in text for term in ui_terms) and any(term in text for term in change_terms)


def _self_interface_action(message: str, payload: dict[str, object]) -> dict[str, object]:
    repo_path = Path(str(payload.get("repo_path") or ".")).resolve()
    return {
        "id": "self-modify-interface-colors",
        "kind": "self_modify",
        "label": "Self-mod UI",
        "summary": "Run a Space Code self-modification against the real Mission Control interface files.",
        "payload": {
            "objective": message,
            "task_type": "tool_expansion",
            "target_files": _mission_control_ui_targets(repo_path),
            "validation_policy": "targeted",
            "validation_commands": ["npm --prefix apps/mission-control run build", "npm --prefix apps/mission-control run test:smoke"],
            "provider": "subprocess",
            "timeout_seconds": payload.get("timeout_seconds") or "300",
            "model": payload.get("default_model") or payload.get("model") or "",
            "base_url": payload.get("model_base_url") or payload.get("base_url") or "http://127.0.0.1:1234/v1",
        },
    }


def _self_platform_action(message: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "id": "self-modify-platform-real-coding",
        "kind": "self_modify",
        "label": "Self-mod core",
        "summary": "Run a real self-modification focused on coding autonomy and reliability.",
        "payload": {
            "objective": message,
            "task_type": "tool_expansion",
            "target_files": [
                "src/nemo_coding_platform/mission_control_server.py",
                "src/nemo_coding_platform/core/headless_runner.py",
                "apps/mission-control/src/main.tsx",
                "tests/test_mission_control_server.py",
            ],
            "validation_policy": "targeted",
            "validation_commands": [
                "python -m unittest tests.test_mission_control_server tests.test_headless_runner",
                "npm --prefix apps/mission-control run build",
            ],
            "provider": "subprocess",
            "real_validation": True,
            "timeout_seconds": payload.get("timeout_seconds") or "300",
            "model": payload.get("default_model") or payload.get("model") or "",
            "base_url": payload.get("model_base_url") or payload.get("base_url") or "http://127.0.0.1:1234/v1",
        },
    }


def _first_url_in_text(message: str) -> str | None:
    match = re.search(r"https?://\S+", message)
    if not match:
        return None
    return match.group(0).rstrip(").,;:!?\"'")


def _pc_control_action(message: str, payload: dict[str, object]) -> dict[str, object] | None:
    text = message.lower()
    timeout_value = payload.get("timeout_seconds") or "45"

    if any(token in text for token in ("buscar", "search web", "buscar web", "search:")):
        query_match = re.search(r"(?:buscar|search(?:\s+web)?)\s*:?\s*(.+)", message, flags=re.IGNORECASE)
        query = query_match.group(1).strip() if query_match else ""
        if query:
            return {
                "id": "pc-control-browser-search",
                "kind": "pc_control",
                "label": "PC Search",
                "summary": "Search the web from chat (requires authorization).",
                "payload": {
                    "mode": "browser_search",
                    "query": query,
                    "timeout_seconds": timeout_value,
                },
            }

    if any(token in text for token in ("abrir", "open", "browser", "navegador", "url")):
        url = _first_url_in_text(message)
        if url:
            return {
                "id": "pc-control-browser-open",
                "kind": "pc_control",
                "label": "PC Open URL",
                "summary": "Open a browser URL from chat (requires authorization).",
                "payload": {
                    "mode": "browser_open",
                    "url": url,
                },
            }

    if any(token in text for token in ("terminal", "powershell", "cmd", "shell", "comando")):
        command_match = re.search(r"(?:terminal|powershell|cmd|shell|comando)\s*:?\s*(.+)", message, flags=re.IGNORECASE)
        command = command_match.group(1).strip() if command_match else ""
        if command:
            return {
                "id": "pc-control-terminal-run",
                "kind": "pc_control",
                "label": "PC Run Cmd",
                "summary": "Run a terminal command from chat (requires authorization).",
                "payload": {
                    "mode": "terminal_run",
                    "command": command,
                    "timeout_seconds": timeout_value,
                },
            }
    return None


def _run_or_handoff_actions(message: str, payload: dict[str, object], selected_objective: str, changed_files: tuple[str, ...]) -> list[dict[str, object]]:
    text = message.lower()
    actions: list[dict[str, object]] = []
    target_files = "\n".join(changed_files)
    provider = payload.get("provider") or "subprocess"
    timeout_value = payload.get("timeout_seconds") or "300"

    if any(token in text for token in ("run", "ejecuta", "lanza", "inicia run", "nuevo run")):
        actions.append(
            {
                "id": "start-run-from-chat",
                "kind": "run",
                "label": "Run",
                "summary": "Start a new async run from this chat request.",
                "payload": {
                    "objective": f"Run: {message}",
                    "acceptance_criteria": "run objective completed with explicit output",
                    "validation_commands": "python -m unittest",
                    "target_files": target_files,
                    "provider": provider,
                    "timeout_seconds": timeout_value,
                },
            }
        )

    if "handoff" in text or "traspaso" in text:
        actions.append(
            {
                "id": "start-handoff-from-chat",
                "kind": "handoff",
                "label": "Handoff",
                "summary": "Start a new async handoff from this chat request.",
                "payload": {
                    "objective": f"Handoff: {message or selected_objective}",
                    "acceptance_criteria": "handoff advances selected objective",
                    "validation_commands": "python -m unittest",
                    "target_files": target_files,
                    "provider": provider,
                    "timeout_seconds": timeout_value,
                },
            }
        )

    pc_action = _pc_control_action(message, payload)
    if pc_action:
        actions.append(pc_action)
    return actions


def _extract_json_score(critique: str) -> float:
    """Parse a numeric score from the LLM's JSON critique response."""
    # Try full JSON parse first (may be wrapped in ```json fence)
    json_match = re.search(r'\{.*?\}', critique, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(0))
            s = data.get("score")
            if s is not None:
                return max(1.0, min(10.0, float(s)))
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    # Fallback: bare "score": N pattern
    m = re.search(r'"score"\s*:\s*(\d+(?:\.\d+)?)', critique)
    if m:
        return max(1.0, min(10.0, float(m.group(1))))
    return 5.0


# Semaphore that serializes all LM Studio API calls (chat + plan loop).
# Arc iGPU (Vulkan, shared VRAM) cannot safely run concurrent inference requests
# from the same or different models — driver-level contention causes freezes.
_LLM_SEM = threading.Semaphore(1)

_SERVER_START_TIME: float = time.time()

# Per-job control plane for running plan loops.
# Keys are job_ids; values are mutable dicts with {cancel: bool, steer: str | None}.
_plan_jobs: dict[str, dict[str, object]] = {}
_plan_jobs_lock = threading.Lock()


def _plan_lm_call(payload: dict[str, Any], system: str, user: str, max_tokens: int = 1024, timeout: int = 120, temperature: float = 0.6) -> str:
    """Minimal LM Studio call for plan loop — no Space Code context, tight budget."""
    base_url = _chat_base_url(payload)
    model = _chat_model(payload)
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{_chat_base_url(payload)}/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {os.environ.get('LMSTUDIO_API_KEY', 'lm-studio')}"},
        method="POST",
    )
    with _LLM_SEM:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        # Hold semaphore during cooldown so concurrent chat can't call LLM while
        # GPU drains from this call (Arc iGPU / Vulkan shared-VRAM contention).
        time.sleep(1.5)
    choices = data.get("choices") or []
    if not choices:
        raise ValueError("plan lm call: no choices in response")
    return str(choices[0].get("message", {}).get("content", ""))


# Shell keywords that appear as bare lines in model output but are invalid Python at runtime.
# These pass ast.parse() (they look like name references) but raise NameError when executed.
_SHELL_BARE_LINES: frozenset[str] = frozenset({"fi", "then", "done", "esac", ";;", "do"})

# Heuristic: a line that starts a Python code block (used by _extract_ast_valid).
_PY_LINE_START = re.compile(
    r"^(import |from |def |class |#|@|matplotlib|plt\.|np\.|[A-Za-z_]\w*\s*[=(])"
)


def _strip_shell_artifacts(code: str) -> str:
    """Remove bare shell-keyword lines that would cause NameError in Python."""
    return "\n".join(
        line for line in code.splitlines() if line.strip() not in _SHELL_BARE_LINES
    )


def _extract_ast_valid(text: str) -> str:
    """Find the longest prefix (from the first Python-looking line) that ast.parse() accepts."""
    lines = text.splitlines()
    start = 0
    for i, line in enumerate(lines):
        if _PY_LINE_START.match(line.strip()):
            start = i
            break
    for end in range(len(lines), start, -1):
        candidate = "\n".join(lines[start:end])
        try:
            ast.parse(candidate)
            return candidate
        except SyntaxError:
            pass
    return "\n".join(lines[start:])


def _extract_think_snippet(text: str, max_chars: int = 500) -> str:
    """Extract first reasoning block from a thinking-model response."""
    for pattern in (r"<think>(.*?)</think>", r"<\|thinking\|>(.*?)<\|/thinking\|>"):
        m = re.search(pattern, text, re.DOTALL)
        if m:
            return m.group(1).strip()[:max_chars]
    return ""


def _extract_code_block(text: str) -> str:
    """Adaptive cascade extractor — model-agnostic, works regardless of which LLM is loaded.

    Pipeline:
      1. Strip reasoning blocks (<think>, <|thinking|>)
      2. Prefer explicitly-tagged ```python fence
      3. Accept any fenced block (```bash, ```sh, untagged, …)
      4. Fallback: longest ast-valid substring starting at first Python-looking line
      Shell artifacts (fi, then, done, esac, ;;, do as bare lines) are stripped at every stage.
    """
    # Strip reasoning blocks from thinking-model variants
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"<\|thinking\|>.*?<\|/thinking\|>", "", text, flags=re.DOTALL)
    text = text.strip()

    # Explicitly-tagged Python fence
    m = re.search(r"```python\n(.*?)```", text, re.DOTALL)
    if m:
        return _strip_shell_artifacts(m.group(1).strip())

    # Any fenced block regardless of language tag
    m = re.search(r"```\w*\n(.*?)```", text, re.DOTALL)
    if m:
        return _strip_shell_artifacts(m.group(1).strip())

    # Last resort: find the longest syntactically-valid Python substring
    return _strip_shell_artifacts(_extract_ast_valid(text))


_AGENT_TOOL_CATALOG = """\
═══════════════════════════════════════════════════════════
AGENT ACTION TOOLS — THE ONLY 4 TOOLS YOU CAN TRIGGER
Embed the JSON in your response text to create an action button.
DO NOT call any NEMO MCP tools (search_memories, context_bootstrap, etc.) here — those are server-only.
═══════════════════════════════════════════════════════════

1. handoff_start — full autonomous coding session (edits files, runs tests, makes commits)
   {"tool": "handoff_start", "params": {"objective": "...", "acceptance": "...", "target_files": "optional"}}
   Use when the user asks to implement, fix bugs, or change repo source code.

2. plan_generate — iterative code generation with scoring (minutes, for scripts and standalone artifacts)
   {"tool": "plan_generate", "params": {"objective": "...", "max_iterations": 3, "quality_threshold": 7.0}}
   Use when the user asks for a script, visualization, or standalone program.

3. job_status — query the status of a running background job
   {"tool": "job_status", "params": {"job_id": "job-..."}}
   Use when the user asks about the progress of an ongoing operation.

4. browser_task — autonomous web navigation with visual AI
   {"tool": "browser_task", "params": {"url": "https://...", "task": "full description", "max_steps": 8}}
   Use when the user asks to navigate, search, scrape, or interact with websites.
   Only add "credential_alias": "alias_name" when the user explicitly mentions needing to log in.
   max_steps defaults to 8 (max 20). Never invent a credential_alias.

CRITICAL: These 4 are the ONLY tools you can embed in your response. Do NOT embed NEMO tool names
(search_memories, context_bootstrap, refresh_context_portfolio, cognitive_ingest, etc.) — those
are executed automatically by the backend and cannot be invoked by you.

Include the JSON in your response when applicable, then explain in text what it will do and why.
Keep params as flat strings/numbers — no nested objects inside params.
"""

_TOOL_KEY_RE = re.compile(r'"tool"\s*:\s*"')


def _parse_llm_tool_calls(text: str) -> list[dict[str, object]]:
    """Extract {\"tool\": \"...\", \"params\": {...}} objects from LLM response text.

    Uses balanced-brace scanning so nested params dicts are handled correctly.
    """
    results: list[dict[str, object]] = []
    seen: set[str] = set()
    for m in _TOOL_KEY_RE.finditer(text):
        # Walk back to find the opening brace of this JSON object
        start = text.rfind("{", 0, m.start())
        if start == -1:
            continue
        # Walk forward counting brace depth to find the matching closing brace
        depth = 0
        end = -1
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end == -1:
            continue
        try:
            obj = json.loads(text[start:end])
            if not (isinstance(obj, dict) and isinstance(obj.get("params"), dict)):
                continue
            tool = str(obj.get("tool") or "")
            if tool and tool not in seen:
                seen.add(tool)
                results.append(obj)
        except (json.JSONDecodeError, ValueError):
            pass
    return results


def _try_run_code(code: str, timeout: int = 10) -> tuple[bool, str]:
    """Attempt to execute Python code in a subprocess. Returns (success, output)."""
    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=timeout,
        )
        out = (result.stdout + result.stderr).strip()
        return result.returncode == 0, out[:400]
    except subprocess.TimeoutExpired:
        return False, "timeout after 10s"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:200]


_CANDIDATE_TEMPS = [0.4, 0.7, 1.0]


def _generate_candidates(payload: dict[str, Any], system: str, user: str, n: int = 3) -> list[str]:
    """Run n parallel LM generation calls with different temperatures; return all responses."""
    temps = _CANDIDATE_TEMPS[:n]
    with ThreadPoolExecutor(max_workers=n) as ex:
        futures = {
            ex.submit(_plan_lm_call, payload, system, user, 512, 300, t): t
            for t in temps
        }
        results: list[str] = []
        for f in as_completed(futures):
            try:
                results.append(f.result())
            except Exception:
                pass
    return results


def _visual_critique_lm_call(payload: dict[str, Any], objective: str, image_path: str = "hand.png") -> str | None:
    """Send the generated image to a multimodal LM for visual scoring. Returns raw JSON string or None."""
    try:
        img_bytes = Path(image_path).read_bytes()
    except (FileNotFoundError, OSError):
        return None
    b64 = base64.b64encode(img_bytes).decode("ascii")
    messages = [
        {
            "role": "system",
            "content": (
                "You are a visual art critic. Reply ONLY with JSON — no prose. "
                'Format: {"score":<int 1-10>,"present":[<str>],"missing":[<str>],"summary":"<str>"}'
            ),
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": f"Rate this image for task: {objective[:120]}"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        },
    ]
    vis_base_url = _chat_base_url(payload)
    body = json.dumps({
        "model": _resolve_lmstudio_model(vis_base_url) or _chat_model(payload),
        "messages": messages,
        "temperature": 0.0,
        "max_tokens": 200,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{vis_base_url}/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {os.environ.get('LMSTUDIO_API_KEY', 'lm-studio')}"},
        method="POST",
    )
    try:
        with _LLM_SEM:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        choices = data.get("choices") or []
        if choices:
            return str(choices[0].get("message", {}).get("content", ""))
    except Exception:  # noqa: BLE001
        pass
    return None


def api_agent_plan_gen(config: MissionControlServerConfig, payload: dict[str, object], job_id: str = ""):  # type: ignore[return]
    """Generator version of the autonomous plan loop. Yields event dicts per iteration then a final 'done' event."""
    settings = _load_settings(config)
    payload = {**settings, **payload}

    objective = str(payload.get("objective") or "").strip()
    if not objective:
        raise _bad_request("objective is required for plan mode", error_code="missing_objective")

    max_iterations = max(1, min(10, int(payload.get("max_iterations") or 5)))
    quality_threshold = max(1.0, min(10.0, float(payload.get("quality_threshold") or 7.0)))
    topic = str(payload.get("topic") or "autonomous_plan").strip()
    use_parallel = bool(payload.get("parallel_candidates", True))
    use_visual = bool(payload.get("visual_critique", True))
    nemo_mcp_url = _require_nemo_mcp_url(payload)
    tool_calls: list[dict[str, object]] = []

    # Register job in control plane so stop/steer endpoints can reach it
    if job_id:
        with _plan_jobs_lock:
            _plan_jobs[job_id] = {"cancel": False, "steer": None}

    def _nemo(tool_name: str, phase: str, **kw: Any) -> dict[str, Any]:
        return _nemo_chat_tool_call(
            config, tool_calls, tool_name,
            lifecycle_phase=phase, nemo_mcp_url=nemo_mcp_url, **kw,
        )

    # Bootstrap NEMO context
    _nemo("context_bootstrap", "start", task=objective, topic=topic, token_budget=600, limit=6)

    iterations: list[dict[str, Any]] = []
    best_code = ""
    best_score = 0.0
    best_exec_ok = False
    final_score = 0.0
    think_snippet = ""

    gen_sys = (
        "You are a Python code generator. Output ONLY raw Python code — no markdown fences, "
        "no explanations, no comments. Keep it under 50 lines. "
        "IMPORTANT rules: (1) always start with 'import matplotlib; matplotlib.use(\"Agg\")' "
        "before other imports so it runs headless; "
        "(2) save the figure with plt.savefig('hand.png') and call plt.close() — never plt.show(); "
        "(3) use simple FancyBboxPatch or Polygon shapes — no deep nested list literals."
    )
    critique_sys = (
        "You are a code reviewer. Reply ONLY with a JSON object — no markdown, no prose. "
        'Format: {"score":<int 1-10>,"present":[<str>],"missing":[<str>],'
        '"improvements":[<str>],"summary":"<str>"}'
    )

    yield {"type": "start", "objective": objective, "max_iterations": max_iterations, "quality_threshold": quality_threshold, "job_id": job_id}

    for i in range(1, max_iterations + 1):
        # --- Check stop/steer control plane ---
        if job_id:
            with _plan_jobs_lock:
                _ctrl = _plan_jobs.get(job_id, {})
                should_cancel = _ctrl.get("cancel")
                _steer_directive = str(_ctrl.get("steer") or "")
                if _steer_directive:
                    _ctrl["steer"] = None  # consume atomically
            if should_cancel:
                yield {"type": "cancelled", "job_id": job_id, "iterations_run": len(iterations),
                       "best_score": best_score}
                with _plan_jobs_lock:
                    _plan_jobs.pop(job_id, None)
                return
        else:
            _steer_directive = ""

        # --- Retrieve NEMO pattern memory (known bad patterns from past sessions) ---
        nemo_hint = ""
        try:
            mem_result = _nemo("search_memories", "read",
                               query=f"{topic} matplotlib error failure syntax",
                               compact=True, limit=4, min_importance=5)
            snippets = [m.get("content", "")[:100] for m in (mem_result.get("memories") or [])]
            if snippets:
                nemo_hint = "\nKnown bad patterns to avoid:\n" + "\n".join(f"- {s}" for s in snippets[:3])
        except Exception:  # noqa: BLE001
            pass

        # --- Build generation prompt ---
        if i == 1:
            gen_user = (
                f"Task: {objective}\n\n"
                "Rules: output ONLY valid Python. Use simple shapes (polygons/patches), "
                f"no complex list literals. Keep total code under 45 lines.{nemo_hint}"
            )
        else:
            last = iterations[-1]
            # Use best known code as base when score regressed
            base_code = best_code if best_score > last["score"] + 0.5 else last["code"]
            exec_hint = ""
            if not last["exec_ok"] and last["exec_output"]:
                exec_hint = f"\nEXECUTION ERROR (fix this first): {last['exec_output'][:200]}\n"
            steer_hint = f"\nUSER DIRECTIVE (apply this now): {_steer_directive}\n" if _steer_directive else ""
            gen_user = (
                f"Improve this code (best score so far: {best_score:.1f}/10, current: {last['score']:.1f}/10)."
                f"{exec_hint}{steer_hint}\n"
                f"Critique: {last['critique_text'][:250]}\n\n"
                f"Base code:\n{base_code[:700]}\n\n"
                f"Return ONLY valid complete Python. Under 45 lines.{nemo_hint}"
            )

        # --- Brief cooldown: let NEMO embedding (iGPU) drain before LLM inference ---
        time.sleep(2.0)

        # --- Generate: parallel candidates or single call ---
        code = ""
        code_response = ""
        try:
            if use_parallel and i > 1:
                responses = _generate_candidates(payload, gen_sys, gen_user, n=3)
            else:
                responses = [_plan_lm_call(payload, gen_sys, gen_user, max_tokens=512, timeout=300, temperature=0.6)]
        except Exception:  # noqa: BLE001
            break  # LM Studio unavailable; stop loop

        # Capture think snippet from first response (before stripping)
        think_snippet = _extract_think_snippet(responses[0]) if responses else ""

        # Pick best syntactically-valid candidate
        for resp in responses:
            candidate = _extract_code_block(resp)
            try:
                ast.parse(candidate)
                code = candidate
                code_response = resp
                break
            except SyntaxError:
                pass
        if not code:
            # All candidates have syntax errors — try single fix retry on the longest one
            longest = max(responses, key=len) if responses else ""
            candidate = _extract_code_block(longest)
            syntax_err_msg = ""
            try:
                ast.parse(candidate)
                code = candidate
                code_response = longest
            except SyntaxError as se:
                syntax_err_msg = f"{se.msg} at line {se.lineno}"
                fix_user = f"SyntaxError: {syntax_err_msg}\n\nBroken code:\n{candidate}\n\nReturn ONLY corrected Python."
                try:
                    fix_resp = _plan_lm_call(payload, gen_sys, fix_user, max_tokens=512, timeout=120, temperature=0.0)
                    fixed = _extract_code_block(fix_resp)
                    ast.parse(fixed)
                    code = fixed
                    code_response = fix_resp
                except SyntaxError:
                    # Still broken — record and continue to next iteration
                    record: dict[str, Any] = {
                        "iteration": i, "code": candidate, "critique_text": "{}",
                        "score": 1.0, "exec_ok": False,
                        "exec_output": f"SyntaxError: {syntax_err_msg}",
                    }
                    iterations.append(record)
                    yield {
                        "type": "iteration", "iteration": i, "score": 1.0,
                        "exec_ok": False, "exec_output": f"SyntaxError: {syntax_err_msg}",
                        "critique_summary": "{}", "code_chars": len(candidate),
                    }
                    continue
                except Exception:  # noqa: BLE001
                    break

        # --- Execute code first so critique sees runtime result ---
        exec_ok, exec_output = _try_run_code(code)

        # --- Visual critique if image was produced ---
        visual_raw: str | None = None
        if exec_ok and use_visual:
            visual_raw = _visual_critique_lm_call(payload, objective)

        # --- Text self-critique ---
        exec_note = "Execution: OK" if exec_ok else f"Execution FAILED: {exec_output[:150]}"
        critique_user = f"Task: {objective[:120]}\n{exec_note}\n\nCode:\n{code[:900]}"
        try:
            critique_raw = _plan_lm_call(payload, critique_sys, critique_user, max_tokens=250, timeout=120, temperature=0.0)
        except Exception:  # noqa: BLE001
            critique_raw = '{"score":5,"present":[],"missing":[],"improvements":[],"summary":"unavailable"}'

        text_score = _extract_json_score(critique_raw)
        visual_score = _extract_json_score(visual_raw) if visual_raw else 0.0
        # Visual score supplements text score when image was produced
        score = max(text_score, visual_score) if visual_score > 0 else text_score
        if not exec_ok:
            score = min(score, 5.0)

        # --- Update best-known snapshot ---
        if score > best_score:
            best_score, best_code, best_exec_ok = score, code, exec_ok

        # --- Brief cooldown: let LLM inference (iGPU) drain before NEMO embedding ---
        time.sleep(2.0)

        # --- NEMO checkpoint ---
        _nemo(
            "cognitive_ingest", "review",
            content=(
                f"Plan autonomo — iteracion {i}/{max_iterations}\n"
                f"Objetivo: {objective}\n"
                f"Puntuacion: {score}/10  Ejecutable: {'si' if exec_ok else 'no'}\n"
                f"Critica texto: {critique_raw[:300]}\n"
                + (f"Critica visual: {visual_raw[:200]}\n" if visual_raw else "")
                + f"Codigo ({len(code)} chars):\n{code[:700]}"
            ),
            memory_type="evidence",
            tags=("plan", "autonomous", "iteration", topic),
            context=f"Autonomous plan loop iteration {i}",
        )

        record = {
            "iteration": i,
            "code": code,
            "critique_text": critique_raw,
            "score": score,
            "exec_ok": exec_ok,
            "exec_output": exec_output,
        }
        iterations.append(record)
        final_score = score

        yield {
            "type": "iteration",
            "iteration": i,
            "score": score,
            "exec_ok": exec_ok,
            "exec_output": exec_output,
            "critique_summary": critique_raw[:300],
            "visual_critique": visual_raw[:200] if visual_raw else None,
            "code_chars": len(code),
            "think_snippet": think_snippet[:400],
        }

        if score >= quality_threshold:
            break

    # Copy any generated image artifacts to the served artifacts folder.
    # Done in a finally block so it runs even if the SSE client disconnects early.
    artifact_file: str | None = None
    try:
        artifacts_dir = config.runtimes_path / "mission-control" / "artifacts" / "images"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        for ext in ("png", "jpg", "jpeg", "svg"):
            for img in Path(".").glob(f"*.{ext}"):
                dest = artifacts_dir / img.name
                shutil.copy2(img, dest)
                if artifact_file is None:
                    artifact_file = img.name
    except Exception:  # noqa: BLE001
        pass

    # Final NEMO summary
    _nemo(
        "cognitive_ingest", "review",
        content=(
            f"Plan autonomo COMPLETADO: {objective}\n"
            f"Iteraciones: {len(iterations)}  Puntuacion final: {final_score}/10\n"
            f"Mejor puntuacion: {best_score}/10\n"
            f"Resultado: {'ACEPTABLE' if final_score >= quality_threshold else 'PARCIAL'}"
        ),
        memory_type="preference",
        tags=("plan", "autonomous", "completed", topic),
        context="Autonomous plan loop finished",
    )

    yield {
        "type": "done",
        "ok": True,
        "objective": objective,
        "iterations_run": len(iterations),
        "final_score": final_score,
        "best_score": best_score,
        "quality_threshold": quality_threshold,
        "completed": final_score >= quality_threshold,
        "artifact_file": artifact_file,
        "iterations": [
            {
                "iteration": it["iteration"],
                "score": it["score"],
                "exec_ok": it["exec_ok"],
                "exec_output": it["exec_output"],
                "critique_summary": it["critique_text"][:300],
                "code_chars": len(it["code"]),
            }
            for it in iterations
        ],
        "final_code": iterations[-1]["code"] if iterations else "",
        "final_critique": iterations[-1]["critique_text"] if iterations else "",
        "tool_calls": tool_calls,
    }
    # Cleanup control plane entry
    if job_id:
        with _plan_jobs_lock:
            _plan_jobs.pop(job_id, None)


def api_agent_plan(config: MissionControlServerConfig, payload: dict[str, object]) -> dict[str, object]:
    """Non-streaming wrapper — collects generator events and returns the final done dict."""
    done: dict[str, object] = {}
    for event in api_agent_plan_gen(config, payload):
        if event.get("type") == "done":
            done = event
    return done if done else {"ok": False, "error": "plan loop produced no done event"}


def api_plan_cancel(payload: dict[str, object]) -> dict[str, object]:
    """Signal a running plan job to cancel after the current iteration."""
    job_id = str(payload.get("job_id") or "")
    with _plan_jobs_lock:
        if not job_id or job_id not in _plan_jobs:
            return {"ok": False, "error": "job not found"}
        _plan_jobs[job_id]["cancel"] = True
    return {"ok": True, "job_id": job_id}


def api_plan_steer(payload: dict[str, object]) -> dict[str, object]:
    """Inject a user directive into the next iteration of a running plan job."""
    job_id = str(payload.get("job_id") or "")
    directive = str(payload.get("directive") or "").strip()
    if not directive:
        return {"ok": False, "error": "directive is required"}
    with _plan_jobs_lock:
        if not job_id or job_id not in _plan_jobs:
            return {"ok": False, "error": "job not found"}
        _plan_jobs[job_id]["steer"] = directive
    return {"ok": True, "job_id": job_id, "directive": directive}


def _llm_tool_call_to_action(
    invocation: dict[str, object],
    server: "MissionControlHttpServer | None",
    payload: dict[str, object],
) -> "dict[str, object] | None":
    """Convert an LLM-emitted tool invocation dict into an AgentAction, or None if not recognized."""
    tool = str(invocation.get("tool") or "")
    params = dict(invocation.get("params") or {})

    if tool == "handoff_start":
        objective = str(params.get("objective") or payload.get("objective") or "")
        if not objective:
            return None
        return {
            "id": f"llm-handoff-{uuid4().hex[:8]}",
            "kind": "run",
            "label": f"Start Handoff: {objective[:60]}",
            "summary": "Full autonomous coding session triggered by the LLM.",
            "payload": {
                "objective": objective,
                "acceptance_criteria": str(params.get("acceptance") or "satisfies the objective"),
                "target_files": str(params.get("target_files") or ""),
                "provider": str(payload.get("provider") or "subprocess"),
                "timeout_seconds": str(payload.get("timeout_seconds") or "120"),
                "nemo_mcp_url": str(payload.get("nemo_mcp_url") or ""),
            },
        }

    if tool == "plan_generate":
        objective = str(params.get("objective") or payload.get("objective") or "")
        if not objective:
            return None
        return {
            "id": f"llm-plan-{uuid4().hex[:8]}",
            "kind": "plan_generate",
            "label": f"Generate: {objective[:60]}",
            "summary": "Iterative code generation with scoring triggered by the LLM.",
            "payload": {
                "objective": objective,
                "max_iterations": int(params.get("max_iterations") or 3),
                "quality_threshold": float(params.get("quality_threshold") or 7.0),
                "topic": str(params.get("topic") or "llm_generated"),
                "parallel_candidates": False,
                "visual_critique": False,
                "model_base_url": str(payload.get("model_base_url") or "http://localhost:1234/v1"),
                "nemo_mcp_url": str(payload.get("nemo_mcp_url") or "http://127.0.0.1:8765/mcp/sse"),
            },
        }

    if tool == "job_status":
        job_id = str(params.get("job_id") or "")
        if server is not None and job_id:
            try:
                job = server.jobs.get(job_id)
                if job:
                    return {
                        "id": f"llm-jobstatus-{uuid4().hex[:8]}",
                        "kind": "layout",
                        "label": f"Job {job_id[:12]}: {job.status}",
                        "summary": f"status={job.status} | returncode={job.returncode}",
                        "payload": {"job_id": job_id},
                    }
            except Exception:  # noqa: BLE001
                pass
        return None

    if tool == "browser_task":
        url = str(params.get("url") or "").strip()
        task = str(params.get("task") or "").strip()
        if not url or not task:
            return None
        return {
            "id": f"llm-browser-{uuid4().hex[:8]}",
            "kind": "browser_task",
            "label": f"Browse: {url[:60]}",
            "summary": "Autonomous web navigation with visual AI triggered by the LLM.",
            "payload": {
                "url": url,
                "task": task,
                "credential_alias": params.get("credential_alias") or None,
                "max_steps": int(params.get("max_steps") or 8),
            },
        }

    return None


def api_agent_message(
    config: MissionControlServerConfig,
    payload: dict[str, object],
    server: "MissionControlHttpServer | None" = None,
) -> dict[str, object]:
    request_started = time.perf_counter()
    settings = _load_settings(config)
    payload = {**settings, **payload}
    message = _message(payload)
    provider = _provider_mode(payload)
    nemo_mcp_url = _require_nemo_mcp_url(payload)
    if nemo_mcp_url == LEGACY_NEMO_SSE_URL and settings.get("nemo_mcp_url") == VSCODE_STDIO_NEMO_URL:
        nemo_mcp_url = VSCODE_STDIO_NEMO_URL
    require_mcp_capabilities = bool(payload.get("require_nemo_mcp_capabilities", provider == "subprocess" and payload.get("nemo_required", config.memory_db is not None)))
    require_mcp_roundtrip = bool(payload.get("require_nemo_roundtrip", False))
    if require_mcp_capabilities:
        capability_probe = _probe_nemo_mcp_capabilities(
            config,
            mcp_url=nemo_mcp_url,
            include_roundtrip_probe=require_mcp_roundtrip,
        )
        if not capability_probe.get("supports_core_context_reads"):
            raise _bad_request(
                "NEMO MCP is reachable but missing required context-read capabilities (context_bootstrap/prime_context/search_memories)",
                error_code="nemo_mcp_capability_mismatch",
            )
        if require_mcp_roundtrip and not capability_probe.get("supports_write_read_roundtrip"):
            raise _bad_request(
                "NEMO MCP write/read continuity probe failed for this environment",
                error_code="nemo_mcp_roundtrip_failed",
            )
    source_json = payload.get("source_json")
    chat_mode = _chat_mode(payload, message, source_json)
    mode_profile = _chat_mode_profile(chat_mode)
    payload_budget = payload.get("token_budget")
    budget_override = int(payload_budget) if isinstance(payload_budget, (int, float)) else None
    portfolio_budget = max(256, budget_override or mode_profile["portfolio_budget"])
    tool_calls: list[dict[str, object]] = []
    agent_trace: list[dict[str, object]] = []
    selected_nemo_tools = _selected_nemo_tools(payload)
    tool_trace_index = 0
    trace_step = 1

    def _trace_tool_call(name: str, detail: str = "") -> None:
        nonlocal trace_step
        agent_trace.append(
            _build_agent_trace_event(
                step=trace_step,
                kind="tool_call",
                label=f"tool_call: {name}",
                status="running",
                detail=detail,
                tool_name=name,
            )
        )
        trace_step += 1

    def _call_nemo(tool_name: str, lifecycle_phase: str, **arguments: Any) -> dict[str, Any]:
        _trace_tool_call(f"nemo_memory.{tool_name}", detail=f"phase={lifecycle_phase}")
        return _nemo_chat_tool_call(
            config,
            tool_calls,
            tool_name,
            lifecycle_phase=lifecycle_phase,
            nemo_mcp_url=nemo_mcp_url,
            allowed_tools=selected_nemo_tools,
            **arguments,
        )

    def _skip_nemo(tool_name: str, summary: str) -> None:
        canonical_name = f"nemo_memory.{tool_name}"
        tool_calls.append(
            {
                "id": f"tool-{uuid4().hex[:8]}",
                "name": canonical_name,
                "tool_name": tool_name,
                "alias_name": f"spacecode.{tool_name}",
                "status": "skipped",
                "summary": summary,
            }
        )

    agent_trace.append(
        _build_agent_trace_event(
            step=trace_step,
            kind="status",
            label="status",
            status="running",
            detail="Agent chat execution started.",
        )
    )
    trace_step += 1

    agent_trace.append(
        _build_agent_trace_event(
            step=trace_step,
            kind="phase",
            label="request_received",
            status="completed",
            detail="Agent request accepted and context build started.",
        )
    )
    trace_step += 1
    agent_trace.append(
        _build_agent_trace_event(
            step=trace_step,
            kind="phase",
            label="mode_selected",
            status="completed",
            detail=f"mode={chat_mode} portfolio_budget={portfolio_budget}",
        )
    )
    trace_step += 1

    run_memory_lookup = _is_nemo_memory_lookup_request(message)
    run_deep_context = chat_mode in {"research", "execution"}
    primary_search_payload: dict[str, Any] = {}
    primary_search_query = _extract_nemo_lookup_query(message) if run_memory_lookup else ""

    bootstrap_payload = _call_nemo("context_bootstrap", "start", task=message, topic="Mission Control conversation", token_budget=portfolio_budget, limit=8)
    tool_trace_index, trace_step = _append_agent_trace_from_tool_calls(agent_trace, tool_calls, start_index=tool_trace_index, step_counter=trace_step)
    if str(nemo_mcp_url).strip().lower() == VSCODE_STDIO_NEMO_URL:
        _skip_nemo("prime_context", "Covered by context_bootstrap prime_context payload; skipped duplicate expensive direct prime_context call.")
    else:
        _call_nemo("prime_context", "start", topic="Mission Control conversation", limit=8)
    tool_trace_index, trace_step = _append_agent_trace_from_tool_calls(agent_trace, tool_calls, start_index=tool_trace_index, step_counter=trace_step)
    if run_deep_context:
        _call_nemo("build_context_portfolio", "plan", task=message, topic="Mission Control conversation", token_budget=portfolio_budget, limit=40)
        tool_trace_index, trace_step = _append_agent_trace_from_tool_calls(agent_trace, tool_calls, start_index=tool_trace_index, step_counter=trace_step)
        _call_nemo("get_context_portfolio_stats", "review")
        tool_trace_index, trace_step = _append_agent_trace_from_tool_calls(agent_trace, tool_calls, start_index=tool_trace_index, step_counter=trace_step)
    else:
        _skip_nemo("build_context_portfolio", "Covered by context_bootstrap portfolio packet for ordinary chat; skipped duplicate stdio call.")
        tool_trace_index, trace_step = _append_agent_trace_from_tool_calls(agent_trace, tool_calls, start_index=tool_trace_index, step_counter=trace_step)
        _skip_nemo("get_context_portfolio_stats", "Deferred portfolio stats for ordinary chat; context_bootstrap and store_conversation remain active.")
        tool_trace_index, trace_step = _append_agent_trace_from_tool_calls(agent_trace, tool_calls, start_index=tool_trace_index, step_counter=trace_step)
    if run_memory_lookup:
        primary_search_payload = _call_nemo("search_memories", "review", query=primary_search_query, limit=min(5, mode_profile["search_limit"]), compact=True, database_filter="ai_memories")
        tool_trace_index, trace_step = _append_agent_trace_from_tool_calls(agent_trace, tool_calls, start_index=tool_trace_index, step_counter=trace_step)
    else:
        _skip_nemo("search_memories", "Skipped expensive semantic search for ordinary chat; NEMO bootstrap/prime context already loaded.")
        tool_trace_index, trace_step = _append_agent_trace_from_tool_calls(agent_trace, tool_calls, start_index=tool_trace_index, step_counter=trace_step)
    if run_deep_context:
        anticipate_payload = _call_nemo("anticipate", "plan", task=message, limit=mode_profile["anticipate_limit"])
    else:
        anticipate_payload = {}
        _skip_nemo("anticipate", "Deferred expensive anticipate for ordinary chat; context_bootstrap and portfolio already loaded proactive NEMO context.")
    tool_trace_index, trace_step = _append_agent_trace_from_tool_calls(agent_trace, tool_calls, start_index=tool_trace_index, step_counter=trace_step)
    memory_lookup_payloads: list[tuple[str, dict[str, Any]]] = [(message, primary_search_payload)]
    if _is_nemo_memory_lookup_request(message):
        lookup_query = _extract_nemo_lookup_query(message)
        if lookup_query and lookup_query.casefold() not in {message.casefold(), primary_search_query.casefold()}:
            lookup_payload = _call_nemo("search_memories", "review", query=lookup_query, limit=min(5, max(3, mode_profile["search_limit"])), compact=True, database_filter="ai_memories")
            memory_lookup_payloads.append((lookup_query, lookup_payload))
            tool_trace_index, trace_step = _append_agent_trace_from_tool_calls(agent_trace, tool_calls, start_index=tool_trace_index, step_counter=trace_step)
    if _is_identity_query(message):
        for identity_query in ("nombre del usuario", "user identity name", "mi nombre es", "user_name", "identity preference"):
            identity_payload = _call_nemo(
                "search_memories",
                "review",
                query=identity_query,
                limit=min(5, max(3, mode_profile["search_limit"])),
                compact=False,
                database_filter="all",
            )
            memory_lookup_payloads.append((identity_query, identity_payload))
            tool_trace_index, trace_step = _append_agent_trace_from_tool_calls(agent_trace, tool_calls, start_index=tool_trace_index, step_counter=trace_step)
    if _is_project_query(message):
        for project_query in ("proyecto activo", "proyectos"):
            project_payload = _call_nemo(
                "search_memories",
                "review",
                query=project_query,
                limit=min(5, max(3, mode_profile["search_limit"])),
                compact=True,
                database_filter="ai_memories",
            )
            memory_lookup_payloads.append((project_query, project_payload))
            tool_trace_index, trace_step = _append_agent_trace_from_tool_calls(agent_trace, tool_calls, start_index=tool_trace_index, step_counter=trace_step)
    source_notes: list[str] = []
    source_items: list[dict[str, object]] = []
    source_urls = _extract_http_urls(message)
    for source_url in source_urls:
        try:
            source, cache_hit = _read_url_source_cached(source_url, timeout_seconds=8, max_chars=2200)
            snippet = str(source.get("content") or "").strip()
            title = str(source.get("title") or source_url).strip()
            source_items.append({"title": title, "url": source_url, "snippet": snippet[:320], "cached": cache_hit})
            source_notes.append(f"[{title}] {source_url}\n{snippet[:360]}")
            tool_calls.append(
                {
                    "id": f"tool-{uuid4().hex[:8]}",
                    "name": "mission_control.read_url",
                    "status": "completed",
                    "summary": f"Read source: {title}{' (cache)' if cache_hit else ''}",
                }
            )
            tool_trace_index, trace_step = _append_agent_trace_from_tool_calls(agent_trace, tool_calls, start_index=tool_trace_index, step_counter=trace_step)
            _call_nemo(
                "cognitive_ingest",
                "review",
                content=f"Source note from user URL: {title} ({source_url})\n{snippet[:1200]}",
                memory_type="evidence",
                tags=("spacecode", "source", "url", "agent-chat"),
                context="URL source extracted by Space Code chat reader",
            )
            tool_trace_index, trace_step = _append_agent_trace_from_tool_calls(agent_trace, tool_calls, start_index=tool_trace_index, step_counter=trace_step)
        except Exception as error:  # noqa: BLE001
            tool_calls.append(
                {
                    "id": f"tool-{uuid4().hex[:8]}",
                    "name": "mission_control.read_url",
                    "status": "failed",
                    "summary": f"Failed to read {source_url}: {error}",
                }
            )
            tool_trace_index, trace_step = _append_agent_trace_from_tool_calls(agent_trace, tool_calls, start_index=tool_trace_index, step_counter=trace_step)
    actions: list[dict[str, object]] = []
    selected: dict[str, Any] | None = None
    mergeable = False
    risk_flags: tuple[str, ...] = ()
    changed_files: tuple[str, ...] = ()
    if isinstance(source_json, str) and source_json:
        selected = load_headless_result_json(source_json)
        summary = selected.get("task", {}) if isinstance(selected.get("task"), dict) else {}
        run_payload = selected.get("run", {}) if isinstance(selected.get("run"), dict) else {}
        tool_calls.append(
            {
                "id": f"tool-{uuid4().hex[:8]}",
                "name": "mission_control.inspect_run",
                "status": "completed",
                "summary": f"Selected {summary.get('id', 'task')} / {run_payload.get('id', 'run')}.",
            }
        )
        tool_trace_index, trace_step = _append_agent_trace_from_tool_calls(agent_trace, tool_calls, start_index=tool_trace_index, step_counter=trace_step)
        plan = build_merge_plan(selected)
        mergeable = plan.mergeable
        risk_flags = plan.risk_flags
        changed_files = tuple(item.path for item in plan.files)
        tool_calls.append(
            {
                "id": f"tool-{uuid4().hex[:8]}",
                "name": "mission_control.build_merge_plan",
                "status": "completed" if mergeable else "needs_attention",
                "summary": f"Mergeable={str(mergeable).lower()} files={len(plan.files)} risks={','.join(risk_flags) or 'none'}.",
            }
        )
        tool_trace_index, trace_step = _append_agent_trace_from_tool_calls(agent_trace, tool_calls, start_index=tool_trace_index, step_counter=trace_step)
        if mergeable:
            actions.append(
                {
                    "id": "apply-selected-run",
                    "kind": "apply",
                    "label": "Apply",
                    "summary": "Apply the reviewed sandbox changes to the workspace.",
                    "payload": {"source_json": source_json, "approve_review": True},
                }
            )
        actions.append(
            {
                "id": "evaluate-selected-run",
                "kind": "evaluate",
                "label": "Evaluate",
                "summary": "Evaluate readiness and SPEC-10 for the selected run.",
                "payload": {"source_json": source_json},
            }
        )
        actions.append(
            {
                "id": "review-selected-run",
                "kind": "review",
                "label": "Review",
                "summary": "Build or refresh the merge plan for the selected run.",
                "payload": {"source_json": source_json},
            }
        )
    text = message.lower()
    selected_objective = "the selected run"
    if selected and isinstance(selected.get("task"), dict):
        selected_objective = str(selected["task"].get("objective") or selected_objective)
    if _is_self_interface_request(message):
        actions.append(_self_interface_action(message, payload))
    if _is_self_improvement_request(message):
        actions.append(_self_platform_action(message, payload))
    if "continue" in text or "continua" in text or "seguir" in text:
        actions.append(
            {
                "id": "continue-handoff",
                "kind": "continue",
                "label": "Continue",
                "summary": "Start a new async handoff that continues from the selected context.",
                "payload": {
                    "objective": f"Continue: {selected_objective}",
                    "acceptance_criteria": "continuation preserves existing review context",
                    "validation_commands": "python -m unittest",
                    "target_files": "\n".join(changed_files),
                    "provider": payload.get("provider") or "subprocess",
                    "timeout_seconds": payload.get("timeout_seconds") or "300",
                },
            }
        )
    if "revise" in text or "revisa" in text or "cambia" in text or "ajusta" in text:
        actions.append(
            {
                "id": "revise-handoff",
                "kind": "revise",
                "label": "Revise",
                "summary": "Start a focused revision handoff for the selected run.",
                "payload": {
                    "objective": f"Revise selected run: {message}",
                    "acceptance_criteria": "revision addresses the requested feedback",
                    "validation_commands": "python -m unittest",
                    "target_files": "\n".join(changed_files),
                    "provider": payload.get("provider") or "subprocess",
                    "timeout_seconds": payload.get("timeout_seconds") or "300",
                },
            }
        )
    actions.extend(_run_or_handoff_actions(message, payload, selected_objective, changed_files))
    if not actions:
        actions.append(
            {
                "id": "draft-revision",
                "kind": "revise",
                "label": "Revise",
                "summary": "Turn this prompt into a focused sandbox revision.",
                "payload": {
                    "objective": message,
                    "acceptance_criteria": "passes validation",
                    "validation_commands": "python -m unittest",
                    "target_files": "\n".join(changed_files),
                    "provider": payload.get("provider") or "subprocess",
                    "timeout_seconds": payload.get("timeout_seconds") or "300",
                },
            }
        )
    _call_nemo(
        "store_conversation",
        "review",
        content=f"Space Code chat user request: {message}",
        role="user",
        topic="Mission Control conversation",
        tags=("spacecode", "agent-chat"),
        atom_type=MemoryAtomType.SESSION_SUMMARY.value,
        source_scope="spacecode_chat",
        importance=8 if actions else 6,
    )
    tool_trace_index, trace_step = _append_agent_trace_from_tool_calls(agent_trace, tool_calls, start_index=tool_trace_index, step_counter=trace_step)
    # Proactively persist user data when they ask to save/remember something
    if _is_memory_store_request(message):
        declared_name = _extract_declared_user_name(message)
        if declared_name:
            _call_nemo(
                "cognitive_ingest",
                "review",
                content=f'{{"user_name": {declared_name!r}, "source_message": {message!r}}}',
                memory_type="preference",
                tags=("spacecode", "user-data", "identity", "agent-chat"),
                context="User explicitly provided their name in Space Code chat",
            )
        else:
            _call_nemo(
                "cognitive_ingest",
                "review",
                content=message,
                memory_type="preference",
                tags=("spacecode", "user-data", "agent-chat"),
                context="User explicitly asked to store this in NEMO memory from Space Code chat",
            )
        tool_trace_index, trace_step = _append_agent_trace_from_tool_calls(agent_trace, tool_calls, start_index=tool_trace_index, step_counter=trace_step)
    risk_note = f" Risks: {', '.join(risk_flags)}." if risk_flags else ""
    # --- Cognitive preload: merge anticipate results + self-mod continuity ---
    _cognitive_parts: list[str] = []
    _anticipated = anticipate_payload.get("memories") or anticipate_payload.get("anticipated") or []
    if _anticipated:
        _cog_items = "; ".join(
            str(item.get("content") or item.get("summary") or "")[:120]
            for item in _anticipated[:3]
            if isinstance(item, dict)
        )
        if _cog_items:
            _cognitive_parts.append(f"Reflexions/risks retrieved for this task: {_cog_items}")
    if source_notes:
        _cognitive_parts.append("Sources read from user-provided URLs:\n" + "\n\n".join(source_notes[:2]))
    if config.memory_db is not None:
        try:
            _continuity = get_self_mod_continuity(config.memory_db, task_objective=message, limit=3)
            _cont_items = _continuity.get("items") or []
            if _cont_items:
                _cont_text = "; ".join(
                    str(item.get("content") or "")[:100] for item in _cont_items[:2] if isinstance(item, dict)
                )
                if _cont_text:
                    _cognitive_parts.append(f"Continuity from similar past tasks: {_cont_text}")
        except Exception:  # noqa: BLE001 — best-effort, never block the chat
            pass
    if _is_identity_query(message) and config.memory_db is not None:
        known_user_name = ""
        memories = _unique_memories_from_payloads(memory_lookup_payloads)
        if isinstance(memories, list) and memories:
            first = memories[0]
            if isinstance(first, dict):
                raw_name = first.get("user_name")
                if isinstance(raw_name, str) and raw_name.strip():
                    known_user_name = raw_name.strip()
        if not known_user_name:
            known_user_name = _extract_user_name_from_memories(memories)
        if not known_user_name and isinstance(bootstrap_payload, dict):
            known_user_name = _extract_name_from_bootstrap(bootstrap_payload)
        if known_user_name:
            _cognitive_parts.append(f"Known user name from NEMO: {known_user_name}")
    if isinstance(bootstrap_payload, dict):
        bootstrap_context = _bootstrap_context_text(bootstrap_payload).strip()
        if bootstrap_context:
            _cognitive_parts.append(f"Bootstrapped NEMO context: {bootstrap_context}")
    _cognitive_parts.append(_nemo_bootstrap_packet(tool_calls))
    cognitive_preload = "\n".join(_cognitive_parts)
    context_summary = _agent_context_summary(
        selected,
        changed_files,
        risk_flags,
        mergeable,
        cognitive_preload=cognitive_preload,
        max_context_chars=_agent_context_char_budget(payload, mode_profile["context_chars"], mode_profile["context_window_tokens"]),
    )
    verified_memory_response = _verified_nemo_memory_response(message, memory_lookup_payloads)
    if verified_memory_response:
        response = verified_memory_response
        tool_calls.append(
            {
                "id": f"tool-{uuid4().hex[:8]}",
                "name": "mission_control.verified_nemo_answer",
                "status": "completed",
                "summary": "Answered from real NEMO MCP search results; skipped model-only memory claims.",
            }
        )
        tool_trace_index, trace_step = _append_agent_trace_from_tool_calls(agent_trace, tool_calls, start_index=tool_trace_index, step_counter=trace_step)
    elif provider == "fake":
        response = (
            "[fake planner] Built an operational response using local Mission Control context. "
            "Use the suggested action buttons to continue safely."
        )
        tool_calls.append(
            {
                "id": f"tool-{uuid4().hex[:8]}",
                "name": "lmstudio.chat_completions",
                "status": "skipped",
                "summary": "Skipped real model call because provider=fake.",
            }
        )
        tool_trace_index, trace_step = _append_agent_trace_from_tool_calls(agent_trace, tool_calls, start_index=tool_trace_index, step_counter=trace_step)
    else:
        agent_trace.append(
            _build_agent_trace_event(
                step=trace_step,
                kind="phase",
                label="model_generation",
                status="running",
                detail="Generating response with configured LM Studio model.",
            )
        )
        trace_step += 1
        try:
            history = [h for h in (payload.get("history") or []) if isinstance(h, dict) and h.get("role") in {"user", "assistant"} and isinstance(h.get("content"), str)]
            response = _lmstudio_chat_completion(payload, message, context_summary, history=history)
            raw_tool_fallback = _raw_tool_name_fallback(response, message)
            if raw_tool_fallback:
                response = raw_tool_fallback
                tool_calls.append(
                    {
                        "id": f"tool-{uuid4().hex[:8]}",
                        "name": "mission_control.normalized_model_output",
                        "status": "completed",
                        "summary": "Normalized raw tool-name-only model output into an operational chat response.",
                    }
                )
            tool_calls.append(
                {
                    "id": f"tool-{uuid4().hex[:8]}",
                    "name": "lmstudio.chat_completions",
                    "status": "completed",
                    "summary": f"Model={_chat_model(payload)} base_url={_chat_base_url(payload)}.",
                }
            )
            tool_trace_index, trace_step = _append_agent_trace_from_tool_calls(agent_trace, tool_calls, start_index=tool_trace_index, step_counter=trace_step)
        except ValueError as error:
            tool_calls.append(
                {
                    "id": f"tool-{uuid4().hex[:8]}",
                    "name": "lmstudio.chat_completions",
                    "status": "failed",
                    "summary": str(error),
                }
            )
            tool_trace_index, trace_step = _append_agent_trace_from_tool_calls(agent_trace, tool_calls, start_index=tool_trace_index, step_counter=trace_step)
            response = (
                "[real-mode fallback] LM Studio is unavailable right now. "
                "I prepared safe next actions from Mission Control state so you can continue without blocking."
            )
    if _is_artifact_request(message) and not _has_typed_artifact_fence(response):
        response = _artifact_fallback_response(message, tool_calls)
        tool_calls.append(
            {
                "id": f"tool-{uuid4().hex[:8]}",
                "name": "mission_control.artifact_fallback",
                "status": "completed",
                "summary": "Generated deterministic html_artifact because the model response did not include a typed artifact fence.",
            }
        )
        tool_trace_index, trace_step = _append_agent_trace_from_tool_calls(agent_trace, tool_calls, start_index=tool_trace_index, step_counter=trace_step)
    # Detect LLM-driven tool calls and convert to AgentActions
    for inv in _parse_llm_tool_calls(response):
        action = _llm_tool_call_to_action(inv, server, payload)
        if action:
            actions.append(action)
    agent_trace.append(
        _build_agent_trace_event(
            step=trace_step,
            kind="phase",
            label="response_ready",
            status="completed",
            detail="Assistant response assembled with actions and tool outputs.",
        )
    )
    trace_step += 1
    elapsed_ms = int((time.perf_counter() - request_started) * 1000)
    agent_trace.append(
        _build_agent_trace_event(
            step=trace_step,
            kind="finalize",
            label="finalize",
            status="completed",
            detail="Trace finalized for this response.",
            duration_ms=elapsed_ms,
        )
    )
    trace_step += 1
    agent_trace.append(
        _build_agent_trace_event(
            step=trace_step,
            kind="status",
            label="status",
            status="completed",
            detail="Agent chat execution completed.",
            duration_ms=elapsed_ms,
        )
    )

    chat_failures = sum(1 for item in tool_calls if str(item.get("status") or "") == "failed")
    chat_tool_fail_rate = (chat_failures / len(tool_calls)) if tool_calls else 0.0
    settings_for_chat_metrics = _load_settings(config)
    metrics = settings_for_chat_metrics.get("chat_metrics")
    history = metrics if isinstance(metrics, list) else []
    history.append(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "duration_ms": elapsed_ms,
            "tool_fail_rate": round(chat_tool_fail_rate, 4),
            "trace_events_count": len(agent_trace),
        }
    )
    settings_for_chat_metrics["chat_metrics"] = history[-300:]
    _save_settings(config, settings_for_chat_metrics)
    _record_source_analytics(config, chat_mode=chat_mode, source_items=source_items)
    return {
        "ok": True,
        "message": {
            "id": f"msg-{uuid4().hex[:8]}",
            "role": "assistant",
            "content": f"{response}{risk_note}",
            "tool_calls": tool_calls,
            "sources": source_items,
            "agent_trace": agent_trace,
            "actions": actions,
        },
    }


def api_rollback(config: MissionControlServerConfig, payload: dict[str, object]) -> dict[str, object]:
    result = MergeApplyResult.from_dict(json.loads(Path(_apply_json(payload)).read_text(encoding="utf-8")))
    rollback = rollback_apply_result(result, approve_review=bool(payload.get("approve_review")))
    return {"ok": True, "result": rollback.to_dict(), "state": api_state(config)}


def api_decision_log_append(config: MissionControlServerConfig, payload: dict[str, object]) -> dict[str, object]:
    source_json = _source_json(payload)
    entry = _decision_log_entry(payload)
    path = _decision_log_path(source_json)
    entries: list[dict[str, object]] = []
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                entries = [item for item in loaded if isinstance(item, dict)]
        except (json.JSONDecodeError, OSError):
            entries = []
    entries.insert(0, entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries[:DECISION_LOG_LIMIT], indent=2, sort_keys=True), encoding="utf-8")
    return {"ok": True, "entry": entry}


def _check_lm_studio_reachable(base_url: str) -> bool:
    """Check if LM Studio is reachable via a quick health check."""
    try:
        # Try to reach /v1/models endpoint (standard OpenAI-compatible endpoint)
        check_url = base_url.rstrip('/') + '/models'
        request = urllib.request.Request(check_url, method='GET')
        request.add_header('Accept', 'application/json')
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.status == 200
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return False


def _get_nemo_database_size_mb(memory_db_path: Path | None) -> float | None:
    """Get NEMO database size in MB, or None if doesn't exist."""
    if memory_db_path is None or not memory_db_path.exists():
        return None
    try:
        size_bytes = memory_db_path.stat().st_size
        return round(size_bytes / (1024 * 1024), 2)
    except (OSError, ValueError):
        return None


def _check_disk_space_sufficient(path: Path, min_mb: int = 500) -> bool:
    """Check if at least min_mb of disk space is available."""
    try:
        usage = shutil.disk_usage(str(path))
        available_mb = usage.free / (1024 * 1024)
        return available_mb >= min_mb
    except (OSError, ValueError):
        return False


def _check_directory_permissions(path: Path) -> bool:
    """Check if directory is readable and writable."""
    try:
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
        return os.access(str(path), os.R_OK | os.W_OK)
    except OSError:
        return False


def api_health(config: MissionControlServerConfig) -> dict[str, object]:
    """
    Health check endpoint for desktop app and monitoring.
    Returns diagnostic info without being too intrusive.
    """
    settings = _load_settings(config)
    lm_studio_url = settings.get("model_base_url", "http://127.0.0.1:1234/v1")
    
    lm_studio_reachable = _check_lm_studio_reachable(str(lm_studio_url))
    nemo_db_size = _get_nemo_database_size_mb(config.memory_db)
    nemo_available = config.memory_db is not None and nemo_db_size is not None
    
    return {
        "status": "healthy",
        "version": "0.1.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "backend": {
            "type": "mission_control",
            "port": 8787,
            "uptime_seconds": int(time.time() - _SERVER_START_TIME),
        },
        "lm_studio": {
            "reachable": lm_studio_reachable,
            "endpoint": str(lm_studio_url),
        },
        "nemo": {
            "available": nemo_available,
            "database_path": str(config.memory_db) if config.memory_db else None,
            "database_size_mb": nemo_db_size,
        },
        "repair": {
            "queue_depth": 0,  # Would need job manager access to populate
            "avg_repair_time_seconds": 0,
        },
    }


def api_startup(config: MissionControlServerConfig) -> dict[str, object]:
    """
    Startup readiness check for desktop app first-run setup.
    Detects issues and suggests fixes.
    """
    settings = _load_settings(config)
    lm_studio_url = settings.get("model_base_url", "http://127.0.0.1:1234/v1")
    
    checks: dict[str, bool] = {
        "lm_studio_reachable": _check_lm_studio_reachable(str(lm_studio_url)),
        "nemo_database_exists": config.memory_db is not None and config.memory_db.exists(),
        "disk_space_sufficient": _check_disk_space_sufficient(config.repo_path, min_mb=500),
        "permissions_ok": _check_directory_permissions(config.runtimes_path),
    }
    
    ready = all(checks.values())
    missing: list[str] = []
    recommended_actions: list[str] = []
    
    # Generate missing/recommended based on failed checks
    if not checks["lm_studio_reachable"]:
        missing.append("lm_studio_not_reachable")
        recommended_actions.append(f"Start LM Studio on {lm_studio_url}")
        recommended_actions.append(f"Or set LM_STUDIO_URL environment variable")
    
    if not checks["nemo_database_exists"] and config.memory_db is not None:
        missing.append("nemo_database_missing")
        recommended_actions.append(f"Initialize NEMO database at {config.memory_db}")
        recommended_actions.append(f"Or leave NEMO disabled for this session")
    
    if not checks["disk_space_sufficient"]:
        missing.append("insufficient_disk_space")
        recommended_actions.append("Free at least 500 MB of disk space")
    
    if not checks["permissions_ok"]:
        missing.append("permission_denied")
        recommended_actions.append(f"Check permissions for {config.runtimes_path}")
    
    return {
        "ready": ready,
        "checks": checks,
        "missing": missing,
        "recommended_actions": recommended_actions,
        "config": {
            "repo_path": str(config.repo_path),
            "runtime_path": str(config.runtimes_path),
            "memory_db": str(config.memory_db) if config.memory_db else None,
            "lm_studio_url": str(lm_studio_url),
        },
    }


def api_stats(config: MissionControlServerConfig, jobs: HandoffJobManager | None = None) -> dict[str, object]:
    """
    Aggregated statistics endpoint for performance monitoring.
    Returns timing data, repair metrics, and queue status.
    """
    job_items = jobs.list() if jobs else []
    job_count = len(job_items)
    active_jobs = sum(1 for job in job_items if str(job.get("status") or "") in ("running", "paused"))
    completed_jobs = sum(1 for job in job_items if str(job.get("status") or "") in ("completed", "failed"))
    settings = _load_settings(config)
    source_stats = _source_analytics_summary(settings.get("source_analytics"))
    raw_chat_metrics = settings.get("chat_metrics")
    chat_metrics = [item for item in raw_chat_metrics if isinstance(item, dict)] if isinstance(raw_chat_metrics, list) else []
    avg_chat_latency = round(sum(float(item.get("duration_ms") or 0.0) for item in chat_metrics) / len(chat_metrics), 1) if chat_metrics else 0.0
    avg_tool_fail_rate = round(sum(float(item.get("tool_fail_rate") or 0.0) for item in chat_metrics) / len(chat_metrics), 4) if chat_metrics else 0.0
    avg_trace_events = round(sum(float(item.get("trace_events_count") or 0.0) for item in chat_metrics) / len(chat_metrics), 2) if chat_metrics else 0.0
    
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "jobs": {
            "total": job_count,
            "active": active_jobs,
            "completed": completed_jobs,
        },
        "repair": {
            "total_attempts": 0,  # Could be populated from run history if available
            "avg_attempts_per_repair": 0.0,
            "repair_success_rate": 0.0,
        },
        "performance": {
            "avg_job_duration_seconds": 0.0,
            "avg_mutation_duration_ms": 0,
            "avg_validation_duration_ms": 0,
            "avg_agent_chat_latency_ms": avg_chat_latency,
            "avg_agent_tool_fail_rate": avg_tool_fail_rate,
            "avg_agent_trace_events": avg_trace_events,
        },
        "sources": source_stats,
    }


def api_record_stats(server: "MissionControlHttpServer", payload: dict[str, object]) -> dict[str, object]:
    """
    Record timing statistics from a completed job for aggregation.
    Used by clients to track performance metrics.
    """
    event_type = payload.get("event_type", "unknown")
    duration_ms = int(payload.get("duration_ms", 0)) if payload.get("duration_ms") else 0
    job_id = payload.get("job_id", "")
    
    # Validate payload
    if not event_type or not duration_ms:
        return {"error": "missing_event_type_or_duration_ms", "recorded": False}
    
    # TODO: Persist stats to a metrics database or file for aggregation
    # For now, just acknowledge receipt
    return {
        "recorded": True,
        "event_type": event_type,
        "duration_ms": duration_ms,
        "job_id": job_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _job_runtime_id(job: "HandoffJob") -> str:
    from nemo_coding_platform.core.worktree_runtime import runtime_slug
    return f"{runtime_slug(job.task_id)}-{runtime_slug(job.run_id)}"


def _job_worktree_path(config: "MissionControlServerConfig", job: "HandoffJob") -> Path:
    return Path(config.repo_path) / ".worktrees" / _job_runtime_id(job)


def _job_worktree_branch(job: "HandoffJob") -> str:
    from nemo_coding_platform.core.worktree_runtime import worktree_branch_name
    return worktree_branch_name(_job_runtime_id(job))


def api_worktree_diff(config: "MissionControlServerConfig", job_id: str, jobs: "HandoffJobManager") -> dict[str, object]:
    try:
        job = jobs.get(job_id)
    except FileNotFoundError:
        return {"error": f"job not found: {job_id}"}
    from nemo_coding_platform.core.worktree_runtime import worktree_diff
    branch = _job_worktree_branch(job)
    wt_path = _job_worktree_path(config, job)
    diff = worktree_diff(Path(config.repo_path), branch)
    return {
        "diff": diff,
        "branch": branch,
        "runtime_id": _job_runtime_id(job),
        "worktree_exists": wt_path.exists(),
    }


def api_worktree_merge(config: "MissionControlServerConfig", job_id: str, payload: dict[str, object], jobs: "HandoffJobManager") -> dict[str, object]:
    if not payload.get("approved"):
        return {"error": "merge requires approved=true in request body"}
    try:
        job = jobs.get(job_id)
    except FileNotFoundError:
        return {"error": f"job not found: {job_id}"}
    from nemo_coding_platform.core.worktree_runtime import cleanup_git_worktree, merge_worktree_to_main
    branch = _job_worktree_branch(job)
    wt_path = _job_worktree_path(config, job)
    message = str(payload.get("message") or f"merge worktree {branch}")
    merged = merge_worktree_to_main(Path(config.repo_path), branch, message)
    if merged:
        cleanup_git_worktree(Path(config.repo_path), wt_path, branch)
    return {"merged": merged, "branch": branch}


def api_worktree_cleanup(config: "MissionControlServerConfig", job_id: str, jobs: "HandoffJobManager") -> dict[str, object]:
    try:
        job = jobs.get(job_id)
    except FileNotFoundError:
        return {"error": f"job not found: {job_id}"}
    from nemo_coding_platform.core.worktree_runtime import cleanup_git_worktree
    branch = _job_worktree_branch(job)
    wt_path = _job_worktree_path(config, job)
    cleanup_git_worktree(Path(config.repo_path), wt_path, branch)
    return {"cleaned_up": True, "branch": branch}


def api_run_timeline(
    config: "MissionControlServerConfig",
    job_id: str,
    jobs: "HandoffJobManager",
) -> dict[str, object]:
    with jobs._lock:
        job = jobs._jobs.get(job_id)
        if not job:
            return {"error": f"job not found: {job_id}"}
        return {
            "job_id": job_id,
            "status": str(job.status),
            "timeline": list(job.timeline),
            "event_count": len(job.timeline),
        }


def api_run_public_html_files(
    config: "MissionControlServerConfig",
    job_id: str,
    jobs: "HandoffJobManager",
) -> dict[str, object]:
    """Return public HTML files created by a job, with their content, for artifact injection."""
    with jobs._lock:
        job = jobs._jobs.get(job_id)
        if not job:
            return {"error": f"job not found: {job_id}", "files": []}
        run_json_path = Path(job.run_json) if job.run_json else None
    if not run_json_path or not run_json_path.exists():
        return {"files": [], "job_id": job_id}
    try:
        run_data = json.loads(run_json_path.read_text(encoding="utf-8"))
    except Exception:
        return {"files": [], "job_id": job_id}
    changed = run_data.get("mutation_result", {}).get("changed_files", [])
    PUBLIC_PREFIX = "apps/mission-control/public/"
    result = []
    for rel_path in changed:
        if rel_path.startswith(PUBLIC_PREFIX) and rel_path.endswith(".html"):
            abs_path = Path(config.repo_path) / rel_path
            if abs_path.exists():
                try:
                    content = abs_path.read_text(encoding="utf-8")
                    url_path = "/" + rel_path[len(PUBLIC_PREFIX):]
                    result.append({"path": rel_path, "url": url_path, "content": content})
                except Exception:
                    pass
    return {"files": result, "job_id": job_id}


def api_permission_grant(
    config: "MissionControlServerConfig", job_id: str, payload: dict[str, object], jobs: "HandoffJobManager"
) -> dict[str, object]:
    note = str(payload.get("note") or "")
    job = jobs.grant_permission(config, job_id, note)
    return {"granted": True, "job_id": job_id, "status": job.status}


def api_permission_deny(
    config: "MissionControlServerConfig", job_id: str, payload: dict[str, object], jobs: "HandoffJobManager"
) -> dict[str, object]:
    note = str(payload.get("note") or "")
    job = jobs.deny_permission(job_id, note)
    return {"denied": True, "job_id": job_id, "status": job.status}


class MissionControlRequestHandler(BaseHTTPRequestHandler):
    server: "MissionControlHttpServer"
    _RATE_LIMIT_EXEMPT_PATHS = frozenset({
        "/api/refresh",
        "/api/state",
        "/api/stats",
        "/api/health",
        "/api/jobs",
        "/api/job",
        "/api/job/signal",
        "/api/nemo/mcp-status",
        "/api/self-mod/insights",
        "/api/nemo/risk-map",
        "/api/nemo/cognitive-stats",
    })

    def do_OPTIONS(self) -> None:
        _json_response(self, 204, {})

    def do_GET(self) -> None:
        if not self._check_rate_limit():
            return
        route = urlparse(self.path).path
        if route == "/api/health":
            self._handle(lambda _: api_health(self.server.config), {})
            return
        if route == "/api/startup":
            self._handle(lambda _: api_startup(self.server.config), {})
            return
        if route == "/api/stats":
            self._handle(lambda _: api_stats(self.server.config, self.server.jobs), {})
            return
        if route.startswith("/api/handoff-job/") and route.endswith("/signal"):
            job_id = route[len("/api/handoff-job/") : -len("/signal")].strip("/")
            self._handle(lambda payload: api_job_signal(self.server, {**payload, "job_id": job_id}), {})
            return
        if route == "/api/jobs":
            self._handle(lambda _: api_jobs(self.server), {})
            return
        if route == "/api/jobs/orphans":
            self._handle(lambda _: api_orphan_jobs(self.server, {}), {})
            return
        if route == "/api/browser":
            self._handle(lambda _: api_browser_state(self.server.config), {})
            return
        if route == "/api/extensions":
            self._handle(lambda _: api_extensions_get(self.server.config), {})
            return
        if route == "/api/git/status":
            self._handle(lambda _: api_git_status(self.server.config), {})
            return
        if route == "/api/git/branches":
            self._handle(lambda _: api_git_branches(self.server.config), {})
            return
        if route == "/api/git/remotes":
            self._handle(lambda _: api_git_remotes(self.server.config), {})
            return
        if route == "/api/applies":
            self._handle(lambda _: api_applies(self.server.config), {})
            return
        if route == "/api/kpis":
            self._handle(lambda _: api_kpis(self.server.config), {})
            return
        if route.startswith("/api/artifacts/image/"):
            image_name = Path(route[len("/api/artifacts/image/"):]).name
            # Check runtimes_path first (plan loop writes here), then legacy nemo-runtimes
            image_path = self.server.config.runtimes_path / "mission-control" / "artifacts" / "images" / image_name
            if not image_path.exists():
                image_path = Path(self.server.config.repo_path) / ".nemo-runtimes" / "mission-control" / "artifacts" / "images" / image_name
            if image_path.exists() and image_path.is_file():
                try:
                    image_data = image_path.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(image_data)))
                    self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:5173")
                    self.send_header("Cache-Control", "public, max-age=86400")
                    self.end_headers()
                    self.wfile.write(image_data)
                except OSError:
                    _json_response(self, 500, {"error": "failed_to_serve_image"})
            else:
                _json_response(self, 404, {"error": "image_not_found"})
            return
        if route.startswith("/api/run/") and route.endswith("/worktree-diff"):
            job_id = route[len("/api/run/"): -len("/worktree-diff")].strip("/")
            self._handle(lambda _: api_worktree_diff(self.server.config, job_id, self.server.jobs), {})
            return
        if route.startswith("/api/run/") and route.endswith("/timeline/stream"):
            job_id = route[len("/api/run/"): -len("/timeline/stream")].strip("/")
            self._handle_timeline_sse(job_id)
            return
        if route.startswith("/api/run/") and route.endswith("/timeline"):
            job_id = route[len("/api/run/"): -len("/timeline")].strip("/")
            self._handle(lambda _: api_run_timeline(self.server.config, job_id, self.server.jobs), {})
            return
        if route.startswith("/api/run/") and route.endswith("/public-html-files"):
            job_id = route[len("/api/run/"): -len("/public-html-files")].strip("/")
            self._handle(lambda _: api_run_public_html_files(self.server.config, job_id, self.server.jobs), {})
            return
        if route == "/api/agent/browser-sessions":
            self._handle(lambda _: api_browser_sessions(self.server.config), {})
            return
        if route.startswith("/api/browser/") and route.endswith("/frame"):
            session_id = route[len("/api/browser/"): -len("/frame")].strip("/")
            self._handle_browser_frame(session_id)
            return
        if route.startswith("/api/browser/") and route.endswith("/info"):
            session_id = route[len("/api/browser/"): -len("/info")].strip("/")
            from nemo_coding_platform.browser_agent import get_browser_info
            info = get_browser_info(session_id)
            if info is None:
                _json_response(self, 404, {"error": "session_not_found"})
            else:
                _json_response(self, 200, info)
            return
        if route == "/api/vault/credentials":
            self._handle(lambda _: api_vault_list(self.server.config), {})
            return
        if route == "/api/models":
            self._handle(lambda _: api_models(self.server.config), {})
            return
        if route != "/api/state":
            _json_response(self, 404, {"error": "not_found"})
            return
        self._handle(lambda _: api_state(self.server.config, self.server.jobs), {})

    def _handle_plan_sse(self, payload: dict[str, object]) -> None:
        """Stream plan loop iterations as Server-Sent Events."""
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:5173")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.end_headers()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return

        def _send_event(data: dict[str, object]) -> bool:
            try:
                line = ("data: " + json.dumps(data, sort_keys=True) + "\n\n").encode("utf-8")
                self.wfile.write(line)
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                return False

        plan_job_id = f"plan-{uuid4().hex[:12]}"
        try:
            for event in api_agent_plan_gen(self.server.config, payload, job_id=plan_job_id):
                if not _send_event(event):
                    break
        except ApiRequestError as error:
            _send_event({"type": "error", "error": str(error), "error_code": error.error_code})
        except Exception as error:  # noqa: BLE001
            _send_event({"type": "error", "error": str(error), "error_code": "internal_error"})
        finally:
            with _plan_jobs_lock:
                _plan_jobs.pop(plan_job_id, None)

    def _handle_browser_task_sse(self, payload: dict[str, object]) -> None:
        """Stream browser agent steps as Server-Sent Events."""
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:5173")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.end_headers()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return

        def _send(data: dict[str, object]) -> bool:
            try:
                line = ("data: " + json.dumps(data, sort_keys=True) + "\n\n").encode("utf-8")
                self.wfile.write(line)
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                return False

        url = str(payload.get("url") or "").strip()
        task = str(payload.get("task") or "").strip()
        credential_alias = str(payload.get("credential_alias") or "").strip() or None
        try:
            max_steps = max(1, min(20, int(payload.get("max_steps") or 10)))
        except (ValueError, TypeError):
            max_steps = 10
        session_id = str(payload.get("session_id") or f"browser-{uuid4().hex[:12]}")

        if not url or not task:
            _send({"type": "error", "message": "url and task are required"})
            return

        config = self.server.config
        settings = _load_settings(config)
        artifacts_dir = config.runtimes_path / "mission-control" / "artifacts" / "images"
        base_url = str(payload.get("model_base_url") or settings.get("model_base_url") or "http://localhost:1234/v1")
        api_key = str(payload.get("api_key") or settings.get("api_key") or os.environ.get("LMSTUDIO_API_KEY") or "lm-studio")
        vlm_model = _resolve_vlm_model(base_url, api_key=api_key)  # None → screenshot-only; non-vision LLMs can't handle images
        vault_db = _vault_db(config) if credential_alias else None

        from nemo_coding_platform.browser_agent import execute_browser_task
        gen = execute_browser_task(
            session_id=session_id,
            url=url,
            task=task,
            credential_alias=credential_alias,
            max_steps=max_steps,
            artifacts_dir=artifacts_dir,
            base_url=base_url,
            vlm_model=vlm_model,
            vault_db_path=vault_db,
            api_key=api_key,
        )
        try:
            for event in gen:
                if not _send(event):
                    break
        except Exception as exc:
            _send({"type": "error", "message": str(exc)})
        finally:
            gen.close()

    def _handle_browser_frame(self, session_id: str) -> None:
        """Serve a single PNG screenshot of the live browser session."""
        from nemo_coding_platform.browser_agent import take_browser_frame
        frame = take_browser_frame(session_id)
        if frame is None:
            _json_response(self, 404, {"error": "session_not_found"})
            return
        try:
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(frame)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(frame)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def _handle_timeline_sse(self, job_id: str) -> None:
        """Stream HandoffJob.timeline events as Server-Sent Events."""
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:5173")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.end_headers()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return

        def _send(data: dict[str, object]) -> bool:
            try:
                line = ("data: " + json.dumps(data, sort_keys=True) + "\n\n").encode("utf-8")
                self.wfile.write(line)
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                return False

        _TERMINAL = {
            HandoffJobStatus.COMPLETED,
            HandoffJobStatus.FAILED,
            HandoffJobStatus.PERMISSION_DENIED,
            HandoffJobStatus.CANCELLED,
            HandoffJobStatus.ORPHANED,
            "paused",  # run thread exits on pause; stream must not hang
        }
        _POLL = 0.5  # seconds
        offset = 0
        jobs = self.server.jobs

        while True:
            with jobs._lock:
                job = jobs._jobs.get(job_id)
                if job is None:
                    _send({"kind": "stream_end", "status": "not_found"})
                    return
                events_slice = list(job.timeline[offset:])
                current_status = str(job.status)

            for event in events_slice:
                if not _send(event):
                    return
                offset += 1

            if current_status in _TERMINAL and not events_slice:
                _send({"kind": "stream_end", "status": current_status})
                return

            time.sleep(_POLL)

    def do_POST(self) -> None:
        if not self._check_rate_limit():
            return
        # SSE streaming for plan endpoint (when client requests event-stream)
        if urlparse(self.path).path == "/api/agent/plan" and "text/event-stream" in self.headers.get("Accept", ""):
            try:
                payload = _load_body(self)
            except ApiRequestError as error:
                _json_response(self, error.status_code, {"error": str(error), "error_code": error.error_code})
                return
            except (json.JSONDecodeError, ValueError) as error:
                _json_response(self, 400, {"error": str(error), "error_code": "invalid_request"})
                return
            self._handle_plan_sse(payload)
            return
        if urlparse(self.path).path == "/api/agent/browser-task" and "text/event-stream" in self.headers.get("Accept", ""):
            try:
                payload = _load_body(self)
            except (ApiRequestError, json.JSONDecodeError, ValueError) as error:
                _json_response(self, 400, {"error": str(error), "error_code": "invalid_request"})
                return
            if not str(payload.get("url") or "").strip() or not str(payload.get("task") or "").strip():
                _json_response(self, 400, {"error": "url and task are required", "error_code": "invalid_request"})
                return
            self._handle_browser_task_sse(payload)
            return
        route = urlparse(self.path).path
        if route.startswith("/api/browser/") and route.endswith("/interact"):
            session_id = route[len("/api/browser/"): -len("/interact")].strip("/")
            try:
                body = _load_body(self)
            except (ApiRequestError, json.JSONDecodeError, ValueError) as error:
                _json_response(self, 400, {"error": str(error), "error_code": "invalid_request"})
                return
            from nemo_coding_platform.browser_agent import browser_interact
            action = str(body.get("action") or "")
            kwargs = {k: v for k, v in body.items() if k != "action"}
            result = browser_interact(session_id, action, **kwargs)
            _json_response(self, 200 if result.get("ok") else 404, result)
            return
        if route.startswith("/api/run/") and route.endswith("/worktree-merge"):
            job_id = route[len("/api/run/"): -len("/worktree-merge")].strip("/")
            try:
                body = _load_body(self)
            except (ApiRequestError, json.JSONDecodeError, ValueError) as error:
                _json_response(self, 400, {"error": str(error), "error_code": "invalid_request"})
                return
            self._handle(lambda payload: api_worktree_merge(self.server.config, job_id, payload, self.server.jobs), body)
            return
        if route.startswith("/api/run/") and route.endswith("/worktree-cleanup"):
            job_id = route[len("/api/run/"): -len("/worktree-cleanup")].strip("/")
            self._handle(lambda _: api_worktree_cleanup(self.server.config, job_id, self.server.jobs), {})
            return
        if route.startswith("/api/run/") and route.endswith("/permission-grant"):
            job_id = route[len("/api/run/"): -len("/permission-grant")].strip("/")
            try:
                body = _load_body(self)
            except (ApiRequestError, json.JSONDecodeError, ValueError) as error:
                _json_response(self, 400, {"error": str(error), "error_code": "invalid_request"})
                return
            self._handle(lambda payload: api_permission_grant(self.server.config, job_id, payload, self.server.jobs), body)
            return
        if route.startswith("/api/run/") and route.endswith("/permission-deny"):
            job_id = route[len("/api/run/"): -len("/permission-deny")].strip("/")
            try:
                body = _load_body(self)
            except (ApiRequestError, json.JSONDecodeError, ValueError) as error:
                _json_response(self, 400, {"error": str(error), "error_code": "invalid_request"})
                return
            self._handle(lambda payload: api_permission_deny(self.server.config, job_id, payload, self.server.jobs), body)
            return
        handlers = {
            "/api/refresh": lambda payload: api_state(self.server.config, self.server.jobs),
            "/api/settings": lambda payload: api_settings(self.server, payload),
            "/api/repo/open": lambda payload: api_repo_open(self.server, payload),
            "/api/repo/clone": lambda payload: api_repo_clone(self.server, payload),
            "/api/repo/pick-folder": lambda payload: api_repo_pick_folder(self.server, payload),
            "/api/terminal/run": lambda payload: api_terminal_run(self.server.config, payload),
            "/api/browser/open": lambda payload: api_browser_open(self.server, payload),
            "/api/browser/search": lambda payload: api_browser_search(self.server, payload),
            "/api/extensions": lambda payload: api_extensions_update(self.server, payload),
            "/api/git/diff": lambda payload: api_git_diff(self.server.config, payload),
            "/api/git/stage": lambda payload: api_git_stage(self.server.config, payload),
            "/api/git/stage-hunk": lambda payload: api_git_stage_hunk(self.server.config, payload),
            "/api/git/commit": lambda payload: api_git_commit(self.server.config, payload),
            "/api/git/checkout": lambda payload: api_git_checkout(self.server.config, payload),
            "/api/git/sync": lambda payload: api_git_sync(self.server.config, payload),
            "/api/artifacts/cleanup": lambda payload: api_cleanup(self.server.config, payload),
            "/api/runs/cleanup": lambda payload: api_runs_cleanup(self.server, payload),
            "/api/file": lambda payload: api_file(self.server.config, payload),
            "/api/handoff": lambda payload: api_handoff(self.server.config, payload),
            "/api/handoff/start": lambda payload: api_handoff_start(self.server, payload),
            "/api/job": lambda payload: api_job(self.server, payload),
            "/api/job/cancel": lambda payload: api_job_cancel(self.server, payload),
            "/api/job/pause": lambda payload: api_job_pause(self.server, payload),
            "/api/job/resume": lambda payload: api_job_resume(self.server, payload),
            "/api/job/signal": lambda payload: api_job_signal(self.server, payload),
            "/api/jobs/orphans": lambda payload: api_orphan_jobs(self.server, payload),
            "/api/self-modify/start": lambda payload: api_self_modify_start(self.server, payload),
            "/api/agent/message": lambda payload: api_agent_message(self.server.config, payload, server=self.server),
            "/api/agent/plan": lambda payload: api_agent_plan(self.server.config, payload),
            "/api/agent/plan/cancel": lambda payload: api_plan_cancel(payload),
            "/api/agent/plan/steer": lambda payload: api_plan_steer(payload),
            "/api/agent/generate-image": lambda payload: api_generate_image(self.server.config, payload),
            "/api/vault/credentials": lambda payload: api_vault_create(self.server.config, payload),
            "/api/vault/credentials/update": lambda payload: api_vault_update(self.server.config, payload),
            "/api/vault/credentials/delete": lambda payload: api_vault_delete(self.server.config, payload),
            "/api/vault/credentials/lookup": lambda payload: api_vault_lookup(self.server.config, payload, self.client_address[0]),
            "/api/agent/browser-confirm": lambda payload: api_browser_confirm(payload),
            "/api/agent/browser-cancel": lambda payload: api_browser_cancel(payload),
            "/api/nemo": lambda payload: api_nemo(self.server.config, payload),
            "/api/nemo/tool": lambda payload: api_nemo_tool(self.server.config, payload),
            "/api/nemo/mcp-status": lambda payload: api_nemo_mcp_status(self.server.config, payload),
            "/api/nemo/cognitive-stats": lambda payload: api_nemo_cognitive_stats(self.server.config, payload),
            "/api/nemo/risk-map": lambda payload: api_nemo_risk_map(self.server.config, payload),
            "/api/search": lambda payload: api_search(self.server.config, payload),
            "/api/self-mod/insights": lambda payload: api_self_mod_insights(self.server.config, payload),
            "/api/eval": lambda payload: api_eval(self.server.config, payload),
            "/api/review": lambda payload: api_review(self.server.config, payload),
            "/api/apply": lambda payload: api_apply(self.server.config, payload),
            "/api/apply-selection": lambda payload: api_apply_selection(self.server.config, payload),
            "/api/rollback": lambda payload: api_rollback(self.server.config, payload),
            "/api/decision-log/append": lambda payload: api_decision_log_append(self.server.config, payload),
            "/api/kpis": lambda payload: api_kpis(self.server.config),
            "/api/stats": lambda payload: api_record_stats(self.server, payload),
        }
        action = handlers.get(urlparse(self.path).path)
        if action is None:
            _json_response(self, 404, {"error": "not_found"})
            return
        try:
            payload = _load_body(self)
        except ApiRequestError as error:
            _json_response(self, error.status_code, {"error": str(error), "error_code": error.error_code})
            return
        except (json.JSONDecodeError, ValueError) as error:
            _json_response(self, 400, {"error": str(error), "error_code": "invalid_request"})
            return
        self._handle(action, payload)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _handle(self, action: Any, payload: dict[str, object]) -> None:
        try:
            _json_response(self, 200, action(payload))
        except ApiRequestError as error:
            _json_response(self, error.status_code, {"error": str(error), "error_code": error.error_code})
        except PermissionError as error:
            _json_response(self, 409, {"error": str(error), "error_code": "permission_denied"})
        except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError) as error:
            _json_response(self, 400, {"error": str(error), "error_code": "invalid_request"})
        except Exception as error:
            _json_response(self, 500, {"error": str(error), "error_code": "internal_error"})

    def _check_rate_limit(self) -> bool:
        route = urlparse(self.path).path
        if route in self._RATE_LIMIT_EXEMPT_PATHS:
            return True
        client_ip = self.client_address[0]
        status = self.server.rate_limiter.check(client_ip)
        if status == "reject":
            _json_response(self, 429, {"error": "Too many requests", "error_code": "rate_limited"})
            return False
        if status == "throttle":
            time.sleep(self.server.rate_limiter.sleep_seconds)
            # We don't reject after throttle, we just slow down
        return True


class MissionControlHttpServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], config: MissionControlServerConfig) -> None:
        super().__init__(server_address, MissionControlRequestHandler)
        self.config = config
        self.jobs = HandoffJobManager(config.job_snapshots_path)
        self.rate_limiter = RateLimiter()

    def server_close(self) -> None:
        for job in list(self.jobs._jobs.values()):
            if job.heartbeat_stop is not None:
                job.heartbeat_stop.set()
            if job.status not in {"completed", "failed", "cancelled"}:
                self.jobs.cancel(job.job_id)
            else:
                self.jobs._join_heartbeat(job)
        super().server_close()


def run_server(host: str, port: int, config: MissionControlServerConfig) -> None:
    server = MissionControlHttpServer((host, port), config)
    print(f"Mission Control API listening on http://{host}:{port}")
    server.serve_forever()