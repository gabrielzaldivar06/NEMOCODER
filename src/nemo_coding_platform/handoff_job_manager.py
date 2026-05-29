"""HandoffJobStatus, HandoffJob, HandoffJobManager — extracted from mission_control_server.py."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time

logger = logging.getLogger(__name__)
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from uuid import uuid4

from nemo_coding_platform.core.engine_interface import ENGINE_MESSAGE_FILE
from nemo_coding_platform.core.persistence import load_headless_result_json
from nemo_coding_platform.core.review_gate import build_merge_plan
from nemo_coding_platform.core.vscode_mcp_config import VSCODE_STDIO_NEMO_URL
from nemo_coding_platform.spacecode_mcp_tools import mcp_call_nemo_tool
from nemo_coding_platform.worktree_api import _job_worktree_branch, _job_worktree_path

if TYPE_CHECKING:
    from nemo_coding_platform.mission_control_server import MissionControlServerConfig

# ── Constants (kept in sync with mission_control_server.py) ─────────────────
NEMO_EVENT_PREFIX = "NEMO_EVENT:"
_TIMELINE_PERSIST_BATCH = 5
JOB_LOG_LIMIT = 10_000
JOB_HEARTBEAT_SECONDS = 20.0
JOB_STALL_HEARTBEATS = 8
# Stall is declared only when BOTH conditions hold: runtime artifacts unchanged
# for JOB_STALL_HEARTBEATS ticks AND stdout silent for at least this many seconds.
# Prevents false-positive stall kills on thinking models that emit reasoning_content
# without touching files during a long LLM call.
JOB_STDOUT_QUIET_SECONDS = 180.0
# Fast poll interval for mid-run permission detection. Decoupled from the 20s
# heartbeat so user approval prompts surface within ~2s instead of ~20s — critical
# in long jobs where multiple permissions can stack up.
PERMISSION_WATCHER_SECONDS = 2.0
LEGACY_NEMO_SSE_URL = "http://127.0.0.1:8765/mcp/sse"


# ── Utility functions (pure — no monolith dependency) ───────────────────────

def _safe_stem(value: str) -> str:
    return "".join(char if char.isalnum() or char in ("-", "_") else "-" for char in value).strip("-") or "run"


def _parse_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _nemo_configured(memory_db: "Path | None", nemo_mcp_url: "str | None") -> bool:
    return bool(memory_db) or bool((nemo_mcp_url or "").strip())


def _handoff_prd_text(payload: dict[str, object]) -> str | None:
    value = payload.get("prd_text")
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _write_current_job_context(config: "MissionControlServerConfig", job_id: str | None) -> None:
    path = Path(config.repo_path) / ".spacecode-runtimes" / "mcp" / "current_job.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if job_id:
            path.write_text(json.dumps({"job_id": job_id}), encoding="utf-8")
        elif path.exists():
            path.unlink()
    except OSError:
        pass


# ── Domain classes ───────────────────────────────────────────────────────────

class HandoffJobStatus(StrEnum):
    STARTING            = "starting"
    AWAITING_PERMISSION = "awaiting_permission"
    RUNNING             = "running"
    COMPLETED           = "completed"
    FAILED              = "failed"
    PERMISSION_DENIED   = "permission_denied"
    ORPHANED            = "orphaned"
    CANCELLED           = "cancelled"


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
    permission_watcher_thread: threading.Thread | None = None
    run_thread: threading.Thread | None = None
    error: str | None = None
    last_runtime_signature: str = ""
    stagnant_heartbeats: int = 0
    permission_request: dict[str, object] | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    timeline: list[dict[str, object]] = field(default_factory=list)
    auto_merged: bool = False
    last_stdout_ts: float = 0.0  # epoch seconds; updated by _pipe_reader on every line
    _tl_event_count: int = 0
    _tool_audit_offset: int = 0  # lines consumed from tool-audit.jsonl for this job

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
            auto_merged=bool(payload.get("auto_merged", False)),
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
            "auto_merged": self.auto_merged,
        }
        if include_logs:
            payload["logs"] = list(self.logs)
        return payload


class HandoffJobManager:
    def __init__(self, snapshot_root: Path | None = None) -> None:
        self._jobs: dict[str, HandoffJob] = {}
        self._lock = threading.Lock()
        self._snapshot_root = snapshot_root
        # Optional hook invoked exactly once after a job reaches a terminal status
        # (completed/failed). The server wires this to fire the Decision Agent on
        # failure. Default: None (no hook). Hook errors are swallowed and logged.
        self.post_completion_hook: "Callable[[HandoffJob], None] | None" = None
        if self._snapshot_root is not None:
            self._snapshot_root.mkdir(parents=True, exist_ok=True)
            self._restore_snapshots()

    def configure_snapshot_root(self, snapshot_root: Path) -> None:
        # Clear in-memory jobs first so switching workspaces does not leak the
        # previous workspace's jobs. Restore from the new snapshot root.
        with self._lock:
            self._jobs.clear()
        self._snapshot_root = snapshot_root
        self._snapshot_root.mkdir(parents=True, exist_ok=True)
        self._restore_snapshots()

    def start(self, config: "MissionControlServerConfig", payload: dict[str, object]) -> HandoffJob:
        from nemo_coding_platform.mission_control_server import (  # lazy
            _load_settings, _enforce_workspace_scope, _objective, _provider_mode, _timeout_seconds,
        )
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
        from nemo_coding_platform.mission_control_server import (  # lazy
            _load_settings, _enforce_workspace_scope, _objective, _provider_mode, _timeout_seconds,
        )
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
        from nemo_coding_platform.mission_control_server import (  # lazy
            ApiRequestError,
            _load_settings, _enforce_workspace_scope, _objective, _provider_mode, _timeout_seconds,
        )

        job = self.get(job_id)
        if job.status != "awaiting_permission":
            raise ApiRequestError(
                f"job {job_id} is not awaiting_permission (status={job.status})",
                error_code="invalid_job_status",
            )
        req_dict = job.permission_request or {}

        # ── Mid-run case: write response file, restore status to running ──────
        if req_dict.get("mid_run"):
            request_id = str(req_dict.get("request_id", ""))
            risk_val = req_dict.get("risk") or req_dict.get("categories", ["unknown"])[0]
            try:
                cat = (PermissionCategory(risk_val),) if risk_val else ()
            except ValueError:
                cat = ()
            decision = PermissionDecision(
                decided_at=datetime.now(_tz.utc).isoformat(),
                decided_by="mid_run_signal",
                approved=True,
                categories=cat,
                note=note,
            )
            with self._lock:
                job.status = "running"
                job.permission_request = {**req_dict, "decision": decision.to_dict()}
            self._persist_job(job)
            self._write_mid_run_response(config, request_id, approved=True, note=note)
            self._append_log(job, f"mid-run permission granted: req_id={request_id} note={note!r}")
            return job
        # ── Pre-run case (original flow) ──────────────────────────────────────

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

    def deny_permission(self, job_id: str, note: str = "", config: "MissionControlServerConfig | None" = None) -> "HandoffJob":
        from datetime import timezone as _tz
        from nemo_coding_platform.core.permission_engine import PermissionDecision, PermissionCategory
        from nemo_coding_platform.mission_control_server import ApiRequestError  # lazy

        job = self.get(job_id)
        if job.status != "awaiting_permission":
            raise ApiRequestError(
                f"job {job_id} is not awaiting_permission (status={job.status})",
                error_code="invalid_job_status",
            )
        req_dict = job.permission_request or {}

        # ── Mid-run case: write deny response, subprocess continues (tool denied) ─
        if req_dict.get("mid_run"):
            request_id = str(req_dict.get("request_id", ""))
            risk_val = req_dict.get("risk") or req_dict.get("categories", ["unknown"])[0]
            try:
                cat = (PermissionCategory(risk_val),) if risk_val else ()
            except ValueError:
                cat = ()
            decision = PermissionDecision(
                decided_at=datetime.now(_tz.utc).isoformat(),
                decided_by="mid_run_signal",
                approved=False,
                categories=cat,
                note=note,
            )
            with self._lock:
                job.status = "running"
                job.permission_request = {**req_dict, "decision": decision.to_dict()}
            self._persist_job(job)
            if config is not None:
                self._write_mid_run_response(config, request_id, approved=False, note=note)
            self._append_log(job, f"mid-run permission denied: req_id={request_id} note={note!r}")
            return job
        # ── Pre-run case (original flow) ─────────────────────────────────────

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

    def continue_job(self, config: "MissionControlServerConfig", source_job_id: str, payload: dict[str, object]) -> "HandoffJob":
        """Start a continuation of a completed handoff job.

        Merges the source worktree to main (if it exists and has not been merged yet),
        then starts a brand-new long-handoff-run on the same repo so the agent sees
        all code from the previous run. long-handoff-continue is only for paused/resume-
        token runs; a completed run simply needs a new job with updated context.
        """
        import json as _json
        from nemo_coding_platform.mission_control_server import _load_settings  # lazy

        source = self.get(source_job_id)

        # Recover the original repo_path from the source job's run JSON.
        # source.payload is empty after server restart so we cannot rely on it.
        source_repo_path: str | None = None
        source_sandbox_path: str | None = None
        if source.run_json:
            try:
                src_data = _json.loads(Path(source.run_json).read_text(encoding="utf-8"))
                source_repo_path = src_data.get("task", {}).get("repo_path")
                source_sandbox_path = src_data.get("run", {}).get("sandbox_path")
            except Exception:
                pass
        effective_repo = Path(source_repo_path) if source_repo_path else Path(config.repo_path)

        # Detect whether the source run was paused (has a resume token in continuation-state.json).
        has_resume_token = False
        if source_sandbox_path:
            try:
                cont_state_path = Path(source_sandbox_path) / "continuation-state.json"
                if cont_state_path.exists():
                    cont_state = _json.loads(cont_state_path.read_text(encoding="utf-8"))
                    has_resume_token = bool(cont_state.get("resume_token"))
            except Exception:
                pass

        # Merge existing worktree so the new run starts from an up-to-date main branch.
        try:
            from nemo_coding_platform.core.worktree_runtime import (
                merge_worktree_to_main, cleanup_git_worktree, worktree_branch_name,
            )
            runtime_id = f"{source.task_id}-{source.run_id}"
            branch = worktree_branch_name(runtime_id)
            wt_path = effective_repo / ".worktrees" / runtime_id
            if wt_path.exists():
                merged = merge_worktree_to_main(effective_repo, branch, f"continue: merge {source_job_id}")
                if merged:
                    cleanup_git_worktree(effective_repo, wt_path, branch)
        except Exception:
            pass  # merge failure is non-fatal — new run will still see worktree code via repo_map

        settings = _load_settings(config)
        continuation_payload = {
            **settings,
            **source.payload,
            **payload,
            # Ensure SSE MCP URL so subprocess can reach NEMO
            "nemo_mcp_url": LEGACY_NEMO_SSE_URL,
            "nemo_mcp_prefix": "nemo.",
        }
        # Override repo_path with the source job's repo — settings always points to the
        # Mission Control repo (C:\dev\dev4) which is wrong for external projects.
        if source_repo_path:
            continuation_payload["repo_path"] = source_repo_path
            # Also reset validation to none so Mission Control build commands don't apply.
            continuation_payload.setdefault("validation_policy", "none")
            continuation_payload["validation_commands"] = ["validation skipped by policy:none"]

        if has_resume_token and source.run_json:
            # Paused run — use long-handoff-continue to resume from checkpoint.
            from nemo_coding_platform.mission_control_server import _objective, _provider_mode, _timeout_seconds  # lazy
            objective = _objective(continuation_payload)
            provider = _provider_mode(continuation_payload)
            timeout = _timeout_seconds(continuation_payload)
            run_suffix = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            short_id = uuid4().hex[:8]
            job_id = f"cont-job-{run_suffix}-{short_id}"
            task_id = f"cont-task-{run_suffix}-{short_id}"
            run_id = f"cont-run-{run_suffix}-{short_id}"
            run_json = config.runtimes_path / "continuations" / f"{_safe_stem(task_id)}-{_safe_stem(run_id)}.json"
            run_json.parent.mkdir(parents=True, exist_ok=True)
            command = self._build_continue_command(config, continuation_payload, Path(source.run_json), objective, provider, timeout, task_id, run_id, run_json)
            job = HandoffJob(job_id, task_id, run_id, str(run_json), "starting", command, dict(continuation_payload), ["starting continuation from paused run"])
            with self._lock:
                self._jobs[job_id] = job
            self._persist_job(job)
            run_thread = threading.Thread(target=self._run_job, args=(config, job), daemon=True)
            job.run_thread = run_thread
            run_thread.start()
            return job

        return self.start(config, continuation_payload)

    def _build_continue_command(
        self,
        config: "MissionControlServerConfig",
        payload: dict[str, object],
        source_run_json: "Path",
        objective: str,
        provider: str,
        timeout: float,
        task_id: str,
        run_id: str,
        run_json: "Path",
    ) -> tuple[str, ...]:
        from nemo_coding_platform.mission_control_server import (  # lazy
            _string_list, _resolve_lmstudio_model,
        )
        command: list[str] = [
            sys.executable, "-u", "-m", "nemo_coding_platform",
            "long-handoff-continue",
            str(source_run_json),
            "--objective", objective,
            "--provider", provider,
            "--timeout", str(timeout),
            "--max-runtime-minutes", str(payload.get("max_runtime_minutes") or 120),
            "--heartbeat-minutes", str(payload.get("heartbeat_minutes") or 15),
            "--max-heartbeats", str(payload.get("max_heartbeats") or 4),
            "--token-budget", str(payload.get("token_budget") or 128000),
            "--plan-minutes", str(payload.get("plan_minutes") or 30),
            "--execute-minutes", str(payload.get("execute_minutes") or 60),
            "--review-minutes", str(payload.get("review_minutes") or 30),
            "--validation-policy", str(payload.get("validation_policy") or "smoke"),
            "--save-json", str(run_json),
            "--json",
        ]
        for item in _string_list(payload, "acceptance_criteria", ("implementation satisfies the objective",)):
            command.extend(("--acceptance", item))
        for item in _string_list(payload, "validation_commands", ()):
            command.extend(("--validation", item))
        for item in _string_list(payload, "target_files", ()):
            command.extend(("--target-file", item))
        if config.memory_db is None:
            command.append("--no-memory-db")
        else:
            command.extend(("--memory-db", str(config.memory_db)))
        mcp_url = str(payload.get("nemo_mcp_url") or "").strip()
        if mcp_url.lower() == VSCODE_STDIO_NEMO_URL:
            mcp_url = LEGACY_NEMO_SSE_URL
        if not mcp_url:
            mcp_url = LEGACY_NEMO_SSE_URL  # continuation subprocesses always need SSE, not stdio
        command.extend(("--mcp-url", mcp_url))
        command.extend(("--mcp-prefix", str(payload.get("nemo_mcp_prefix") or "nemo.")))
        base_url = str(payload.get("base_url") or payload.get("model_base_url") or "http://127.0.0.1:1234/v1")
        model = str(payload.get("model") or payload.get("default_model") or "").strip() or _resolve_lmstudio_model(base_url)
        command.extend(("--model-profile", model))
        command.extend(("--lmstudio-base-url", base_url))
        if bool(payload.get("use_git_worktree", True)):
            command.append("--use-git-worktree")
        if bool(payload.get("real_validation", True)):
            command.append("--real-validation")
        return tuple(command)

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

    @staticmethod
    def _kill_process_tree(pid: int, log_lines: list[str]) -> None:
        """Kill a process and all its descendants. Uses psutil when available for reliability on Windows."""
        try:
            import psutil
            try:
                parent = psutil.Process(pid)
                children = parent.children(recursive=True)
            except psutil.NoSuchProcess:
                return
            # Kill children first so the parent can't respawn them
            for child in children:
                try:
                    child.kill()
                    log_lines.append(f"killed child pid={child.pid} name={child.name()}")
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            try:
                parent.kill()
                log_lines.append(f"killed parent pid={pid}")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        except ImportError:
            # psutil not available — plain kill
            try:
                import signal
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

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
            pid = job.process.pid
            kill_logs: list[str] = []
            self._kill_process_tree(pid, kill_logs)
            for line in kill_logs:
                self._append_log(job, line)
            try:
                job.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                job.process.kill()
                self._append_log(job, "process did not exit after tree kill; sent direct kill")
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
            run_thread.join(timeout=30)
            if run_thread.is_alive():
                logger.warning("run_thread for %s did not finish in 30s", job.job_id)
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

    def _auto_merge_on_completion(self, config: "MissionControlServerConfig", job: HandoffJob) -> None:
        """Merge the job's worktree to main when auto_merge=true and result is clean."""
        if not job.payload.get("auto_merge"):
            return
        if job.returncode != 0:
            self._append_log(job, "auto_merge skipped: returncode != 0")
            return
        run_path = Path(job.run_json)
        if not run_path.exists():
            self._append_log(job, "auto_merge skipped: run_json not found")
            return
        try:
            result_data = load_headless_result_json(run_path)
        except Exception as exc:  # noqa: BLE001
            self._append_log(job, f"auto_merge skipped: cannot load run_json — {exc}")
            return
        plan = build_merge_plan(result_data)
        if not plan.mergeable:
            self._append_log(job, f"auto_merge skipped: not mergeable — {', '.join(plan.risk_flags) or 'no files to merge'}")
            return
        if plan.risk_flags:
            self._append_log(job, f"auto_merge skipped: risk_flags present — {', '.join(plan.risk_flags)}")
            return
        from nemo_coding_platform.core.worktree_runtime import cleanup_git_worktree, merge_worktree_to_main
        branch = _job_worktree_branch(job)
        wt_path = _job_worktree_path(config, job)
        objective = str(job.payload.get("objective") or job.job_id)[:80]
        merged = merge_worktree_to_main(Path(config.repo_path), branch, f"auto-merge: {objective}")
        if merged:
            cleanup_git_worktree(Path(config.repo_path), wt_path, branch)
            job.auto_merged = True
            self._append_log(job, f"auto_merge succeeded: branch={branch} → main, files={len(plan.files)}")
            self._persist_job(job)
            _nemo_url = str(job.payload.get("nemo_mcp_url") or getattr(config, "nemo_mcp_url", "") or "")
            _nemo_db = str(getattr(config, "memory_db", "") or "")
            if _nemo_configured(_nemo_db, _nemo_url):
                try:
                    mcp_call_nemo_tool(
                        "cognitive_ingest",
                        lifecycle_phase="close",
                        memory_db=_nemo_db,
                        mcp_url=_nemo_url,
                        content=f"Auto-merged job {job.job_id}: {objective[:60]}, branch={branch}, {len(plan.files)} file(s) changed",
                        memory_type="task_outcome",
                        tags=["auto_merge", "completed", "merged"],
                        context=f"Worktree branch {branch} merged to main automatically. Files: {[item.path for item in plan.files[:10]]}",
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("NEMO cognitive_ingest failed for auto-merge job %s: %s", job.job_id, exc)
        else:
            self._append_log(job, f"auto_merge failed: merge_worktree_to_main returned False for branch={branch}")

    @staticmethod
    def _mid_run_perm_dir(config: "MissionControlServerConfig") -> Path:
        return Path(config.repo_path) / ".spacecode-runtimes" / "mcp"

    @staticmethod
    def _write_mid_run_response(
        config: "MissionControlServerConfig",
        request_id: str,
        *,
        approved: bool,
        note: str = "",
    ) -> None:
        perm_dir = HandoffJobManager._mid_run_perm_dir(config)
        perm_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "approved": approved,
            "note": note,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        (perm_dir / f"response-perm-{request_id}.json").write_text(
            json.dumps(record), encoding="utf-8"
        )

    def _check_mid_run_permissions(
        self, config: "MissionControlServerConfig", job: HandoffJob
    ) -> None:
        """Detect pending mid-run permission requests written by mcp_server.py."""
        if job.status != "running":
            return
        perm_dir = self._mid_run_perm_dir(config)
        if not perm_dir.exists():
            return
        for pending_file in sorted(perm_dir.glob("pending-perm-*.json")):
            try:
                data = json.loads(pending_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            if data.get("job_id") != job.job_id:
                continue
            req_id = str(data.get("request_id") or pending_file.stem.removeprefix("pending-perm-"))
            # Skip if already tracking this request
            if (job.permission_request or {}).get("request_id") == req_id:
                return
            mid_run_request: dict[str, object] = {
                "mid_run": True,
                "request_id": req_id,
                "job_id": job.job_id,
                "categories": [data.get("risk", "unknown")],
                "rationale": f'Agent wants to call "{data.get("tool", "?")}" (risk: {data.get("risk", "?")})',
                "requires_user_approval": [data.get("risk", "unknown")],
                "tool": data.get("tool"),
                "risk": data.get("risk"),
                "tool_args_preview": data.get("tool_args_preview", {}),
                "ts": data.get("ts"),
            }
            with self._lock:
                job.permission_request = mid_run_request
                job.status = "awaiting_permission"
            self._persist_job(job)
            self._append_log(
                job,
                f"mid-run permission requested: tool={data.get('tool')} risk={data.get('risk')} req_id={req_id}",
            )
            return

    def _sync_tool_audit_events(
        self, config: "MissionControlServerConfig", job: HandoffJob
    ) -> None:
        """Read new entries from tool-audit.jsonl for this job and add them to job.timeline."""
        import os as _os
        audit_path = Path(
            _os.environ.get("SPACE_CODE_MCP_AUDIT_LOG", ".spacecode-runtimes/mcp/tool-audit.jsonl")
        )
        if not audit_path.is_absolute():
            audit_path = Path(config.repo_path) / audit_path
        if not audit_path.exists():
            return
        try:
            lines = audit_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        new_events: list[dict[str, object]] = []
        for raw in lines[job._tool_audit_offset:]:
            job._tool_audit_offset += 1
            if not raw.strip():
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if entry.get("job_id") != job.job_id:
                continue
            audit = entry.get("tool_call_audit") or {}
            outcome = str(entry.get("outcome", ""))
            tool_name = str(audit.get("tool", "?"))
            risk = str(audit.get("risk", "?"))
            # Skip noisy read-only tools; include writes and denied actions
            if outcome == "allowed" and risk == "read_only":
                continue
            summary = f"{tool_name} — {outcome}"
            seq = len(job.timeline) + len(new_events)
            new_events.append({
                "kind": "tool_use",
                "summary": summary,
                "phase": "execute",
                "sequence": seq,
                "ts": str(entry.get("ts", "")),
                "payload": {
                    "tool": tool_name,
                    "risk": risk,
                    "outcome": outcome,
                    "allowed": bool(audit.get("allowed", outcome == "allowed")),
                },
            })
        if new_events:
            with self._lock:
                job.timeline.extend(new_events)
                job._tl_event_count += len(new_events)
                job.updated_at = datetime.now(timezone.utc).isoformat()

    def _heartbeat_tick(self, config: "MissionControlServerConfig", job: HandoffJob) -> None:
        self._check_mid_run_permissions(config, job)
        self._sync_tool_audit_events(config, job)
        self._append_log(job, self._heartbeat_snapshot(config, job))
        stalled_reason = self._stall_reason(config, job)
        if stalled_reason and job.process and job.process.poll() is None:
            self._append_log(job, stalled_reason)
            job.error = stalled_reason
            job.process.kill()
            self._set_status(job, "failed")

    def _stall_reason(self, config: "MissionControlServerConfig", job: HandoffJob) -> str | None:
        # Never stall-kill while waiting for user to approve/deny a mid-run request.
        if job.status == "awaiting_permission":
            return None
        runtime_path = self._runtime_path(config, job)
        run_path = Path(job.run_json)
        if runtime_path is None or not runtime_path.exists() or run_path.exists():
            job.last_runtime_signature = ""
            job.stagnant_heartbeats = 0
            return None

        top_level = [path for path in sorted(runtime_path.iterdir(), key=lambda item: item.name) if path.is_file()]
        signature = "|".join(f"{path.name}:{path.stat().st_size}:{int(path.stat().st_mtime * 10)}" for path in top_level)
        if signature and signature == job.last_runtime_signature:
            job.stagnant_heartbeats += 1
        else:
            job.last_runtime_signature = signature
            job.stagnant_heartbeats = 0
        self._persist_job(job)

        if job.stagnant_heartbeats < JOB_STALL_HEARTBEATS:
            return None
        # Dual-signal: artifacts AND stdout must both be silent. Thinking models
        # (qwen3.5, kimi, DeepSeek-R1 family) can emit reasoning_content for
        # minutes without writing files — killing them mid-think wastes budget.
        last_stdout = getattr(job, "last_stdout_ts", 0.0) or 0.0
        stdout_silence = time.time() - last_stdout if last_stdout > 0 else None
        if stdout_silence is not None and stdout_silence < JOB_STDOUT_QUIET_SECONDS:
            # Artifacts stagnant but stdout is still active — agent is thinking,
            # not stalled. Don't kill it.
            return None
        silence_label = (
            f"{int(stdout_silence)}s" if stdout_silence is not None else "no stdout activity recorded"
        )
        return (
            "stall detected: runtime artifacts unchanged for "
            f"{job.stagnant_heartbeats} heartbeats (~{int(job.stagnant_heartbeats * JOB_HEARTBEAT_SECONDS)}s) "
            f"AND stdout silent for {silence_label}; terminating job to fail closed"
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

        def _permission_watcher_loop() -> None:
            # Fast poll for pending mid-run permission requests. The heartbeat loop
            # also checks this every 20s, but for long jobs a 2s poll surfaces the
            # request to the UI almost instantly instead of stalling the agent.
            while job.process and job.process.poll() is None:
                if stop_event.wait(PERMISSION_WATCHER_SECONDS):
                    break
                if not job.process or job.process.poll() is not None:
                    break
                if job.status == "running":
                    try:
                        self._check_mid_run_permissions(config, job)
                    except Exception:  # noqa: BLE001
                        logger.debug("permission watcher tick failed for %s", job.job_id, exc_info=True)

        watcher = threading.Thread(
            target=_permission_watcher_loop,
            daemon=True,
            name=f"perm-{job.job_id}",
        )
        job.permission_watcher_thread = watcher
        watcher.start()

    def _join_heartbeat(self, job: HandoffJob) -> None:
        heartbeat_thread = job.heartbeat_thread
        if heartbeat_thread is not None and heartbeat_thread.is_alive():
            heartbeat_thread.join(timeout=5)
            if heartbeat_thread.is_alive():
                logger.warning("heartbeat_thread for %s did not stop in 5s", job.job_id)
        job.heartbeat_thread = None
        watcher = job.permission_watcher_thread
        if watcher is not None and watcher.is_alive():
            watcher.join(timeout=3)
            if watcher.is_alive():
                logger.warning("permission_watcher_thread for %s did not stop in 3s", job.job_id)
        job.permission_watcher_thread = None
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
        from nemo_coding_platform.mission_control_server import (  # lazy
            _string_list, _handoff_spec_mode, _resolve_lmstudio_model,
        )
        effective_repo = str(payload.get("repo_path") or config.repo_path)
        command: list[str] = [
            sys.executable,
            "-u",
            "-m",
            "nemo_coding_platform",
            "long-handoff-run",
            objective,
            "--repo",
            effective_repo,
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
        _target_files = _string_list(payload, "target_files", ())
        _raw_validation = _string_list(payload, "validation_commands", ())
        # Strip npm-based validation when all explicit targets are non-frontend files.
        # This prevents settings-level npm build commands leaking into Python-only handoffs.
        _frontend_exts = frozenset({".ts", ".tsx", ".js", ".jsx", ".css", ".scss", ".vue", ".svelte"})
        _has_frontend_target = any(Path(f).suffix in _frontend_exts for f in _target_files)
        _effective_validation = (
            _raw_validation
            if not _raw_validation or not _target_files or _has_frontend_target
            else tuple(v for v in _raw_validation if not v.lstrip().startswith("npm"))
        )
        for item in _effective_validation:
            command.extend(("--validation", item))
        for item in _target_files:
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
        # Only wire NEMO MCP when using the subprocess provider — fake/test providers
        # don't call an LLM so they don't need cross-session memory, and connecting to
        # NEMO SSE would add 60-90s of NEMO bootstrap latency for no benefit.
        if mcp_url and provider == "subprocess":
            # stdio://vscode/nemo only works inside VS Code — child subprocesses need HTTP SSE
            if mcp_url.lower() == VSCODE_STDIO_NEMO_URL:
                mcp_url = LEGACY_NEMO_SSE_URL
            command.extend(("--mcp-url", mcp_url))
            command.extend(("--mcp-prefix", str(payload.get("nemo_mcp_prefix") or "nemo.")))
        else:
            # Non-subprocess providers (fake, test) or no NEMO URL — run without MCP
            command.append("--allow-non-mcp")
        base_url_run = str(payload.get("base_url") or payload.get("model_base_url") or "http://127.0.0.1:1234/v1")
        model_run = str(payload.get("model") or payload.get("default_model") or "").strip() or _resolve_lmstudio_model(base_url_run)
        command.extend(("--model-profile", model_run))
        command.extend(("--lmstudio-base-url", base_url_run))
        model_roles = payload.get("model_roles") or {}
        if isinstance(model_roles, dict) and model_roles:
            roles_str = ",".join(f"{k}={v}" for k, v in model_roles.items() if v)
            if roles_str:
                command.extend(("--model-roles", roles_str))
        if payload.get("pause_after_minutes") is not None:
            command.extend(("--pause-after-minutes", str(payload.get("pause_after_minutes"))))
        if bool(payload.get("validation_escalation_mode")):
            command.append("--validation-escalation-mode")
        if bool(payload.get("real_validation", True)):
            command.append("--real-validation")
        if bool(payload.get("use_git_worktree", True)):  # default True — git worktree gives agent full repo access
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
        from nemo_coding_platform.mission_control_server import (  # lazy
            _string_list, _resolve_lmstudio_model,
        )
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
        from nemo_coding_platform.mission_control_server import _load_settings  # lazy
        source_root = Path(__file__).resolve().parents[1]
        existing_pythonpath = os.environ.get("PYTHONPATH", "")
        pythonpath = str(source_root) if not existing_pythonpath else f"{source_root};{existing_pythonpath}"
        env = {**os.environ, "PYTHONPATH": pythonpath, "PYTHONUNBUFFERED": "1"}
        _nemo_settings = _load_settings(config)
        # Propagate the API key from settings so nemo_code_runtime subprocess can authenticate
        # against non-local endpoints (e.g. NVIDIA NIM) where LMSTUDIO_API_KEY is not set.
        _settings_api_key = str(_nemo_settings.get("api_key") or job.payload.get("api_key") or "")
        if _settings_api_key and _settings_api_key != "lm-studio":
            env.setdefault("LMSTUDIO_API_KEY", _settings_api_key)
        _nemo_url = str(_nemo_settings.get("nemo_mcp_url") or "")
        _nemo_db = str(config.memory_db) if config.memory_db else ""
        if config.memory_db is not None and _nemo_configured(config.memory_db, _nemo_url):
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
            except Exception as exc:
                logger.warning("NEMO context_bootstrap failed for job %s: %s", job.job_id, exc)
            # Pre-load task-specific memories (code patterns, past corrections) so the
            # subprocess inherits a warmed NEMO context relevant to this exact task.
            try:
                _anticipate_result = mcp_call_nemo_tool(
                    "anticipate",
                    lifecycle_phase="start",
                    memory_db=_nemo_db,
                    mcp_url=_nemo_url,
                    context=f"handoff task: {str(job.payload.get('objective') or 'handoff job')[:200]}",
                    limit=5,
                    tags_include=["code_pattern", "correction", "plan_loop_success"],
                )
                _anticipated = (_anticipate_result or {}).get("memories") or []
                if _anticipated:
                    logger.info(
                        "NEMO anticipate: %d relevant memories pre-loaded for job %s",
                        len(_anticipated), job.job_id,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.debug("NEMO anticipate (non-critical) failed for job %s: %s", job.job_id, exc)
        _write_current_job_context(config, job.job_id)
        with self._lock:
            if job.status in {"cancelled", "paused"}:
                return
        try:
            job.process = subprocess.Popen(
                job.command,
                cwd=config.repo_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
                close_fds=True,  # prevent inheriting parent's pipe handles on Windows
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
                            job.last_stdout_ts = time.time()
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
                _reader.join(timeout=15.0)
                if _reader.is_alive():
                    logger.warning("pipe_reader for %s did not drain in 15s", job.job_id)
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
            self._auto_merge_on_completion(config, job)
            if self.post_completion_hook is not None:
                try:
                    self.post_completion_hook(job)
                except Exception:  # noqa: BLE001
                    logger.warning("post_completion_hook failed for %s", job.job_id, exc_info=True)
        except OSError as error:
            if job.heartbeat_stop is not None:
                job.heartbeat_stop.set()
            self._join_heartbeat(job)
            self._set_status(job, "failed")
            job.error = str(error)
            self._append_log(job, str(error))
            if self.post_completion_hook is not None:
                try:
                    self.post_completion_hook(job)
                except Exception:  # noqa: BLE001
                    logger.warning("post_completion_hook failed for %s", job.job_id, exc_info=True)
        finally:
            _write_current_job_context(config, None)
            if config.memory_db is not None and _nemo_configured(config.memory_db, _nemo_url):
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
                except Exception as exc:
                    logger.warning("NEMO store_conversation failed for job %s: %s", job.job_id, exc)
            if job.run_thread is threading.current_thread():
                job.run_thread = None
