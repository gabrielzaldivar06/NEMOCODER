from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nemo_coding_platform.core.event_emitter import emit_event
from nemo_coding_platform.core.headless_handoff import HandoffRequest
from nemo_coding_platform.core.headless_runner import HeadlessRunResult, execute_headless_handoff
from nemo_coding_platform.core.nemo_adapter import InMemoryNemoAdapter, PersistentNemoAdapter
from nemo_coding_platform.core.nemo_lifecycle import NemoLifecyclePhase
from nemo_coding_platform.core.task_run import AppendOnlyTimeline, Artifact, ArtifactType, EventKind, MemoryTrace, RunEvent
from nemo_coding_platform.core.worktree_runtime import WorktreeRuntimeSpec, snapshot_runtime_files, write_runtime_file


@dataclass(frozen=True, slots=True)
class LongHandoffBudget:
    max_runtime_minutes: int = 240
    heartbeat_minutes: int = 30
    max_heartbeats: int = 8
    token_budget: int = 64_000
    pause_after_minutes: int | None = None
    plan_minutes: int = 60
    execute_minutes: int = 120
    review_minutes: int = 60

    def validate(self) -> None:
        if self.max_runtime_minutes <= 0:
            raise ValueError("max_runtime_minutes must be positive")
        if self.heartbeat_minutes <= 0:
            raise ValueError("heartbeat_minutes must be positive")
        if self.max_heartbeats <= 0:
            raise ValueError("max_heartbeats must be positive")
        if self.token_budget <= 0:
            raise ValueError("token_budget must be positive")
        if self.pause_after_minutes is not None and self.pause_after_minutes <= 0:
            raise ValueError("pause_after_minutes must be positive when set")
        if self.plan_minutes <= 0:
            raise ValueError("plan_minutes must be positive")
        if self.execute_minutes <= 0:
            raise ValueError("execute_minutes must be positive")
        if self.review_minutes <= 0:
            raise ValueError("review_minutes must be positive")


@dataclass(frozen=True, slots=True)
class SupervisorHeartbeat:
    elapsed_minutes: int
    checkpoint_ref: str
    summary: str


@dataclass(frozen=True, slots=True)
class LongHandoffResumePlan:
    can_resume: bool
    task_id: str | None
    run_id: str | None
    objective: str | None
    provider_mode: str | None
    validation_policy: str | None
    validation_commands: tuple[str, ...]
    memory_writeback_handles: tuple[str, ...]
    resume_token: str | None
    resume_minute: int | None
    checkpoint_refs: tuple[str, ...]
    latest_atomic_checkpoint: str | None
    repair_cursor: int
    resume_validation_state: tuple[str, ...]
    snapshot_runtime_path: str | None
    next_action: str
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "can_resume": self.can_resume,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "objective": self.objective,
            "provider_mode": self.provider_mode,
            "validation_policy": self.validation_policy,
            "validation_commands": list(self.validation_commands),
            "memory_writeback_handles": list(self.memory_writeback_handles),
            "resume_token": self.resume_token,
            "resume_minute": self.resume_minute,
            "checkpoint_refs": list(self.checkpoint_refs),
            "latest_atomic_checkpoint": self.latest_atomic_checkpoint,
            "repair_cursor": self.repair_cursor,
            "resume_validation_state": list(self.resume_validation_state),
            "snapshot_runtime_path": self.snapshot_runtime_path,
            "next_action": self.next_action,
            "reasons": list(self.reasons),
        }


