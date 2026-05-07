from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from difflib import SequenceMatcher
from hashlib import sha256
from datetime import datetime, timezone
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

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
        merged_payload = {**settings, **payload}
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
        merged_payload = {**settings, **payload}
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
        if source.status != "paused":
            raise ValueError("only paused jobs can be resumed")
        payload = dict(source.payload)
        resumed = self.start(config, payload)
        resumed.logs.insert(0, f"resumed from {job_id}")
        return resumed

    def _stop(self, job_id: str, stopped_status: str, log_line: str) -> HandoffJob:
        job = self.get(job_id)
        job.logs.append(log_line)
        if job.process and job.process.poll() is None:
            job.status = stopped_status
            job.process.terminate()
        elif job.status not in {"completed", "failed"}:
            job.status = stopped_status
        return job

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
            "--validation-policy",
            str(payload.get("validation_policy") or "smoke"),
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
        if config.memory_db is None:
            command.append("--no-memory-db")
        else:
            command.extend(("--memory-db", str(config.memory_db)))
        command.extend(("--model-profile", str(payload.get("model") or "nvidia.agentic.coder-4b")))
        command.extend(("--lmstudio-base-url", str(payload.get("base_url") or "http://localhost:1234/v1")))
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
            job.status = "running"
            if job.process.stdout:
                try:
                    for line in job.process.stdout:
                        job.logs.append(line.rstrip())
                finally:
                    job.process.stdout.close()
            job.returncode = job.process.wait()
            if job.status in {"cancelled", "paused"}:
                job.logs.append(f"job {job.status}")
                return
            job.status = "completed" if job.returncode == 0 else "failed"
            job.logs.append(f"job finished returncode={job.returncode}")
        except OSError as error:
            job.status = "failed"
            job.error = str(error)
            job.logs.append(str(error))


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
        "provider": "fake",
        "memory_db": str(config.memory_db) if config.memory_db else "",
        "runtime_path": str(config.runtimes_path),
        "timeout_seconds": 120,
        "max_runtime_minutes": 120,
        "heartbeat_minutes": 15,
        "max_heartbeats": 4,
        "token_budget": 32000,
        "validation_policy": "smoke",
        "nemo_required": config.memory_db is not None,
        "quality_core": "product/aider",
        "recent_repos": [str(config.repo_path)],
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
        raise ValueError("request body too large")
    payload = json.loads(handler.rfile.read(length).decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    return payload


def _source_json(payload: dict[str, object]) -> str:
    source_json = payload.get("source_json")
    if not isinstance(source_json, str) or not source_json:
        raise ValueError("source_json is required")
    return source_json


def _apply_json(payload: dict[str, object]) -> str:
    apply_json = payload.get("apply_json")
    if not isinstance(apply_json, str) or not apply_json:
        raise ValueError("apply_json is required")
    return apply_json


def _job_id(payload: dict[str, object]) -> str:
    job_id = payload.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        raise ValueError("job_id is required")
    return job_id


def _message(payload: dict[str, object]) -> str:
    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("message is required")
    return message.strip()


def _file_path(payload: dict[str, object]) -> str:
    file_path = payload.get("file_path")
    if not isinstance(file_path, str) or not file_path:
        raise ValueError("file_path is required")
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
    raise ValueError(f"{key} must be a string or list")


def _objective(payload: dict[str, object]) -> str:
    objective = payload.get("objective")
    if not isinstance(objective, str) or not objective.strip():
        raise ValueError("objective is required")
    return objective.strip()


def _nemo_adapter(memory_db: Path | None) -> PersistentNemoAdapter | None:
    if memory_db is None:
        return None
    return PersistentNemoAdapter(PersistentMemoryStore(memory_db))


def _provider_mode(payload: dict[str, object]) -> str:
    provider = str(payload.get("provider") or "fake")
    if provider not in {"fake", "subprocess"}:
        raise ValueError("provider must be fake or subprocess")
    return provider


def _timeout_seconds(payload: dict[str, object]) -> float:
    value = payload.get("timeout_seconds", 30.0)
    try:
        timeout = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError("timeout_seconds must be numeric") from error
    if timeout <= 0:
        raise ValueError("timeout_seconds must be positive")
    return timeout


def _chat_base_url(payload: dict[str, object]) -> str:
    value = payload.get("base_url") or payload.get("model_base_url") or "http://localhost:1234/v1"
    if not isinstance(value, str) or not value.strip():
        raise ValueError("model_base_url is required")
    return value.strip().rstrip("/")


def _chat_model(payload: dict[str, object]) -> str:
    value = payload.get("model") or payload.get("default_model") or "nvidia.agentic.coder-4b"
    if not isinstance(value, str) or not value.strip():
        raise ValueError("default_model is required")
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
                    "You are the real Mission Control coding agent for a local-first NEMO/Aider platform. "
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
        "validation_policy",
        "quality_core",
    }
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
        raise ValueError("url is required")
    if not isinstance(destination, str) or not destination.strip():
        raise ValueError("destination is required")
    target = Path(destination).resolve()
    if target.exists() and any(target.iterdir()):
        raise ValueError("destination already exists and is not empty")
    target.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(["git", "clone", url, str(target)], cwd=server.config.repo_path, text=True, capture_output=True, timeout=600)
    if completed.returncode != 0:
        raise ValueError(completed.stderr.strip() or completed.stdout.strip() or "git clone failed")
    return api_repo_open(server, {"repo_path": str(target)})


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
    plan = build_merge_plan(load_headless_result_json(_source_json(payload)))
    result = apply_merge_plan(plan, approve_review=bool(payload.get("approve_review")), autonomy_profile=str(payload.get("autonomy_profile") or "manual"))
    config.apply_results_path.mkdir(parents=True, exist_ok=True)
    apply_json = config.apply_results_path / f"{_safe_stem(result.task_id)}-{_safe_stem(result.run_id)}.json"
    apply_json.write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    _write_apply_memory(result, config.memory_db)
    return {"ok": True, "apply_json": str(apply_json), "result": result.to_dict(), "state": api_state(config)}


