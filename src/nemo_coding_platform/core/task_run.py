from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import sha256

from nemo_coding_platform.core.contracts import ExecutionPhase, RuntimeState
from nemo_coding_platform.core.product import AutonomyLevel


class TaskStatus(StrEnum):
    OPEN = "open"
    RUNNING = "running"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    FAILED = "failed"


class EventKind(StrEnum):
    CONTEXT_BOOTSTRAPPED = "context_bootstrapped"
    PLAN_CREATED = "plan_created"
    PERMISSION_DECIDED = "permission_decided"
    MUTATION_CREATED = "mutation_created"
    VALIDATION_RUN = "validation_run"
    CHECKPOINT = "checkpoint"
    REVIEW_PACKAGE_CREATED = "review_package_created"
    MEMORY_WRITTEN = "memory_written"
    HEARTBEAT = "heartbeat"
    ESCALATION = "escalation"
    PAUSED = "paused"
    RESUMED = "resumed"


class ArtifactType(StrEnum):
    SPEC = "spec"
    TEST = "test"
    PATCH = "patch"
    VALIDATION = "validation"
    CHECKPOINT = "checkpoint"
    REVIEW_PACKAGE = "review_package"
    MEMORY_SUMMARY = "memory_summary"
    SUPERVISOR = "supervisor"


@dataclass(frozen=True, slots=True)
class Task:
    id: str
    repo_path: str
    title: str
    objective: str
    autonomy_level: AutonomyLevel
    status: TaskStatus = TaskStatus.OPEN
    linked_prd: str | None = None
    linked_specs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Run:
    id: str
    task_id: str
    state: RuntimeState
    phase: ExecutionPhase
    runtime_id: str
    sandbox_path: str
    model_profile: str
    permission_profile: str
    validation_profile: str
    failure_reason: str | None = None


@dataclass(frozen=True, slots=True)
class RunEvent:
    id: str
    run_id: str
    sequence: int
    phase: ExecutionPhase
    kind: EventKind
    summary: str
    payload_ref: str | None = None


@dataclass(frozen=True, slots=True)
class Artifact:
    id: str
    run_id: str
    artifact_type: ArtifactType
    path: str
    summary: str
    content_hash: str

    @classmethod
    def from_content(
        cls,
        id: str,
        run_id: str,
        artifact_type: ArtifactType,
        path: str,
        summary: str,
        content: str,
    ) -> Artifact:
        return cls(id, run_id, artifact_type, path, summary, sha256(content.encode("utf-8")).hexdigest())


@dataclass(frozen=True, slots=True)
class MemoryTrace:
    id: str
    run_id: str
    nemo_tool: str
    suite: str
    risk: str
    summary: str
    evidence_handle: str | None = None
    memory_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AppendOnlyTimeline:
    events: tuple[RunEvent, ...] = ()

    def append(self, event: RunEvent) -> AppendOnlyTimeline:
        expected = len(self.events) + 1
        if event.sequence != expected:
            raise ValueError(f"timeline event sequence must be {expected}, got {event.sequence}")
        if self.events and event.run_id != self.events[-1].run_id:
            raise ValueError("timeline cannot mix run ids")
        return replace(self, events=self.events + (event,))

    def has_event_kind(self, kind: EventKind) -> bool:
        return any(event.kind == kind for event in self.events)


class HandoffCompletionGuard:
    def validate(self, run: Run, timeline: AppendOnlyTimeline, artifacts: tuple[Artifact, ...]) -> None:
        if run.state != RuntimeState.REVIEWING:
            raise ValueError("full handoff can only request completion from review state")
        if not timeline.has_event_kind(EventKind.CHECKPOINT):
            raise ValueError("full handoff completion requires at least one checkpoint")
        if not any(artifact.artifact_type == ArtifactType.REVIEW_PACKAGE for artifact in artifacts):
            raise ValueError("full handoff completion requires a final review package")