def _extract_atomic_resume_state(payload: dict[str, Any], state: dict[str, Any]) -> tuple[str | None, int, tuple[str, ...], str | None]:
    """Extract latest atomic checkpoint id, repair cursor, validation state, and snapshot path.

    Returns:
        (checkpoint_id, repair_cursor, validation_state, snapshot_runtime_path)
    """
    snapshots = payload.get("execution_snapshots")
    if not isinstance(snapshots, dict):
        snapshots = {}
    preferred = state.get("latest_atomic_checkpoint")
    preferred_id = str(preferred) if isinstance(preferred, str) and preferred else None

    atomic_candidates: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for checkpoint_id, checkpoint_payload in snapshots.items():
        if not isinstance(checkpoint_id, str) or not isinstance(checkpoint_payload, dict):
            continue
        snapshot = checkpoint_payload.get("snapshot")
        if not isinstance(snapshot, dict):
            continue
        if str(snapshot.get("resume_mode") or "") != "atomic":
            continue
        atomic_candidates.append((checkpoint_id, checkpoint_payload, snapshot))

    if not atomic_candidates:
        return None, 0, (), None

    scored_candidates: list[tuple[int, tuple[str, dict[str, Any], dict[str, Any]]]] = []
    for item in atomic_candidates:
        _, _, snapshot = item
        scored_candidates.append((max(0, int(snapshot.get("repair_cursor") or 0)), item))

    max_cursor = max(score for score, _ in scored_candidates)
    top_candidates = [item for score, item in scored_candidates if score == max_cursor]

    # On ties, prefer repair checkpoints because they capture the latest repair state.
    repair_ties = [item for item in top_candidates if item[0].startswith("checkpoint-repair")]

    selected: tuple[str, dict[str, Any], dict[str, Any]]
    if repair_ties:
        selected = repair_ties[-1]
    elif preferred_id:
        preferred_candidate = next((item for item in top_candidates if item[0] == preferred_id), None)
        selected = preferred_candidate if preferred_candidate else top_candidates[-1]
    else:
        selected = top_candidates[-1]

    checkpoint_id, checkpoint_payload, snapshot = selected
    cursor = int(snapshot.get("repair_cursor") or 0)
    validation_state = tuple(str(item) for item in snapshot.get("validation_state", ()) if isinstance(item, str))
    # snapshot_runtime_path is stored in the outer checkpoint JSON (not in the nested snapshot dict).
    raw_srp = checkpoint_payload.get("snapshot_runtime_path")
    snapshot_runtime_path = str(raw_srp) if isinstance(raw_srp, str) and raw_srp else None
    return checkpoint_id, max(0, cursor), validation_state, snapshot_runtime_path
    return None, 0, (), None


def _planned_heartbeats(budget: LongHandoffBudget) -> tuple[SupervisorHeartbeat, ...]:
    last_elapsed = min(budget.max_runtime_minutes, budget.heartbeat_minutes * budget.max_heartbeats)
    elapsed_values = tuple(range(budget.heartbeat_minutes, last_elapsed + 1, budget.heartbeat_minutes))
    if not elapsed_values:
        elapsed_values = (budget.max_runtime_minutes,)
    return tuple(
        SupervisorHeartbeat(
            elapsed,
            "checkpoint-execute.md" if elapsed < budget.max_runtime_minutes else "checkpoint-review.md",
            f"Supervisor heartbeat at minute {elapsed}: validation and checkpoint trail remain replayable.",
        )
        for elapsed in elapsed_values
    )


def _escalation_flags(result: HeadlessRunResult, budget: LongHandoffBudget) -> tuple[str, ...]:
    flags: list[str] = []
    if budget.max_runtime_minutes <= budget.heartbeat_minutes:
        flags.append("tight_runtime_budget")
    if not result.validation.passed:
        flags.append("validation_failed")
    if not result.effective_changed_files:
        flags.append("no_effective_mutation")
    if budget.pause_after_minutes is not None and budget.pause_after_minutes <= budget.max_runtime_minutes:
        flags.append("pause_requested")
    return tuple(flags)


def _supervisor_report(
    budget: LongHandoffBudget,
    heartbeats: tuple[SupervisorHeartbeat, ...],
    escalation_flags: tuple[str, ...],
    resume_token: str | None,
) -> str:
    lines = [
        "# Long Handoff Supervisor",
        "",
        f"max_runtime_minutes={budget.max_runtime_minutes}",
        f"heartbeat_minutes={budget.heartbeat_minutes}",
        f"max_heartbeats={budget.max_heartbeats}",
        f"token_budget={budget.token_budget}",
        f"pause_after_minutes={budget.pause_after_minutes or ''}",
        f"resume_token={resume_token or ''}",
        "",
        "## Heartbeats",
    ]
    lines.extend(f"- minute {item.elapsed_minutes}: {item.checkpoint_ref}" for item in heartbeats)
    lines.extend(["", "## Escalation Flags"])
    lines.extend(f"- {flag}" for flag in escalation_flags) if escalation_flags else lines.append("- none")
    return "\n".join(lines) + "\n"