def api_apply_selection(config: MissionControlServerConfig, payload: dict[str, object]) -> dict[str, object]:
    if not bool(payload.get("approve_review")):
        raise PermissionError("review approval required before applying selected changes")
    plan = build_merge_plan(load_headless_result_json(_source_json(payload)))
    if not plan.mergeable:
        raise PermissionError(f"merge plan is not mergeable: {', '.join(plan.risk_flags)}")
    accepted_files_payload = payload.get("accepted_files", [])
    accepted_hunks_payload = payload.get("accepted_hunks", {})
    accepted_files = set(str(item) for item in accepted_files_payload) if isinstance(accepted_files_payload, list) else set()
    accepted_hunks = accepted_hunks_payload if isinstance(accepted_hunks_payload, dict) else {}
    applied: list[str] = []
    created_files: list[str] = []
    updated_files: list[str] = []
    backup_files: list[str] = []
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
    return {"ok": True, "apply_json": str(apply_json), "result": result.to_dict(), "state": api_state(config)}


def api_handoff(config: MissionControlServerConfig, payload: dict[str, object]) -> dict[str, object]:
    payload = {**_load_settings(config), **payload}
    objective = _objective(payload)
    run_suffix = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    short_id = uuid4().hex[:8]
    task_id = f"mc-task-{run_suffix}-{short_id}"
    run_id = f"mc-run-{run_suffix}-{short_id}"
    target_files = _string_list(payload, "target_files", ())
    request = HandoffRequest(
        prd=objective,
        repo_path=str(config.repo_path),
        acceptance_criteria=_string_list(payload, "acceptance_criteria", ("implementation satisfies the objective",)),
        validation_commands=validation_commands_for_policy(str(payload.get("validation_policy") or "smoke"), _string_list(payload, "validation_commands", ())),
    )
    result = execute_long_handoff_supervisor(
        request,
        budget=LongHandoffBudget(
            max_runtime_minutes=int(payload.get("max_runtime_minutes") or 30),
            heartbeat_minutes=int(payload.get("heartbeat_minutes") or 10),
            max_heartbeats=int(payload.get("max_heartbeats") or 1),
            token_budget=int(payload.get("token_budget") or 8000),
        ),
        task_id=task_id,
        run_id=run_id,
        validation_cwd="runtime",
        provider_mode=_provider_mode(payload),
        timeout_seconds=_timeout_seconds(payload),
        target_files=target_files,
        validation_policy=str(payload.get("validation_policy") or "smoke"),
        nemo_adapter=_nemo_adapter(config.memory_db),
    )
    config.run_results_path.mkdir(parents=True, exist_ok=True)
    result_json = config.run_results_path / f"{task_id}-{run_id}.json"
    save_headless_result_json(result, result_json)
    return {"ok": True, "run_json": str(result_json), "summary": result.to_summary_dict(), "state": api_state(config)}


def api_handoff_start(server: "MissionControlHttpServer", payload: dict[str, object]) -> dict[str, object]:
    job = server.jobs.start(server.config, payload)
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


def api_self_modify_start(server: "MissionControlHttpServer", payload: dict[str, object]) -> dict[str, object]:
    job = server.jobs.start_self_modify(server.config, payload)
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


def _is_self_interface_request(message: str) -> bool:
    text = message.lower()
    self_terms = ("tu propia", "tus ", "propia interfaz", "propia interfase", "automejora", "self", "mission control", "interfaz", "interfase", "interface")
    color_terms = ("color", "colores", "amarillo", "dorado", "gold", "yellow", "negro", "black")
    change_terms = ("cambia", "cambiar", "ajusta", "modifica", "mejora", "mejorar", "replace", "sustituye")
    return any(term in text for term in self_terms) and any(term in text for term in color_terms) and any(term in text for term in change_terms)


