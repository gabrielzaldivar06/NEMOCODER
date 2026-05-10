"""Tool contracts: shared types for tool inputs, outputs, errors, and metadata."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ToolErrorCode(str, Enum):
    NOT_CONFIGURED = "not_configured"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    PERMISSION_DENIED = "permission_denied"
    INVALID_INPUT = "invalid_input"
    TOOL_UNAVAILABLE = "tool_unavailable"


@dataclass(frozen=True)
class ToolOutput:
    ok: bool
    data: Any = None
    error_code: ToolErrorCode | None = None
    error_message: str | None = None

    @classmethod
    def success(cls, data: Any = None) -> "ToolOutput":
        return cls(ok=True, data=data)

    @classmethod
    def failure(
        cls, code: ToolErrorCode, message: str, data: Any = None
    ) -> "ToolOutput":
        return cls(ok=False, data=data, error_code=code, error_message=message)

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"ok": self.ok}
        if self.data is not None:
            result["data"] = self.data
        if self.error_code is not None:
            result["error_code"] = self.error_code.value
        if self.error_message is not None:
            result["error_message"] = self.error_message
        return result


@dataclass(frozen=True)
class ToolMeta:
    slug: str
    description: str
    is_interactive: bool = False
    phases_allowed: tuple[str, ...] = field(default_factory=tuple)
    risk_level: str = "low"  # low | medium | high

    def allows_phase(self, phase: str) -> bool:
        if not self.phases_allowed:
            return True
        return phase in self.phases_allowed
