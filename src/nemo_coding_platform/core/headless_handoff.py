from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class HandoffStepKind(StrEnum):
    BOOTSTRAP_NEMO = "bootstrap_nemo"
    GENERATE_SPECS = "generate_specs"
    GENERATE_TESTS = "generate_tests"
    PLAN_IMPLEMENTATION = "plan_implementation"
    CREATE_SANDBOX = "create_sandbox"
    IMPLEMENT_WITH_ENGINE = "implement_with_engine"
    IMPLEMENT_WITH_AIDER = IMPLEMENT_WITH_ENGINE
    RUN_VALIDATION = "run_validation"
    REPAIR_FAILURES = "repair_failures"
    CREATE_CHECKPOINT = "create_checkpoint"
    CREATE_REVIEW_PACKAGE = "create_review_package"
    WRITE_NEMO_MEMORY = "write_nemo_memory"


@dataclass(frozen=True, slots=True)
class HandoffRequest:
    prd: str
    repo_path: str
    acceptance_criteria: tuple[str, ...]
    validation_commands: tuple[str, ...]
    checkpoint_minutes: int = 15
    repair_budget: int = 3
    objective_summary: str | None = None
    linked_prd: str | None = None
    spec_mode: str = "auto"


@dataclass(frozen=True, slots=True)
class HandoffStep:
    kind: HandoffStepKind
    summary: str
    requires_human_prompt: bool = False


@dataclass(frozen=True, slots=True)
class HandoffPlan:
    request: HandoffRequest
    steps: tuple[HandoffStep, ...]
    review_gate_required: bool = True
    can_run_unattended: bool = True

    def step_kinds(self) -> tuple[HandoffStepKind, ...]:
        return tuple(step.kind for step in self.steps)


def build_handoff_plan(request: HandoffRequest) -> HandoffPlan:
    steps = (
        HandoffStep(HandoffStepKind.BOOTSTRAP_NEMO, "Load compact NEMO context."),
        HandoffStep(HandoffStepKind.GENERATE_SPECS, "Convert PRD into executable specs."),
        HandoffStep(HandoffStepKind.GENERATE_TESTS, "Create tests or contract checks before code."),
        HandoffStep(HandoffStepKind.PLAN_IMPLEMENTATION, "Plan implementation slices."),
        HandoffStep(HandoffStepKind.CREATE_SANDBOX, "Create isolated worktree/container runtime."),
        HandoffStep(HandoffStepKind.IMPLEMENT_WITH_ENGINE, "Apply code through the NEMO CODE quality engine."),
        HandoffStep(HandoffStepKind.RUN_VALIDATION, "Run configured validation commands."),
        HandoffStep(HandoffStepKind.REPAIR_FAILURES, "Repair validation failures within budget."),
        HandoffStep(HandoffStepKind.CREATE_CHECKPOINT, "Emit checkpoint for unattended run."),
        HandoffStep(HandoffStepKind.CREATE_REVIEW_PACKAGE, "Assemble diff, validation, risks, and memory summary."),
        HandoffStep(HandoffStepKind.WRITE_NEMO_MEMORY, "Persist durable NEMO outcomes."),
    )
    return HandoffPlan(request=request, steps=steps)


def validate_handoff_request(request: HandoffRequest) -> None:
    if not request.prd.strip():
        raise ValueError("Full Handoff requires a PRD or objective")
    if not request.acceptance_criteria:
        raise ValueError("Full Handoff requires acceptance criteria")
    if not request.validation_commands:
        raise ValueError("Full Handoff requires validation commands")
    if request.checkpoint_minutes <= 0:
        raise ValueError("checkpoint cadence must be positive")
    if request.spec_mode not in {"auto", "sdd"}:
        raise ValueError("spec_mode must be 'auto' or 'sdd'")