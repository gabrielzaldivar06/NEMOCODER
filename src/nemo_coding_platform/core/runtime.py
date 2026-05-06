from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from nemo_coding_platform.core.contracts import RuntimeState


class SandboxKind(StrEnum):
    PROCESS = "process"
    WORKTREE = "worktree"
    CONTAINER = "container"


@dataclass(frozen=True, slots=True)
class RuntimeSession:
    session_id: str
    sandbox: SandboxKind
    state: RuntimeState = RuntimeState.INITIALIZING


class RuntimeController:
    _allowed: dict[RuntimeState, set[RuntimeState]] = {
        RuntimeState.INITIALIZING: {RuntimeState.READY, RuntimeState.FAILED},
        RuntimeState.READY: {RuntimeState.PLANNING, RuntimeState.PAUSED, RuntimeState.FAILED},
        RuntimeState.PLANNING: {RuntimeState.AWAITING_APPROVAL, RuntimeState.FAILED},
        RuntimeState.AWAITING_APPROVAL: {RuntimeState.EXECUTING, RuntimeState.PAUSED, RuntimeState.FAILED},
        RuntimeState.EXECUTING: {RuntimeState.REVIEWING, RuntimeState.PAUSED, RuntimeState.FAILED},
        RuntimeState.REVIEWING: {RuntimeState.COMPLETED, RuntimeState.PAUSED, RuntimeState.FAILED},
        RuntimeState.PAUSED: {RuntimeState.READY, RuntimeState.PLANNING, RuntimeState.AWAITING_APPROVAL, RuntimeState.EXECUTING, RuntimeState.REVIEWING, RuntimeState.FAILED},
        RuntimeState.COMPLETED: set(),
        RuntimeState.FAILED: set(),
    }

    def transition(self, session: RuntimeSession, new_state: RuntimeState) -> RuntimeSession:
        allowed = self._allowed[session.state]
        if new_state not in allowed:
            raise ValueError(f"invalid runtime transition: {session.state} -> {new_state}")
        return replace(session, state=new_state)
