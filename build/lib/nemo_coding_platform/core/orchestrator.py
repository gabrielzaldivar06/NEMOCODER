from __future__ import annotations

from dataclasses import dataclass

from nemo_coding_platform.core.contracts import ApprovalLevel, ExecutionPhase, PhaseContract, PortfolioMode
from nemo_coding_platform.core.mutations import DryRunResult, MutationPlan, QualityMutationEngine


SUPERVISED_PHASES: tuple[PhaseContract, ...] = (
    PhaseContract(
        phase=ExecutionPhase.PLAN,
        portfolio_mode=PortfolioMode.SAFE,
        read_only=True,
        mutation_allowed=False,
        approval_level=ApprovalLevel.NONE,
        review_gate_required=False,
    ),
    PhaseContract(
        phase=ExecutionPhase.EXECUTE,
        portfolio_mode=PortfolioMode.FAST,
        read_only=False,
        mutation_allowed=True,
        approval_level=ApprovalLevel.SCOPED,
        review_gate_required=False,
    ),
    PhaseContract(
        phase=ExecutionPhase.REVIEW,
        portfolio_mode=PortfolioMode.THOROUGH,
        read_only=True,
        mutation_allowed=False,
        approval_level=ApprovalLevel.REQUIRED,
        review_gate_required=True,
    ),
)


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    name: str
    phases: tuple[PhaseContract, ...]
    subagents_enabled: bool
    max_subagent_depth: int
    nemo_required: bool


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    approved: bool
    summary: str


@dataclass(frozen=True, slots=True)
class WorkflowRunResult:
    dry_run: DryRunResult
    applied_files: tuple[str, ...]
    review: ReviewDecision


DEFAULT_WORKFLOW = WorkflowDefinition(
    name="supervised-local-first",
    phases=SUPERVISED_PHASES,
    subagents_enabled=True,
    max_subagent_depth=1,
    nemo_required=True,
)


def phase_contract(phase: ExecutionPhase) -> PhaseContract:
    for contract in DEFAULT_WORKFLOW.phases:
        if contract.phase == phase:
            return contract
    raise KeyError(phase)


class SupervisedWorkflowRunner:
    def __init__(self, mutation_engine: QualityMutationEngine, workflow: WorkflowDefinition = DEFAULT_WORKFLOW) -> None:
        self.mutation_engine = mutation_engine
        self.workflow = workflow

    def dry_run_execution(self, plan: MutationPlan) -> DryRunResult:
        execute_contract = phase_contract(ExecutionPhase.EXECUTE)
        if not execute_contract.mutation_allowed:
            raise RuntimeError("execute phase must allow scoped mutations")
        return self.mutation_engine.dry_run(plan)

    def execute_approved_plan(self, plan: MutationPlan, review: ReviewDecision) -> WorkflowRunResult:
        if not review.approved:
            raise PermissionError("review approval is required before applying a supervised workflow")
        if not plan.approved:
            raise PermissionError("execution approval is required before applying a supervised workflow")

        dry_run = self.mutation_engine.dry_run(plan)
        executable = MutationPlan(writes=plan.writes, approved=True, dry_run_completed=True)
        applied = self.mutation_engine.apply(executable)
        return WorkflowRunResult(dry_run=dry_run, applied_files=applied, review=review)
