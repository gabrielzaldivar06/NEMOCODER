"""
Role-specific execution profiles for mutation runtime behavior.

This module defines how different roles (planner, editor, reviewer, summarizer)
execute mutations with role-specific timeout constraints, error strategies, and
provider-aware reliability settings.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal


class ErrorStrategy(Enum):
    """Error handling strategy per role."""

    FAIL_FAST = "fail_fast"  # Planner: quick failures, no retries
    STANDARD = "standard"  # Editor: standard timeout + single attempt
    CONSERVATIVE = "conservative"  # Reviewer: longer timeout, single attempt, strict validation
    RESILIENT = "resilient"  # Summarizer: longest timeout, best effort


class RoleExecutionProfile:
    """Runtime execution profile for a specific role.

    Defines timeout constraints, error strategies, and provider-aware
    reliability settings for role-based mutation execution.
    """

    # Role hierarchy: planner < editor < reviewer < summarizer (in terms of granted time/resources)
    ROLE_TIMEOUT_RATIOS = {
        "planner": 0.5,  # 50% of standard timeout
        "editor": 1.0,  # Standard timeout
        "reviewer": 1.5,  # 150% of standard timeout
        "summarizer": 2.0,  # 200% of standard timeout
    }

    ROLE_ERROR_STRATEGIES = {
        "planner": ErrorStrategy.FAIL_FAST,
        "editor": ErrorStrategy.STANDARD,
        "reviewer": ErrorStrategy.CONSERVATIVE,
        "summarizer": ErrorStrategy.RESILIENT,
    }

    PROVIDER_TIMEOUT_BOUNDS = {
        "subprocess": {"min": 5.0, "max": 600.0},
        "fake": {"min": 1.0, "max": 300.0},
    }

    def __init__(
        self,
        role: Literal["planner", "editor", "reviewer", "summarizer"],
        provider: Literal["subprocess", "fake"],
        base_timeout_seconds: float,
    ) -> None:
        """Initialize an execution profile for a role and provider.

        Args:
            role: One of planner, editor, reviewer, summarizer.
            provider: One of subprocess or fake.
            base_timeout_seconds: Base timeout to apply role multiplier to.

        Raises:
            ValueError: If role or provider is unsupported.
        """
        if role not in self.ROLE_TIMEOUT_RATIOS:
            raise ValueError(f"unsupported role: {role}")
        if provider not in self.PROVIDER_TIMEOUT_BOUNDS:
            raise ValueError(f"unsupported provider: {provider}")

        self.role = role
        self.provider = provider
        self.base_timeout_seconds = base_timeout_seconds

    @property
    def error_strategy(self) -> ErrorStrategy:
        """Return the error strategy for this role."""
        return self.ROLE_ERROR_STRATEGIES[self.role]

    @property
    def timeout_ratio(self) -> float:
        """Return the timeout multiplier for this role."""
        return self.ROLE_TIMEOUT_RATIOS[self.role]

    @property
    def effective_timeout_seconds(self) -> float:
        """Compute the effective timeout for this role, respecting provider bounds.

        Returns:
            The timeout in seconds, clamped to provider-specific bounds.
        """
        desired = self.base_timeout_seconds * self.timeout_ratio
        bounds = self.PROVIDER_TIMEOUT_BOUNDS[self.provider]

        # Clamp to provider bounds
        if desired < bounds["min"]:
            return bounds["min"]
        if desired > bounds["max"]:
            return bounds["max"]
        return desired

    @property
    def is_fail_fast(self) -> bool:
        """Return True if this role uses fail-fast error strategy."""
        return self.error_strategy == ErrorStrategy.FAIL_FAST

    @property
    def is_conservative(self) -> bool:
        """Return True if this role uses conservative error strategy."""
        return self.error_strategy == ErrorStrategy.CONSERVATIVE

    @property
    def is_resilient(self) -> bool:
        """Return True if this role uses resilient error strategy."""
        return self.error_strategy == ErrorStrategy.RESILIENT

    def describe(self) -> str:
        """Return a human-readable description of this execution profile."""
        return (
            f"Role={self.role} Provider={self.provider} "
            f"Strategy={self.error_strategy.value} "
            f"EffectiveTimeout={self.effective_timeout_seconds}s "
            f"(base={self.base_timeout_seconds}s × {self.timeout_ratio})"
        )


def default_execution_profile_for_role(
    role: Literal["planner", "editor", "reviewer", "summarizer"],
    provider: Literal["subprocess", "fake"],
) -> RoleExecutionProfile:
    """Create a default execution profile for a role and provider.

    This returns a profile with a sensible base timeout (30s for subprocess, 5s for fake)
    and applies role-specific multipliers.

    Args:
        role: One of planner, editor, reviewer, summarizer.
        provider: One of subprocess or fake.

    Returns:
        A RoleExecutionProfile for the given role and provider.

    Raises:
        ValueError: If role or provider is unsupported.
    """
    # Default base timeouts per provider
    base_timeouts = {
        "subprocess": 30.0,
        "fake": 5.0,
    }
    base_timeout = base_timeouts.get(provider, 30.0)
    return RoleExecutionProfile(role, provider, base_timeout)