def _self_interface_action(message: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "id": "self-modify-interface-colors",
        "kind": "self_modify",
        "label": "Self-mod UI",
        "summary": "Run a NEMOCODE self-modification against the real Mission Control interface files.",
        "payload": {
            "objective": message,
            "task_type": "tool_expansion",
            "target_files": ["apps/mission-control/src/styles.css", "apps/mission-control/src/main.tsx"],
            "validation_policy": "targeted",
            "validation_commands": ["npm --prefix apps/mission-control run build"],
            "provider": "subprocess",
            "timeout_seconds": payload.get("timeout_seconds") or "180",
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
    text = message.lower()
    selected_objective = "the selected run"
    if selected and isinstance(selected.get("task"), dict):
        selected_objective = str(selected["task"].get("objective") or selected_objective)
    if _is_self_interface_request(message):
        actions.append(_self_interface_action(message, payload))
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
                    "provider": payload.get("provider") or "fake",
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
                    "provider": payload.get("provider") or "fake",
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
                    "provider": payload.get("provider") or "fake",
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
    if provider == "subprocess":
        context_summary = _agent_context_summary(selected, changed_files, risk_flags, mergeable)
        response = _lmstudio_chat_completion(payload, message, context_summary)
        tool_calls.append(
            {
                "id": f"tool-{uuid4().hex[:8]}",
                "name": "lmstudio.chat_completions",
                "status": "completed",
                "summary": f"Model={_chat_model(payload)} base_url={_chat_base_url(payload)}.",
            }
        )
    else:
        if any(action.get("kind") == "self_modify" for action in actions):
            response = "[fake planner] I called the NEMO memory tool plane and prepared a real self-modification action against Mission Control UI files."
        else:
            response = "[fake planner] I called the NEMO memory tool plane and prepared the next safe actions." if selected else "[fake planner] I called the NEMO memory tool plane and prepared actions from your prompt."
    return {
        "ok": True,
        "message": {
            "id": f"msg-{uuid4().hex[:8]}",
            "role": "assistant",
            "content": response if provider == "subprocess" else f"{response}{risk_note}",
            "tool_calls": tool_calls,
            "actions": actions,
        },
    }


def api_rollback(config: MissionControlServerConfig, payload: dict[str, object]) -> dict[str, object]:
    result = MergeApplyResult.from_dict(json.loads(Path(_apply_json(payload)).read_text(encoding="utf-8")))
    rollback = rollback_apply_result(result, approve_review=bool(payload.get("approve_review")))
    return {"ok": True, "result": rollback.to_dict(), "state": api_state(config)}


class MissionControlRequestHandler(BaseHTTPRequestHandler):
    server: "MissionControlHttpServer"

    def do_OPTIONS(self) -> None:
        _json_response(self, 204, {})

    def do_GET(self) -> None:
        if not self._check_rate_limit():
            return
        route = urlparse(self.path).path
        if route == "/api/jobs":
            self._handle(lambda _: api_jobs(self.server), {})
            return
        if route == "/api/applies":
            self._handle(lambda _: api_applies(self.server.config), {})
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
            "/api/artifacts/cleanup": lambda payload: api_cleanup(self.server.config, payload),
            "/api/file": lambda payload: api_file(self.server.config, payload),
            "/api/handoff": lambda payload: api_handoff(self.server.config, payload),
            "/api/handoff/start": lambda payload: api_handoff_start(self.server, payload),
            "/api/job": lambda payload: api_job(self.server, payload),
            "/api/job/cancel": lambda payload: api_job_cancel(self.server, payload),
            "/api/job/pause": lambda payload: api_job_pause(self.server, payload),
            "/api/job/resume": lambda payload: api_job_resume(self.server, payload),
            "/api/self-modify/start": lambda payload: api_self_modify_start(self.server, payload),
            "/api/agent/message": lambda payload: api_agent_message(self.server.config, payload),
            "/api/nemo": lambda payload: api_nemo(self.server.config, payload),
            "/api/self-mod/insights": lambda payload: api_self_mod_insights(self.server.config, payload),
            "/api/review": lambda payload: api_review(self.server.config, payload),
            "/api/apply": lambda payload: api_apply(self.server.config, payload),
            "/api/apply-selection": lambda payload: api_apply_selection(self.server.config, payload),
            "/api/rollback": lambda payload: api_rollback(self.server.config, payload),
        }
        action = handlers.get(urlparse(self.path).path)
        if action is None:
            _json_response(self, 404, {"error": "not_found"})
            return
        try:
            payload = _load_body(self)
        except (json.JSONDecodeError, ValueError) as error:
            _json_response(self, 400, {"error": str(error)})
            return
        self._handle(action, payload)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _handle(self, action: Any, payload: dict[str, object]) -> None:
        try:
            _json_response(self, 200, action(payload))
        except PermissionError as error:
            _json_response(self, 409, {"error": str(error)})
        except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError) as error:
            _json_response(self, 400, {"error": str(error)})

    def _check_rate_limit(self) -> bool:
        client_ip = self.client_address[0]
        status = self.server.rate_limiter.check(client_ip)
        if status == "reject":
            _json_response(self, 429, {"error": "Too many requests"})
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


def run_server(host: str, port: int, config: MissionControlServerConfig) -> None:
    server = MissionControlHttpServer((host, port), config)
    print(f"Mission Control API listening on http://{host}:{port}")
    server.serve_forever()