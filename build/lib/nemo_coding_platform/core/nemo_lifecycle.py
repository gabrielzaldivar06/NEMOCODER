from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from nemo_coding_platform.core.contracts import ExecutionPhase
from nemo_coding_platform.core.memory import NEMO_TOOL_REGISTRY, NemoToolRisk


class NemoLifecyclePhase(StrEnum):
    START = "start"
    PLAN = "plan"
    BUILD = "build"
    REVIEW = "review"
    CLOSE = "close"


@dataclass(frozen=True, slots=True)
class NemoLifecycleContract:
    phase: NemoLifecyclePhase
    tool_names: tuple[str, ...]
    memory_write_allowed: bool
    scheduling_allowed: bool
    checkpoint_writeback: bool = False


def _tools_for_execution_phase(phase: ExecutionPhase) -> tuple[str, ...]:
    return tuple(tool.name for tool in NEMO_TOOL_REGISTRY if phase in tool.phase_access)


NEMO_LIFECYCLE: dict[NemoLifecyclePhase, NemoLifecycleContract] = {
    NemoLifecyclePhase.START: NemoLifecycleContract(
        NemoLifecyclePhase.START,
        ("prime_context", "context_bootstrap"),
        memory_write_allowed=False,
        scheduling_allowed=False,
    ),
    NemoLifecyclePhase.PLAN: NemoLifecycleContract(
        NemoLifecyclePhase.PLAN,
        _tools_for_execution_phase(ExecutionPhase.PLAN),
        memory_write_allowed=True,
        scheduling_allowed=False,
    ),
    NemoLifecyclePhase.BUILD: NemoLifecycleContract(
        NemoLifecyclePhase.BUILD,
        _tools_for_execution_phase(ExecutionPhase.EXECUTE),
        memory_write_allowed=True,
        scheduling_allowed=False,
        checkpoint_writeback=True,
    ),
    NemoLifecyclePhase.REVIEW: NemoLifecycleContract(
        NemoLifecyclePhase.REVIEW,
        _tools_for_execution_phase(ExecutionPhase.REVIEW),
        memory_write_allowed=True,
        scheduling_allowed=True,
    ),
    NemoLifecyclePhase.CLOSE: NemoLifecycleContract(
        NemoLifecyclePhase.CLOSE,
        ("store_conversation", "record_context_feedback", "update_memory"),
        memory_write_allowed=True,
        scheduling_allowed=True,
    ),
}


def lifecycle_contract(phase: NemoLifecyclePhase) -> NemoLifecycleContract:
    return NEMO_LIFECYCLE[phase]


def tool_allowed_in_lifecycle(phase: NemoLifecyclePhase, tool_name: str) -> bool:
    return tool_name in NEMO_LIFECYCLE[phase].tool_names


def scheduling_tools_are_review_or_close_gated() -> bool:
    scheduling = {tool.name for tool in NEMO_TOOL_REGISTRY if tool.risk == NemoToolRisk.SCHEDULING_WRITE}
    blocked_phases = (NemoLifecyclePhase.START, NemoLifecyclePhase.PLAN, NemoLifecyclePhase.BUILD)
    return all(scheduling.isdisjoint(NEMO_LIFECYCLE[phase].tool_names) for phase in blocked_phases)


def nemo_is_not_portfolio_only() -> bool:
    names = {tool.name for tool in NEMO_TOOL_REGISTRY}
    return {"prime_context", "create_correction", "store_conversation"}.issubset(names)