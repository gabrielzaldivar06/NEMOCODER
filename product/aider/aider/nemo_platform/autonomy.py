from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class IsolationKind(StrEnum):
    NONE = "none"
    WORKTREE = "worktree"
    CONTAINER = "container"


class AutonomyLevel(StrEnum):
    MANUAL = "manual"
    ASSISTED = "assisted"
    AUTONOMOUS_SANDBOX = "autonomous_sandbox"
    BACKGROUND_AGENT = "background_agent"
    FULL_HANDOFF = "full_handoff"


@dataclass(frozen=True, slots=True)
class AutonomyContract:
    level: AutonomyLevel
    isolation: IsolationKind
    can_spawn_subagents: bool
    can_run_background: bool
    can_write_main_workspace_directly: bool
    requires_review_before_merge: bool
    can_continue_without_human_interaction: bool = False
    requires_spec_driven_development: bool = False
    requires_checkpoints: bool = False


AUTONOMY_CONTRACTS: dict[AutonomyLevel, AutonomyContract] = {
    AutonomyLevel.MANUAL: AutonomyContract(
        AutonomyLevel.MANUAL,
        IsolationKind.NONE,
        can_spawn_subagents=False,
        can_run_background=False,
        can_write_main_workspace_directly=False,
        requires_review_before_merge=True,
    ),
    AutonomyLevel.ASSISTED: AutonomyContract(
        AutonomyLevel.ASSISTED,
        IsolationKind.WORKTREE,
        can_spawn_subagents=True,
        can_run_background=False,
        can_write_main_workspace_directly=False,
        requires_review_before_merge=True,
    ),
    AutonomyLevel.AUTONOMOUS_SANDBOX: AutonomyContract(
        AutonomyLevel.AUTONOMOUS_SANDBOX,
        IsolationKind.WORKTREE,
        can_spawn_subagents=True,
        can_run_background=True,
        can_write_main_workspace_directly=False,
        requires_review_before_merge=True,
    ),
    AutonomyLevel.BACKGROUND_AGENT: AutonomyContract(
        AutonomyLevel.BACKGROUND_AGENT,
        IsolationKind.CONTAINER,
        can_spawn_subagents=True,
        can_run_background=True,
        can_write_main_workspace_directly=False,
        requires_review_before_merge=True,
        can_continue_without_human_interaction=True,
        requires_checkpoints=True,
    ),
    AutonomyLevel.FULL_HANDOFF: AutonomyContract(
        AutonomyLevel.FULL_HANDOFF,
        IsolationKind.CONTAINER,
        can_spawn_subagents=True,
        can_run_background=True,
        can_write_main_workspace_directly=False,
        requires_review_before_merge=True,
        can_continue_without_human_interaction=True,
        requires_spec_driven_development=True,
        requires_checkpoints=True,
    ),
}


def autonomy_contract(level: AutonomyLevel) -> AutonomyContract:
    return AUTONOMY_CONTRACTS[level]