def _parse_resume_minute(resume_token: str | None) -> int | None:
    if not resume_token or ":minute-" not in resume_token:
        return None
    try:
        return int(resume_token.rsplit(":minute-", 1)[1])
    except ValueError:
        return None


def _memory_writeback_handles_from_result(result: HeadlessRunResult) -> tuple[str, ...]:
    handles: list[str] = []
    for trace in result.memory_traces:
        if trace.evidence_handle:
            handles.append(trace.evidence_handle)
    for nemo_result in result.nemo_results:
        handle = nemo_result.payload.get("handle")
        if isinstance(handle, str) and handle:
            handles.append(handle)
    return tuple(dict.fromkeys(handles))


def _validation_commands_from_payload(payload: dict[str, Any]) -> tuple[str, ...]:
    validation = payload.get("validation", {}) if isinstance(payload.get("validation"), dict) else {}
    results = validation.get("results", []) if isinstance(validation, dict) else []
    commands: list[str] = []
    for result in results:
        command = result.get("command", {}) if isinstance(result, dict) and isinstance(result.get("command"), dict) else {}
        value = command.get("command")
        if isinstance(value, str) and value:
            commands.append(value)
    return tuple(commands)


def _continuation_state_from_result(
    result: HeadlessRunResult,
    budget: LongHandoffBudget,
    heartbeats: tuple[SupervisorHeartbeat, ...],
    escalation_flags: tuple[str, ...],
    resume_token: str | None,
) -> dict[str, object]:
    mutation = result.effective_mutation_result
    return {
        "schema_version": 1,
        "task_id": result.task.id,
        "run_id": result.run.id,
        "objective": result.task.objective,
        "repo_path": result.task.repo_path,
        "provider_mode": mutation.provider_mode if mutation else None,
        "provider": mutation.provider if mutation else None,
        "model_profile": result.run.model_profile,
        "validation_policy": result.run.validation_profile,
        "validation_commands": [item.command.command for item in result.validation.results],
        "checkpoint_refs": ["checkpoint-plan.md", "checkpoint-execute.md", "checkpoint-review.md"],
        "heartbeat_refs": [heartbeat.checkpoint_ref for heartbeat in heartbeats],
        "memory_writeback_handles": list(_memory_writeback_handles_from_result(result)),
        "budget": {
            "max_runtime_minutes": budget.max_runtime_minutes,
            "heartbeat_minutes": budget.heartbeat_minutes,
            "max_heartbeats": budget.max_heartbeats,
            "token_budget": budget.token_budget,
            "pause_after_minutes": budget.pause_after_minutes,
        },
        "escalation_flags": list(escalation_flags),
        "resume_token": resume_token,
        "resume_minute": _parse_resume_minute(resume_token),
        "execution_snapshot_ids": list((result.execution_snapshots or {}).keys()),
        "latest_atomic_checkpoint": next(
            (
                checkpoint_id
                for checkpoint_id, checkpoint_payload in reversed(list((result.execution_snapshots or {}).items()))
                if isinstance(checkpoint_payload, dict)
                and isinstance(checkpoint_payload.get("snapshot"), dict)
                and checkpoint_payload["snapshot"].get("resume_mode") == "atomic"
            ),
            None,
        ),
    }


def _load_continuation_state(payload: dict[str, Any]) -> dict[str, Any]:
    run = payload.get("run", {}) if isinstance(payload.get("run"), dict) else {}
    sandbox_path = run.get("sandbox_path")
    state_path = Path(sandbox_path) / "continuation-state.json" if sandbox_path else None
    if not state_path or not state_path.exists():
        return {}
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return state if isinstance(state, dict) else {}


