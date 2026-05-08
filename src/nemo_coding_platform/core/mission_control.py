from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nemo_coding_platform.core.persistence import load_headless_result_json, summarize_persisted_result
from nemo_coding_platform.core.review_gate import build_merge_plan


@dataclass(frozen=True, slots=True)
class MissionControlRun:
    task_id: str
    run_id: str
    objective: str
    linked_prd: str | None
    linked_specs: tuple[str, ...]
    repo_path: str
    sandbox_path: str
    runtime_id: str
    runtime_state: str
    execution_phase: str
    validation_profile: str
    permission_profile: str
    model_profile: str
    continuation_state: dict[str, object] | None
    grade: str
    score: int
    changed_files: tuple[str, ...]
    event_count: int
    artifact_count: int
    review_status: str
    risk_flags: tuple[str, ...]
    mergeable: bool
    source_json: str
    timeline: tuple[dict[str, object], ...]
    decision_log: tuple[dict[str, object], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "objective": self.objective,
            "linked_prd": self.linked_prd,
            "linked_specs": list(self.linked_specs),
            "repo_path": self.repo_path,
            "sandbox_path": self.sandbox_path,
            "runtime_id": self.runtime_id,
            "runtime_state": self.runtime_state,
            "execution_phase": self.execution_phase,
            "validation_profile": self.validation_profile,
            "permission_profile": self.permission_profile,
            "model_profile": self.model_profile,
            "continuation_state": self.continuation_state,
            "grade": self.grade,
            "score": self.score,
            "changed_files": list(self.changed_files),
            "event_count": self.event_count,
            "artifact_count": self.artifact_count,
            "review_status": self.review_status,
            "risk_flags": list(self.risk_flags),
            "mergeable": self.mergeable,
            "source_json": self.source_json,
            "timeline": list(self.timeline),
            "decision_log": list(self.decision_log),
        }


def _run_json_files(root: Path) -> tuple[Path, ...]:
    if root.is_file() and root.suffix == ".json":
        return (root,)
    if not root.exists():
        return ()
    return tuple(sorted(path for path in root.rglob("*.json") if path.is_file()))


def _source_mtime(run: MissionControlRun) -> float:
    try:
        return Path(run.source_json).stat().st_mtime
    except OSError:
        return 0.0


def _timeline_preview(payload: dict[str, Any], limit: int = 12) -> tuple[dict[str, object], ...]:
    events = payload.get("timeline", [])
    if not isinstance(events, list):
        return ()
    preview: list[dict[str, object]] = []
    for event in events[-limit:]:
        if not isinstance(event, dict):
            continue
        preview.append(
            {
                "sequence": event.get("sequence"),
                "phase": event.get("phase"),
                "kind": event.get("kind"),
                "summary": event.get("summary"),
                "payload_ref": event.get("payload_ref"),
            }
        )
    return tuple(preview)


def _load_continuation_state(sandbox_path: str) -> dict[str, object] | None:
    if not sandbox_path:
        return None
    path = Path(sandbox_path) / "continuation-state.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"error": "continuation_state_unreadable", "path": str(path)}
    return payload if isinstance(payload, dict) else {"error": "continuation_state_invalid", "path": str(path)}


def _load_decision_log(sandbox_path: str, limit: int = 40) -> tuple[dict[str, object], ...]:
    if not sandbox_path:
        return ()
    path = Path(sandbox_path) / "decision-log.json"
    if not path.exists():
        return ()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ()
    if not isinstance(payload, list):
        return ()
    rows: list[dict[str, object]] = []
    for item in payload[:limit]:
        if isinstance(item, dict):
            rows.append(
                {
                    "id": item.get("id"),
                    "ts": item.get("ts"),
                    "action": item.get("action"),
                    "status": item.get("status"),
                    "detail": item.get("detail"),
                }
            )
    return tuple(rows)


def _review_status(grade: str, changed_files: tuple[str, ...], mergeable: bool, risk_flags: tuple[str, ...]) -> str:
    if risk_flags:
        return "blocked"
    if mergeable:
        return "awaiting_review"
    if changed_files:
        return "not_ready"
    if grade == "ready":
        return "no_changes"
    return "running"


