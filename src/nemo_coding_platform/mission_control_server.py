from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
import urllib.error
import urllib.request
from difflib import SequenceMatcher
from hashlib import sha256
from datetime import datetime, timezone
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlparse
from uuid import uuid4

from nemo_coding_platform.core.evals import score_persisted_result, score_spec10_lite
from nemo_coding_platform.core.headless_handoff import HandoffRequest
from nemo_coding_platform.core.long_handoff_supervisor import LongHandoffBudget, execute_long_handoff_supervisor
from nemo_coding_platform.core.memory import MemoryAtomType
from nemo_coding_platform.core.memory_persistence import PersistentMemoryStore
from nemo_coding_platform.core.mission_control import build_mission_control_state
from nemo_coding_platform.core.nemo_adapter import PersistentNemoAdapter
from nemo_coding_platform.core.nemo_lifecycle import NemoLifecyclePhase
from nemo_coding_platform.core.persistence import load_headless_result_json, save_headless_result_json
from nemo_coding_platform.core.review_gate import MergeApplyResult, apply_merge_plan, build_merge_plan, rollback_apply_result
from nemo_coding_platform.core.rate_limiter import RateLimiter
from nemo_coding_platform.core.self_modification import self_mod_impact, self_mod_similar_runs, self_mod_trajectory
from nemo_coding_platform.core.validation import validation_commands_for_policy
from nemo_coding_platform.nemocode_mcp_tools import mcp_call_nemo_tool


DEFAULT_APPLY_RESULTS = ".nemo-runtimes/mission-control/apply-results"
DEFAULT_RUN_RESULTS = ".nemo-runtimes/mission-control/runs"
JOB_LOG_LIMIT = 10_000
DECISION_LOG_LIMIT = 200


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
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

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
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if include_logs:
            payload["logs"] = list(self.logs)
        return payload


class HandoffJobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, HandoffJob] = {}
        self._lock = threading.Lock()

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
        command = self._build_command(config, merged_payload, objective, provider, timeout, task_id, run_id, run_json)
        job = HandoffJob(job_id, task_id, run_id, str(run_json), "starting", command, dict(merged_payload), ["starting handoff job"])
        with self._lock:
            self._jobs[job_id] = job
        threading.Thread(target=self._run_job, args=(config, job), daemon=True).start()
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
        threading.Thread(target=self._run_job, args=(config, job), daemon=True).start()
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
        return job

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

    def _set_status(self, job: HandoffJob, status: str) -> None:
        job.status = status
        job.updated_at = datetime.now(timezone.utc).isoformat()

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
            str(payload.get("token_budget") or 32000),
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
        command.extend(("--model-profile", str(payload.get("model") or "nvidia.agentic.coder-4b")))
        command.extend(("--lmstudio-base-url", str(payload.get("base_url") or "http://localhost:1234/v1")))
        if payload.get("pause_after_minutes") is not None:
            command.extend(("--pause-after-minutes", str(payload.get("pause_after_minutes"))))
        if bool(payload.get("validation_escalation_mode")):
            command.append("--validation-escalation-mode")
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
        command.extend(("--model-profile", str(payload.get("model") or payload.get("default_model") or "nvidia.agentic.coder-4b")))
        command.extend(("--lmstudio-base-url", str(payload.get("base_url") or payload.get("model_base_url") or "http://localhost:1234/v1")))
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
        env = {**os.environ, "PYTHONPATH": pythonpath}
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
            if job.process.stdout:
                try:
                    for line in job.process.stdout:
                        self._append_log(job, line.rstrip())
                finally:
                    job.process.stdout.close()
            job.returncode = job.process.wait()
            if job.status in {"cancelled", "paused"}:
                self._append_log(job, f"job {job.status}")
                return
            self._set_status(job, "completed" if job.returncode == 0 else "failed")
            self._append_log(job, f"job finished returncode={job.returncode}")
        except OSError as error:
            self._set_status(job, "failed")
            job.error = str(error)
            self._append_log(job, str(error))


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
    return {
        "repo_path": str(config.repo_path),
        "model_base_url": "http://localhost:1234/v1",
        "default_model": "nvidia.agentic.coder-4b",
        "provider": "subprocess",
        "memory_db": str(config.memory_db) if config.memory_db else "",
        "runtime_path": str(config.runtimes_path),
        "timeout_seconds": 120,
        "max_runtime_minutes": 120,
        "heartbeat_minutes": 15,
        "max_heartbeats": 4,
        "token_budget": 32000,
        "pause_after_minutes": None,
        "plan_minutes": 30,
        "execute_minutes": 60,
        "review_minutes": 30,
        "repair_time_limit_seconds": 3600,
        "validation_time_budget_seconds": 1800,
        "validation_escalation_mode": False,
        "validation_policy": "smoke",
        "nemo_required": config.memory_db is not None,
        "quality_core": "product/nemo_code_runtime",
        "recent_repos": [str(config.repo_path)],
        "browser_homepage": "https://github.com",
        "browser_last_url": "",
        "browser_history": [],
        "browser_search_query": "",
        "browser_search_history": [],
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
    recent = settings.get("recent_repos")
    if not isinstance(recent, list):
        recent = []
    settings["recent_repos"] = _recent_repos(tuple(str(item) for item in recent), str(config.repo_path))
    if settings.get("provider") != "subprocess":
        settings["provider"] = "subprocess"
    if settings.get("validation_policy") not in {"none", "smoke", "targeted", "full"}:
        settings["validation_policy"] = "smoke"
    return settings


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
    return HandoffRequest(
        prd=linked_prd or objective,
        repo_path=str(config.repo_path),
        acceptance_criteria=_string_list(payload, "acceptance_criteria", ("implementation satisfies the objective",)),
        validation_commands=validation_commands_for_policy(str(payload.get("validation_policy") or "smoke"), _string_list(payload, "validation_commands", ())),
        objective_summary=objective,
        linked_prd=linked_prd,
        spec_mode=_handoff_spec_mode(payload),
    )


def _nemo_adapter(memory_db: Path | None) -> PersistentNemoAdapter | None:
    if memory_db is None:
        return None
    return PersistentNemoAdapter(PersistentMemoryStore(memory_db))


def _provider_mode(payload: dict[str, object]) -> str:
    provider = str(payload.get("provider") or "subprocess")
    if provider != "subprocess":
        raise _bad_request("provider must be subprocess", error_code="invalid_provider")
    return provider


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
    return timeout


def _chat_base_url(payload: dict[str, object]) -> str:
    value = payload.get("base_url") or payload.get("model_base_url") or "http://localhost:1234/v1"
    if not isinstance(value, str) or not value.strip():
        raise _bad_request("model_base_url is required", error_code="invalid_model_base_url")
    return value.strip().rstrip("/")


def _chat_model(payload: dict[str, object]) -> str:
    value = payload.get("model") or payload.get("default_model") or "nvidia.agentic.coder-4b"
    if not isinstance(value, str) or not value.strip():
        raise _bad_request("default_model is required", error_code="invalid_default_model")
    return value.strip()


def _agent_context_summary(selected: dict[str, Any] | None, changed_files: tuple[str, ...], risk_flags: tuple[str, ...], mergeable: bool) -> str:
    if not selected:
        return "No run is selected. Answer the user's chat request and propose a safe next action."
    task = selected.get("task", {}) if isinstance(selected.get("task"), dict) else {}
    run = selected.get("run", {}) if isinstance(selected.get("run"), dict) else {}
    portfolio = selected.get("portfolio", {}) if isinstance(selected.get("portfolio"), dict) else {}
    context = str(portfolio.get("context") or "")[:2400]
    return "\n".join(
        line for line in (
            f"Selected task: {task.get('id', 'unknown')} / {run.get('id', 'unknown')}",
            f"Objective: {task.get('objective') or task.get('title') or 'Untitled'}",
            f"Mergeable: {mergeable}",
            f"Changed files: {', '.join(changed_files) or 'none'}",
            f"Risk flags: {', '.join(risk_flags) or 'none'}",
            f"NEMO context:\n{context}" if context else "",
        ) if line
    )


def _lmstudio_chat_completion(payload: dict[str, object], user_message: str, context_summary: str) -> str:
    body = {
        "model": _chat_model(payload),
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are the real Mission Control coding agent for the local-first NEMO CODE platform. "
                    "Answer in the user's language. Be concise, specific, and operational. "
                    "Use the selected run and NEMO context when available. Do not claim you applied code unless an explicit action did it."
                ),
            },
            {"role": "system", "content": context_summary},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.2,
        "stream": False,
    }
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"{_chat_base_url(payload)}/chat/completions",
        data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {os.environ.get('LMSTUDIO_API_KEY', 'lm-studio')}"},
        method="POST",
    )
    timeout = min(max(_timeout_seconds(payload), 1.0), 120.0)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        raise ValueError(f"LM Studio chat failed: HTTP {error.code} {details[:400]}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise ValueError(f"LM Studio chat failed: {error}") from error
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


def _write_apply_memory(result: MergeApplyResult, memory_db: Path | None) -> None:
    if memory_db is None:
        return
    adapter = PersistentNemoAdapter(PersistentMemoryStore(memory_db))
    adapter.call(
        NemoLifecyclePhase.REVIEW,
        "store_conversation",
        summary=(
            f"Mission Control apply completed task={result.task_id} "
            f"run={result.run_id} applied_files={','.join(result.applied_files)}"
        ),
        topic="Mission Control Review Gate",
        tags=(result.task_id, result.run_id, "mission_control", "apply", "review_to_main"),
        atom_type=MemoryAtomType.DECISION.value,
        source_scope="mission_control",
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


def api_state(config: MissionControlServerConfig) -> dict[str, object]:
    settings = _load_settings(config)
    recent = tuple(str(item) for item in settings.get("recent_repos", []) if isinstance(item, str))
    return build_mission_control_state(config.repo_path, config.runtimes_path, settings=settings, recent_repos=recent)


def api_settings(server: "MissionControlHttpServer", payload: dict[str, object]) -> dict[str, object]:
    settings = _load_settings(server.config)
    allowed = {
        "model_base_url",
        "default_model",
        "provider",
        "memory_db",
        "runtime_path",
        "timeout_seconds",
        "max_runtime_minutes",
        "heartbeat_minutes",
        "max_heartbeats",
        "token_budget",
        "pause_after_minutes",
        "plan_minutes",
        "execute_minutes",
        "review_minutes",
        "repair_time_limit_seconds",
        "validation_time_budget_seconds",
        "validation_escalation_mode",
        "validation_policy",
        "quality_core",
    }
    unknown = {key for key in payload if key not in allowed and key != "repo_path"}
    if unknown:
        raise _bad_request(f"unknown setting key(s): {', '.join(sorted(unknown))}", error_code="invalid_setting_key")
    if "provider" in payload:
        _provider_mode(payload)
    if "validation_policy" in payload and payload["validation_policy"] not in {"none", "smoke", "targeted", "full"}:
        raise _bad_request("validation_policy must be none, smoke, targeted, or full", error_code="invalid_validation_policy")
    for _numeric_key in (
        "timeout_seconds",
        "max_runtime_minutes",
        "heartbeat_minutes",
        "max_heartbeats",
        "token_budget",
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
    if "pause_after_minutes" in payload and payload.get("pause_after_minutes") is not None:
        try:
            pause_minutes = float(payload["pause_after_minutes"])
        except (TypeError, ValueError):
            raise _bad_request("pause_after_minutes must be numeric", error_code="invalid_setting_value")
        if pause_minutes <= 0:
            raise _bad_request("pause_after_minutes must be positive", error_code="invalid_setting_value")
    if "validation_escalation_mode" in payload and not isinstance(payload.get("validation_escalation_mode"), bool):
        raise _bad_request("validation_escalation_mode must be boolean", error_code="invalid_setting_value")
    for key in allowed:
        if key in payload:
            settings[key] = payload[key]
    repo_path = str(payload.get("repo_path") or server.config.repo_path)
    settings["recent_repos"] = _recent_repos(tuple(str(item) for item in settings.get("recent_repos", [])), repo_path)
    next_config = server.config.with_runtime_settings({**settings, "repo_path": repo_path})
    settings["repo_path"] = str(next_config.repo_path)
    settings["runtime_path"] = str(next_config.runtimes_path)
    settings["memory_db"] = str(next_config.memory_db) if next_config.memory_db else ""
    _save_settings(next_config, settings)
    server.config = next_config
    return {"ok": True, "settings": settings, "state": api_state(server.config)}


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
    return {"ok": True, "repo": {"path": str(repo), "is_git_repo": True}, "state": api_state(server.config)}


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
    run_suffix = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    short_id = uuid4().hex[:8]
    task_id = f"mc-task-{run_suffix}-{short_id}"
    run_id = f"mc-run-{run_suffix}-{short_id}"
    target_files = _string_list(payload, "target_files", ())
    request = _handoff_request(config, payload)
    result = execute_long_handoff_supervisor(
        request,
        budget=LongHandoffBudget(
            max_runtime_minutes=int(payload.get("max_runtime_minutes") or 30),
            heartbeat_minutes=int(payload.get("heartbeat_minutes") or 10),
            max_heartbeats=int(payload.get("max_heartbeats") or 1),
            token_budget=int(payload.get("token_budget") or 8000),
            pause_after_minutes=int(payload.get("pause_after_minutes")) if payload.get("pause_after_minutes") is not None else None,
            plan_minutes=int(payload.get("plan_minutes") or 30),
            execute_minutes=int(payload.get("execute_minutes") or 60),
            review_minutes=int(payload.get("review_minutes") or 30),
        ),
        task_id=task_id,
        run_id=run_id,
        validation_cwd="runtime",
        provider_mode=_provider_mode(payload),
        timeout_seconds=_timeout_seconds(payload),
        target_files=target_files,
        validation_policy=str(payload.get("validation_policy") or "smoke"),
        nemo_adapter=_nemo_adapter(config.memory_db),
        repair_time_limit_seconds=float(payload.get("repair_time_limit_seconds")) if payload.get("repair_time_limit_seconds") is not None else None,
        validation_time_budget_seconds=float(payload.get("validation_time_budget_seconds")) if payload.get("validation_time_budget_seconds") is not None else None,
        validation_escalation_mode=bool(payload.get("validation_escalation_mode")),
    )
    config.run_results_path.mkdir(parents=True, exist_ok=True)
    result_json = config.run_results_path / f"{task_id}-{run_id}.json"
    save_headless_result_json(result, result_json)
    return {"ok": True, "run_json": str(result_json), "summary": result.to_summary_dict(), "state": api_state(config)}


def api_handoff_start(server: "MissionControlHttpServer", payload: dict[str, object]) -> dict[str, object]:
    job = server.jobs.start(server.config, _enforce_workspace_scope(server.config, payload))
    return {"ok": True, "job": job.to_dict()}


def api_jobs(server: "MissionControlHttpServer") -> dict[str, object]:
    return {"ok": True, "jobs": server.jobs.list()}


def api_job(server: "MissionControlHttpServer", payload: dict[str, object]) -> dict[str, object]:
    return {"ok": True, "job": server.jobs.get(_job_id(payload)).to_dict(), "state": api_state(server.config)}


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
    job = server.jobs.start_self_modify(server.config, _enforce_workspace_scope(server.config, payload))
    return {"ok": True, "job": job.to_dict()}


def _nemo_chat_tool_call(
    config: MissionControlServerConfig,
    tool_calls: list[dict[str, object]],
    tool_name: str,
    *,
    lifecycle_phase: str | None = None,
    **arguments: Any,
) -> dict[str, Any]:
    display_name = f"nemocode.{tool_name}"
    if config.memory_db is None:
        tool_calls.append({"id": f"tool-{uuid4().hex[:8]}", "name": display_name, "status": "skipped", "summary": "NEMO memory database is disabled for this Mission Control session."})
        return {}
    result = mcp_call_nemo_tool(tool_name, lifecycle_phase=lifecycle_phase, memory_db=str(config.memory_db), **arguments)
    ok = bool(result.get("ok"))
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    tool_calls.append(
        {
            "id": f"tool-{uuid4().hex[:8]}",
            "name": display_name,
            "status": "completed" if ok else "failed",
            "summary": _nemo_tool_summary(tool_name, payload if ok else result),
        }
    )
    return payload if ok else {}


def _nemo_tool_summary(tool_name: str, payload: dict[str, Any]) -> str:
    if tool_name == "prime_context":
        context = str(payload.get("context") or "")
        return f"Loaded Mission Control memory context chars={len(context)}."
    if tool_name == "build_context_portfolio":
        return f"Built context portfolio tokens={payload.get('estimated_tokens', 0)} evidence={len(payload.get('evidence_handles', []))}."
    if tool_name == "search_memories":
        return f"Searched memory; matches={len(payload.get('memories', []))}."
    if tool_name == "store_conversation":
        return f"Stored conversation memory atom={payload.get('atom_id', 'unknown')}."
    if "error" in payload:
        return str(payload.get("error"))
    return "NEMO tool completed."


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
        "summary": "Run a NEMOCODE self-modification against the real Mission Control interface files.",
        "payload": {
            "objective": message,
            "task_type": "tool_expansion",
            "target_files": _mission_control_ui_targets(repo_path),
            "validation_policy": "targeted",
            "validation_commands": ["npm --prefix apps/mission-control run build", "npm --prefix apps/mission-control run test:smoke"],
            "provider": "subprocess",
            "timeout_seconds": payload.get("timeout_seconds") or "180",
            "model": payload.get("default_model") or payload.get("model") or "nvidia.agentic.coder-4b",
            "base_url": payload.get("model_base_url") or payload.get("base_url") or "http://localhost:1234/v1",
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
            "timeout_seconds": payload.get("timeout_seconds") or "240",
            "model": payload.get("default_model") or payload.get("model") or "nvidia.agentic.coder-4b",
            "base_url": payload.get("model_base_url") or payload.get("base_url") or "http://localhost:1234/v1",
        },
    }


def api_agent_message(config: MissionControlServerConfig, payload: dict[str, object]) -> dict[str, object]:
    payload = {**_load_settings(config), **payload}
    message = _message(payload)
    provider = _provider_mode(payload)
    source_json = payload.get("source_json")
    tool_calls: list[dict[str, object]] = []
    _nemo_chat_tool_call(config, tool_calls, "prime_context", lifecycle_phase="start", topic="Mission Control conversation", limit=8)
    _nemo_chat_tool_call(config, tool_calls, "build_context_portfolio", lifecycle_phase="plan", task=message, topic="Mission Control conversation", token_budget=900, limit=40)
    _nemo_chat_tool_call(config, tool_calls, "search_memories", lifecycle_phase="review", query=message, topic="NEMOCODE self-modification", limit=5)
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
                    "timeout_seconds": payload.get("timeout_seconds") or "120",
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
                    "timeout_seconds": payload.get("timeout_seconds") or "120",
                },
            }
        )
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
                    "timeout_seconds": payload.get("timeout_seconds") or "120",
                },
            }
        )
    _nemo_chat_tool_call(
        config,
        tool_calls,
        "store_conversation",
        lifecycle_phase="review",
        summary=f"Mission Control chat user request: {message}",
        topic="Mission Control conversation",
        tags=("mission-control", "agent-chat"),
        atom_type=MemoryAtomType.SESSION_SUMMARY.value,
        source_scope="mission_control_chat",
        importance=8 if actions else 6,
    )
    risk_note = f" Risks: {', '.join(risk_flags)}." if risk_flags else ""
    context_summary = _agent_context_summary(selected, changed_files, risk_flags, mergeable)
    try:
        response = _lmstudio_chat_completion(payload, message, context_summary)
        tool_calls.append(
            {
                "id": f"tool-{uuid4().hex[:8]}",
                "name": "lmstudio.chat_completions",
                "status": "completed",
                "summary": f"Model={_chat_model(payload)} base_url={_chat_base_url(payload)}.",
            }
        )
    except ValueError as error:
        tool_calls.append(
            {
                "id": f"tool-{uuid4().hex[:8]}",
                "name": "lmstudio.chat_completions",
                "status": "failed",
                "summary": str(error),
            }
        )
        raise
    return {
        "ok": True,
        "message": {
            "id": f"msg-{uuid4().hex[:8]}",
            "role": "assistant",
            "content": f"{response}{risk_note}",
            "tool_calls": tool_calls,
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


class MissionControlRequestHandler(BaseHTTPRequestHandler):
    server: "MissionControlHttpServer"

    def do_OPTIONS(self) -> None:
        _json_response(self, 204, {})

    def do_GET(self) -> None:
        if not self._check_rate_limit():
            return
        route = urlparse(self.path).path
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
        if route != "/api/state":
            _json_response(self, 404, {"error": "not_found"})
            return
        self._handle(lambda _: api_state(self.server.config), {})

    def do_POST(self) -> None:
        if not self._check_rate_limit():
            return
        handlers = {
            "/api/refresh": lambda payload: api_state(self.server.config),
            "/api/settings": lambda payload: api_settings(self.server, payload),
            "/api/repo/open": lambda payload: api_repo_open(self.server, payload),
            "/api/repo/clone": lambda payload: api_repo_clone(self.server, payload),
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
            "/api/agent/message": lambda payload: api_agent_message(self.server.config, payload),
            "/api/nemo": lambda payload: api_nemo(self.server.config, payload),
            "/api/self-mod/insights": lambda payload: api_self_mod_insights(self.server.config, payload),
            "/api/eval": lambda payload: api_eval(self.server.config, payload),
            "/api/review": lambda payload: api_review(self.server.config, payload),
            "/api/apply": lambda payload: api_apply(self.server.config, payload),
            "/api/apply-selection": lambda payload: api_apply_selection(self.server.config, payload),
            "/api/rollback": lambda payload: api_rollback(self.server.config, payload),
            "/api/decision-log/append": lambda payload: api_decision_log_append(self.server.config, payload),
            "/api/kpis": lambda payload: api_kpis(self.server.config),
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
        self.jobs = HandoffJobManager()
        self.rate_limiter = RateLimiter()

    def server_close(self) -> None:
        for job in list(self.jobs._jobs.values()):
            if job.status not in {"completed", "failed", "cancelled"}:
                self.jobs.cancel(job.job_id)
        super().server_close()


def run_server(host: str, port: int, config: MissionControlServerConfig) -> None:
    server = MissionControlHttpServer((host, port), config)
    print(f"Mission Control API listening on http://{host}:{port}")
    server.serve_forever()