def build_long_handoff_resume_plan(payload: dict[str, Any]) -> LongHandoffResumePlan:
    task = payload.get("task", {}) if isinstance(payload.get("task"), dict) else {}
    run = payload.get("run", {}) if isinstance(payload.get("run"), dict) else {}
    mutation = payload.get("mutation_result", {}) if isinstance(payload.get("mutation_result"), dict) else {}
    state = _load_continuation_state(payload)
    timeline = payload.get("timeline", [])
    artifacts = payload.get("artifacts", [])
    task_id = task.get("id")
    run_id = run.get("id")
    sandbox_path = run.get("sandbox_path")
    paused = any(isinstance(event, dict) and event.get("kind") == EventKind.PAUSED.value for event in timeline)
    has_resume_artifact = any(isinstance(artifact, dict) and artifact.get("path") == "resume-token.txt" for artifact in artifacts)
    checkpoint_refs = tuple(
        event.get("payload_ref")
        for event in timeline
        if isinstance(event, dict) and event.get("kind") in {EventKind.CHECKPOINT.value, EventKind.HEARTBEAT.value} and event.get("payload_ref")
    )
    reasons: list[str] = []
    if not paused:
        reasons.append("run_not_paused")
    if not has_resume_artifact:
        reasons.append("missing_resume_artifact")
    resume_token_path = Path(sandbox_path) / "resume-token.txt" if sandbox_path else None
    resume_token = resume_token_path.read_text(encoding="utf-8").strip() if resume_token_path and resume_token_path.exists() else None
    if not resume_token:
        reasons.append("missing_resume_token_file")
    resume_minute = _parse_resume_minute(resume_token)
    latest_atomic_checkpoint, repair_cursor, resume_validation_state, snapshot_runtime_path = _extract_atomic_resume_state(payload, state)
    if resume_token and resume_minute is None:
        reasons.append("invalid_resume_token")
    can_resume = not reasons
    next_action = (
        f"Resume supervised handoff from minute {resume_minute} using checkpoint {latest_atomic_checkpoint or 'phase-boundary'} at repair_cursor={repair_cursor}."
        if can_resume
        else "Cannot resume until the paused run has a valid resume token and checkpoint trail."
    )
    validation_commands = tuple(str(item) for item in state.get("validation_commands", ()) if isinstance(item, str)) or _validation_commands_from_payload(payload)
    handles = tuple(str(item) for item in state.get("memory_writeback_handles", ()) if isinstance(item, str))
    return LongHandoffResumePlan(
        can_resume,
        task_id,
        run_id,
        str(state.get("objective") or task.get("objective") or "") or None,
        str(state.get("provider_mode") or mutation.get("provider_mode") or "") or None,
        str(state.get("validation_policy") or run.get("validation_profile") or "") or None,
        validation_commands,
        handles,
        resume_token,
        resume_minute,
        checkpoint_refs,
        latest_atomic_checkpoint,
        repair_cursor,
        resume_validation_state,
        snapshot_runtime_path,
        next_action,
        tuple(reasons),
    )


def _continuation_link_markdown(plan: LongHandoffResumePlan, source_run_id: str | None) -> str:
    lines = [
        "# Long Handoff Continuation",
        "",
        f"source_task_id={plan.task_id or ''}",
        f"source_run_id={source_run_id or plan.run_id or ''}",
        f"resume_token={plan.resume_token or ''}",
        f"resume_minute={plan.resume_minute or ''}",
        f"latest_atomic_checkpoint={plan.latest_atomic_checkpoint or ''}",
        f"repair_cursor={plan.repair_cursor}",
        f"snapshot_runtime_path={plan.snapshot_runtime_path or ''}",
        "",
        "## Checkpoint Refs",
    ]
    lines.extend(f"- {checkpoint}" for checkpoint in plan.checkpoint_refs)
    return "\n".join(lines) + "\n"


def _continuation_memory_summary(plan: LongHandoffResumePlan, continuation: HeadlessRunResult) -> str:
    return (
        "Long handoff continuation linked "
        f"source_task={plan.task_id or ''} source_run={plan.run_id or ''} "
        f"continuation_task={continuation.task.id} continuation_run={continuation.run.id} "
        f"resume_token={plan.resume_token or ''} resume_minute={plan.resume_minute or ''}"
    )