def _mission_run_from_payload(path: Path, payload: dict[str, Any]) -> MissionControlRun | None:
    if not isinstance(payload.get("task"), dict) or not isinstance(payload.get("run"), dict):
        return None
    summary = summarize_persisted_result(payload)
    changed_files = tuple(str(item).replace("\\", "/") for item in summary.get("changed_files") or [])
    risk_flags: tuple[str, ...] = ()
    mergeable = False
    if changed_files:
        try:
            plan = build_merge_plan(payload)
            risk_flags = plan.risk_flags
            mergeable = plan.mergeable
        except (OSError, ValueError) as error:
            risk_flags = (f"plan_error:{error}",)
    task = payload["task"]
    run = payload["run"]
    grade = str(summary.get("grade") or "unknown")
    sandbox_path = str(run.get("sandbox_path") or "")
    return MissionControlRun(
        task_id=str(summary.get("task_id") or task.get("id") or ""),
        run_id=str(summary.get("run_id") or run.get("id") or ""),
        objective=str(task.get("objective") or task.get("title") or "Untitled task"),
        linked_prd=str(task.get("linked_prd")) if isinstance(task.get("linked_prd"), str) and task.get("linked_prd") else None,
        linked_specs=tuple(str(item) for item in task.get("linked_specs", []) if isinstance(item, str)) if isinstance(task.get("linked_specs"), list) else (),
        repo_path=str(task.get("repo_path") or ""),
        sandbox_path=sandbox_path,
        runtime_id=str(run.get("runtime_id") or ""),
        runtime_state=str(run.get("state") or ""),
        execution_phase=str(run.get("phase") or ""),
        validation_profile=str(run.get("validation_profile") or ""),
        permission_profile=str(run.get("permission_profile") or ""),
        model_profile=str(run.get("model_profile") or ""),
        continuation_state=_load_continuation_state(sandbox_path),
        grade=grade,
        score=int(summary.get("score") or 0),
        changed_files=changed_files,
        event_count=int(summary.get("events") or 0),
        artifact_count=int(summary.get("artifacts") or 0),
        review_status=_review_status(grade, changed_files, mergeable, risk_flags),
        risk_flags=risk_flags,
        mergeable=mergeable,
        source_json=str(path),
        timeline=_timeline_preview(payload),
        decision_log=_load_decision_log(sandbox_path),
    )


def build_mission_control_state(repo_path: str | Path = ".", runtimes_path: str | Path = ".nemo-runtimes", settings: dict[str, object] | None = None, recent_repos: tuple[str, ...] = ()) -> dict[str, object]:
    repo = Path(repo_path).resolve()
    runtime_root = Path(runtimes_path)
    if not runtime_root.is_absolute():
        runtime_root = repo / runtime_root
    runs: list[MissionControlRun] = []
    for path in _run_json_files(runtime_root):
        try:
            payload = load_headless_result_json(path)
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(payload, dict):
            continue
        run = _mission_run_from_payload(path, payload)
        if run is not None:
            runs.append(run)
    runs.sort(key=lambda item: (_source_mtime(item), item.source_json), reverse=True)
    review_queue = [run for run in runs if run.review_status in {"awaiting_review", "blocked"}]
    repos = sorted({run.repo_path for run in runs if run.repo_path} | {str(repo)} | set(recent_repos))
    state_settings = {
        "model_base_url": "http://localhost:1234/v1",
        "default_model": "nvidia.agentic.coder-4b",
        "provider": "subprocess",
        "memory_db": ".nemo-runtimes/nemo-memory.sqlite",
        "runtime_path": str(runtime_root),
        "timeout_seconds": 120,
        "max_runtime_minutes": 120,
        "heartbeat_minutes": 15,
        "max_heartbeats": 4,
        "token_budget": 32000,
        "validation_policy": "smoke",
        "nemo_required": True,
        "quality_core": "product/nemo_code_runtime",
        "recent_repos": list(recent_repos),
    }
    if settings:
        state_settings.update(settings)
    if not isinstance(state_settings.get("validation_policy"), str) or not state_settings.get("validation_policy"):
        state_settings["validation_policy"] = "smoke"
    return {
        "schema_version": 1,
        "product": "NEMO CODE Mission Control",
        "repo_path": str(repo),
        "runtimes_path": str(runtime_root),
        "repos": repos,
        "runs": [run.to_dict() for run in runs],
        "approval_queue": [run.to_dict() for run in review_queue],
        "settings": state_settings,
    }