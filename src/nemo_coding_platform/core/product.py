from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ProductSurface(StrEnum):
    DESKTOP = "desktop"
    CLI_HARNESS = "cli_harness"
    HEADLESS = "headless"


class DesktopShell(StrEnum):
    UNDECIDED = "undecided"
    TAURI = "tauri"
    ELECTRON = "electron"


class BackendProtocol(StrEnum):
    UNDECIDED = "undecided"
    HTTP_LOCAL = "http_local"
    JSON_RPC_LOCAL = "json_rpc_local"


class AutonomyLevel(StrEnum):
    MANUAL = "manual"
    ASSISTED = "assisted"
    AUTONOMOUS_SANDBOX = "autonomous_sandbox"
    BACKGROUND_AGENT = "background_agent"
    FULL_HANDOFF = "full_handoff"
    TEAM_CI = "team_ci"


@dataclass(frozen=True, slots=True)
class DesktopProductContract:
    target_surface: ProductSurface
    cli_role: ProductSurface
    shell: DesktopShell
    backend_protocol: BackendProtocol
    requires_repo_picker: bool
    requires_task_workspace: bool
    requires_approval_queue: bool
    requires_artifact_timeline: bool
    requires_nemo_memory_trace: bool
    requires_model_settings: bool


@dataclass(frozen=True, slots=True)
class AutonomyContract:
    level: AutonomyLevel
    requires_isolation: bool
    can_run_background: bool
    can_spawn_subagents: bool
    can_write_main_workspace_directly: bool
    requires_review_before_merge: bool
    can_continue_without_human_interaction: bool = False
    requires_spec_driven_development: bool = False
    requires_checkpoints: bool = False


DESKTOP_PRODUCT = DesktopProductContract(
    target_surface=ProductSurface.DESKTOP,
    cli_role=ProductSurface.CLI_HARNESS,
    shell=DesktopShell.TAURI,
    backend_protocol=BackendProtocol.HTTP_LOCAL,
    requires_repo_picker=True,
    requires_task_workspace=True,
    requires_approval_queue=True,
    requires_artifact_timeline=True,
    requires_nemo_memory_trace=True,
    requires_model_settings=True,
)


AUTONOMY_CONTRACTS: dict[AutonomyLevel, AutonomyContract] = {
    AutonomyLevel.MANUAL: AutonomyContract(
        level=AutonomyLevel.MANUAL,
        requires_isolation=False,
        can_run_background=False,
        can_spawn_subagents=False,
        can_write_main_workspace_directly=False,
        requires_review_before_merge=True,
    ),
    AutonomyLevel.ASSISTED: AutonomyContract(
        level=AutonomyLevel.ASSISTED,
        requires_isolation=False,
        can_run_background=False,
        can_spawn_subagents=True,
        can_write_main_workspace_directly=False,
        requires_review_before_merge=True,
    ),
    AutonomyLevel.AUTONOMOUS_SANDBOX: AutonomyContract(
        level=AutonomyLevel.AUTONOMOUS_SANDBOX,
        requires_isolation=True,
        can_run_background=True,
        can_spawn_subagents=True,
        can_write_main_workspace_directly=False,
        requires_review_before_merge=True,
    ),
    AutonomyLevel.BACKGROUND_AGENT: AutonomyContract(
        level=AutonomyLevel.BACKGROUND_AGENT,
        requires_isolation=True,
        can_run_background=True,
        can_spawn_subagents=True,
        can_write_main_workspace_directly=False,
        requires_review_before_merge=True,
        can_continue_without_human_interaction=True,
        requires_checkpoints=True,
    ),
    AutonomyLevel.FULL_HANDOFF: AutonomyContract(
        level=AutonomyLevel.FULL_HANDOFF,
        requires_isolation=True,
        can_run_background=True,
        can_spawn_subagents=True,
        can_write_main_workspace_directly=False,
        requires_review_before_merge=True,
        can_continue_without_human_interaction=True,
        requires_spec_driven_development=True,
        requires_checkpoints=True,
    ),
    AutonomyLevel.TEAM_CI: AutonomyContract(
        level=AutonomyLevel.TEAM_CI,
        requires_isolation=True,
        can_run_background=True,
        can_spawn_subagents=True,
        can_write_main_workspace_directly=False,
        requires_review_before_merge=True,
    ),
}


def autonomy_contract(level: AutonomyLevel) -> AutonomyContract:
    return AUTONOMY_CONTRACTS[level]