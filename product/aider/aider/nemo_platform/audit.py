from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum


class AuditEventType(StrEnum):
    PHASE_ENTERED = "phase_entered"
    TOOL_CALLED = "tool_called"
    PERMISSION_DECIDED = "permission_decided"
    TEST_RUN = "test_run"
    CHECKPOINT_CREATED = "checkpoint_created"
    REVIEW_PACKAGE_CREATED = "review_package_created"


class RiskFlag(StrEnum):
    STALE_MEMORY = "stale_memory"
    VALIDATION_FAILURE = "validation_failure"
    REPEATED_FAILURE = "repeated_failure"
    PERMISSION_PRESSURE = "permission_pressure"
    UNRESOLVED_RISK = "unresolved_risk"


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_id: str
    run_id: str
    sequence: int
    event_type: AuditEventType
    summary: str
    risk_flags: tuple[RiskFlag, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "event_type": self.event_type.value,
            "summary": self.summary,
            "risk_flags": [flag.value for flag in self.risk_flags],
        }


@dataclass(frozen=True, slots=True)
class AuditLog:
    events: tuple[AuditEvent, ...] = ()

    def append(self, event: AuditEvent) -> AuditLog:
        expected = len(self.events) + 1
        if event.sequence != expected:
            raise ValueError(f"audit event sequence must be {expected}, got {event.sequence}")
        return replace(self, events=self.events + (event,))