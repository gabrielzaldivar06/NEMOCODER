from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ExecutionPhase(StrEnum):
    PLAN = "plan"
    EXECUTE = "execute"
    REVIEW = "review"


class PortfolioMode(StrEnum):
    SAFE = "safe"
    FAST = "fast"
    THOROUGH = "thorough"


class ApprovalLevel(StrEnum):
    NONE = "none"
    SCOPED = "scoped"
    REQUIRED = "required"


class RuntimeState(StrEnum):
    INITIALIZING = "initializing"
    READY = "ready"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    BUILDING = "building"
    EXECUTING = "executing"
    REVIEWING = "reviewing"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class PhaseContract:
    phase: ExecutionPhase
    portfolio_mode: PortfolioMode
    read_only: bool
    mutation_allowed: bool
    approval_level: ApprovalLevel
    review_gate_required: bool


@dataclass(frozen=True, slots=True)
class SupervisionContext:
    """Tracks elapsed time, current phase, and budget during execution."""
    start_time: float
    phase: ExecutionPhase
    elapsed_seconds: float
    budget_max_runtime_minutes: int
    budget_heartbeat_minutes: int
    phase_start_time: float

    @property
    def elapsed_minutes(self) -> float:
        return self.elapsed_seconds / 60.0

    @property
    def phase_elapsed_seconds(self) -> float:
        import time
        return time.time() - self.phase_start_time


@dataclass(frozen=True, slots=True)
class HeartbeatSignal:
    """Emitted when heartbeat interval is reached during execution."""
    elapsed_minutes: float
    elapsed_seconds: float
    phase: ExecutionPhase
    checkpoint_path: str | None
    next_heartbeat_seconds: float
    summary: str = ""


@dataclass(frozen=True, slots=True)
class PauseSignal:
    """Emitted when pause point is triggered (timeout or explicit pause)."""
    reason: str
    resume_token: str
    checkpoint_path: str
    total_elapsed_minutes: float
    total_elapsed_seconds: float
    phase_when_paused: ExecutionPhase
    checkpoint_json: dict[str, Any]
