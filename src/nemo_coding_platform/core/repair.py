from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class RepairBudget:
    max_attempts: int = 3

    def __post_init__(self) -> None:
        if self.max_attempts < 0:
            raise ValueError("repair budget cannot be negative")


@dataclass(frozen=True, slots=True)
class RepairAttempt:
    attempt: int
    reason: str
    action: str


@dataclass(frozen=True, slots=True)
class RepairPlan:
    budget: RepairBudget
    attempts: tuple[RepairAttempt, ...] = ()

    @property
    def exhausted(self) -> bool:
        return len(self.attempts) >= self.budget.max_attempts

    def can_record_attempt(self) -> bool:
        return not self.exhausted

    def next_attempt(self, reason: str, action: str) -> RepairPlan:
        if self.exhausted:
            raise RuntimeError("repair budget exhausted")
        attempt = RepairAttempt(len(self.attempts) + 1, reason, action)
        return replace(self, attempts=self.attempts + (attempt,))

    def summary(self) -> str:
        return f"repair attempts={len(self.attempts)}/{self.budget.max_attempts} exhausted={self.exhausted}"