def execute_long_handoff_continuation(
    payload: dict[str, Any],
    *,
    objective: str | None = None,
    acceptance_criteria: tuple[str, ...] | None = None,
    validation_commands: tuple[str, ...] | None = None,
    budget: LongHandoffBudget | None = None,
    **handoff_kwargs: object,
) -> HeadlessRunResult:
    continuation_kwargs: dict[str, object] = dict(handoff_kwargs)
    plan = build_long_handoff_resume_plan(payload)
    if not plan.can_resume:
        raise ValueError(f"cannot continue long handoff: {', '.join(plan.reasons)}")
    task = payload.get("task", {}) if isinstance(payload.get("task"), dict) else {}
    run = payload.get("run", {}) if isinstance(payload.get("run"), dict) else {}
    repo_path = str(task.get("repo_path") or ".")
    source_objective = str(plan.objective or task.get("objective") or "Resume paused long handoff")
    resume_objective = objective or f"Resume paused long handoff from {plan.resume_token}. Continue objective: {source_objective}"
    resume_task_id = f"{plan.task_id or 'task'}-resume"
    resume_run_id = f"{plan.run_id or 'run'}-resume-{plan.resume_minute}"
    continuation_kwargs.setdefault("provider_mode", plan.provider_mode or "fake")
    result = execute_long_handoff_supervisor(
        HandoffRequest(
            resume_objective,
            repo_path,
            acceptance_criteria or ("continuation remains replayable",),
            validation_commands or plan.validation_commands or ("python -m unittest",),
        ),
        budget=budget or LongHandoffBudget(max_runtime_minutes=30, heartbeat_minutes=10, max_heartbeats=1, token_budget=8000),
        task_id=resume_task_id,
        run_id=resume_run_id,
        resume_checkpoint_id=plan.latest_atomic_checkpoint,
        resume_repair_cursor=plan.repair_cursor,
        resume_validation_state=plan.resume_validation_state,
        resume_snapshot_runtime_path=plan.snapshot_runtime_path,
        resume_mode="atomic" if plan.latest_atomic_checkpoint else "phase_boundary",
        **continuation_kwargs,
    )
    link = _continuation_link_markdown(plan, run.get("id") if isinstance(run, dict) else None)
    runtime_path = Path(result.run.sandbox_path)
    runtime = WorktreeRuntimeSpec(result.run.runtime_id, result.task.id, result.run.id, runtime_path.parent, runtime_path, result.run.state)
    write_runtime_file(runtime, "continuation-link.md", link)
    memory_summary = _continuation_memory_summary(plan, result)
    write_runtime_file(runtime, "continuation-memory.md", memory_summary + "\n")
    sequence = len(result.timeline.events) + 1
    timeline = result.timeline.append(
        RunEvent(
            f"event-{sequence}",
            result.run.id,
            sequence,
            result.run.phase,
            EventKind.RESUMED,
            f"Continuation linked to resume token {plan.resume_token}.",
            "continuation-link.md",
        )
    )
    artifacts = result.artifacts + (
        Artifact.from_content("artifact-continuation-link", result.run.id, ArtifactType.SUPERVISOR, "continuation-link.md", "Long handoff continuation link", link),
        Artifact.from_content("artifact-continuation-memory", result.run.id, ArtifactType.MEMORY_SUMMARY, "continuation-memory.md", "Long handoff continuation memory link", memory_summary),
    )
    memory_adapter = continuation_kwargs.get("nemo_adapter")
    active_memory_adapter = memory_adapter if isinstance(memory_adapter, (InMemoryNemoAdapter, PersistentNemoAdapter)) else InMemoryNemoAdapter()
    _, memory_result = active_memory_adapter.call(NemoLifecyclePhase.REVIEW, "store_conversation", summary=memory_summary)
    memory_traces = result.memory_traces + (
        MemoryTrace("mem-continuation-link", result.run.id, "store_conversation", "conversation", "memory_write", memory_summary),
    )
    return HeadlessRunResult(
        task=result.task,
        run=result.run,
        timeline=timeline,
        artifacts=artifacts,
        memory_traces=memory_traces,
        validation=result.validation,
        review_package=result.review_package,
        nemo_results=result.nemo_results + (memory_result,),
        repair_plan=result.repair_plan,
        execution_snapshots=result.execution_snapshots,
        platform_info=result.platform_info,
        portfolio=result.portfolio,
        mutation_result=result.mutation_result,
        repair_result=result.repair_result,
        runtime_files=snapshot_runtime_files(runtime),
    )


