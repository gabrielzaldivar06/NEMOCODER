from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum


class RuntimeState(StrEnum):
    INITIALIZING = "initializing"
    READY = "ready"
    PLANNING = "planning"
    BUILDING = "building"
    REVIEWING = "reviewing"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AgentRuntimeSession:
    session_id: str
    state: RuntimeState = RuntimeState.INITIALIZING


class RuntimeStateMachine:
    allowed: dict[RuntimeState, set[RuntimeState]] = {
        RuntimeState.INITIALIZING: {RuntimeState.READY, RuntimeState.FAILED},
        RuntimeState.READY: {RuntimeState.PLANNING, RuntimeState.PAUSED, RuntimeState.FAILED},
        RuntimeState.PLANNING: {RuntimeState.BUILDING, RuntimeState.PAUSED, RuntimeState.FAILED},
        RuntimeState.BUILDING: {RuntimeState.REVIEWING, RuntimeState.PAUSED, RuntimeState.FAILED},
        RuntimeState.REVIEWING: {RuntimeState.COMPLETED, RuntimeState.PAUSED, RuntimeState.FAILED},
        RuntimeState.PAUSED: {
            RuntimeState.READY,
            RuntimeState.PLANNING,
            RuntimeState.BUILDING,
            RuntimeState.REVIEWING,
            RuntimeState.FAILED,
        },
        RuntimeState.COMPLETED: set(),
        RuntimeState.FAILED: set(),
    }

    def transition(self, session: AgentRuntimeSession, state: RuntimeState) -> AgentRuntimeSession:
        if state not in self.allowed[session.state]:
            raise ValueError(f"invalid transition: {session.state} -> {state}")
        return replace(session, state=state)