from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


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
