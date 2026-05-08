from __future__ import annotations

from enum import StrEnum


class AgentPhase(StrEnum):
    PLAN = "plan"
    BUILD = "build"
    REVIEW = "review"
    BACKGROUND = "background"


def phase_from_execution_phase(value: str) -> AgentPhase:
    if value == "execute":
        return AgentPhase.BUILD
    return AgentPhase(value)