def execute_long_handoff_supervisor(
    request: HandoffRequest,
    *,
    budget: LongHandoffBudget | None = None,
    **handoff_kwargs: object,
) -> HeadlessRunResult:
    active_budget = budget or LongHandoffBudget()
    active_budget.validate()
    active_handoff_kwargs: dict[str, object] = dict(handoff_kwargs)
    active_handoff_kwargs.setdefault("provider_mode", "fake")
    emit_event("heartbeat", "Supervisor: starting headless handoff", "execute", {"iteration": 1})
    result = execute_headless_handoff(request, bounded_simulation=True, **active_handoff_kwargs)
    emit_event("heartbeat", "Supervisor: headless handoff complete", "execute", {"iteration": 1})
    heartbeats = _planned_heartbeats(active_budget)
    escalation_flags = _escalation_flags(result, active_budget)
    resume_token = (
        f"{result.task.id}:{result.run.id}:minute-{active_budget.pause_after_minutes}"
        if active_budget.pause_after_minutes is not None and active_budget.pause_after_minutes <= active_budget.max_runtime_minutes
        else None
    )
    report = _supervisor_report(active_budget, heartbeats, escalation_flags, resume_token)
    runtime_path = Path(result.run.sandbox_path)
    runtime = WorktreeRuntimeSpec(result.run.runtime_id, result.task.id, result.run.id, runtime_path.parent, runtime_path, result.run.state)
    continuation_state = _continuation_state_from_result(result, active_budget, heartbeats, escalation_flags, resume_token)
    write_runtime_file(runtime, "supervisor-report.md", report)
    write_runtime_file(runtime, "continuation-state.json", json.dumps(continuation_state, indent=2, sort_keys=True) + "\n")
    if resume_token:
        write_runtime_file(runtime, "resume-token.txt", resume_token + "\n")

    timeline = result.timeline
    next_sequence = len(timeline.events) + 1
    for index, heartbeat in enumerate(heartbeats, start=0):
        timeline = timeline.append(
            RunEvent(
                f"event-{next_sequence + index}",
                result.run.id,
                next_sequence + index,
                result.run.phase,
                EventKind.HEARTBEAT,
                heartbeat.summary,
                heartbeat.checkpoint_ref,
            )
        )
    next_sequence = len(timeline.events) + 1
    if escalation_flags:
        timeline = timeline.append(
            RunEvent(
                f"event-{next_sequence}",
                result.run.id,
                next_sequence,
                result.run.phase,
                EventKind.ESCALATION,
                f"Supervisor escalation flags: {', '.join(escalation_flags)}.",
                "supervisor-report.md",
            )
        )
        next_sequence += 1
    if resume_token:
        timeline = timeline.append(
            RunEvent(
                f"event-{next_sequence}",
                result.run.id,
                next_sequence,
                result.run.phase,
                EventKind.PAUSED,
                "Supervisor pause point reached; resume token persisted.",
                "resume-token.txt",
            )
        )

    artifacts = result.artifacts + (
        Artifact.from_content("artifact-supervisor", result.run.id, ArtifactType.SUPERVISOR, "supervisor-report.md", "Long handoff supervisor budget and heartbeats", report),
        Artifact.from_content("artifact-continuation-state", result.run.id, ArtifactType.SUPERVISOR, "continuation-state.json", "Long handoff continuation state", json.dumps(continuation_state, sort_keys=True)),
    )
    if resume_token:
        artifacts = artifacts + (
            Artifact.from_content("artifact-resume-token", result.run.id, ArtifactType.SUPERVISOR, "resume-token.txt", "Long handoff resume token", resume_token),
        )
    runtime_files = snapshot_runtime_files(runtime)
    return HeadlessRunResult(
        task=result.task,
        run=result.run,
        timeline=timeline,
        artifacts=artifacts,
        memory_traces=result.memory_traces,
        validation=result.validation,
        review_package=result.review_package,
        nemo_results=result.nemo_results,
        repair_plan=result.repair_plan,
        execution_snapshots=result.execution_snapshots,
        platform_info=result.platform_info,
        portfolio=result.portfolio,
        mutation_result=result.mutation_result,
        repair_result=result.repair_result,
        runtime_files=runtime_files,
    )