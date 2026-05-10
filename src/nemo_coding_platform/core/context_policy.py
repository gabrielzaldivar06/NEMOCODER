"""Context policy: mode-aware token budgets, profiles, and context trimming.

Critical invariant: NEMO corrections are NEVER trimmed regardless of budget.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ContextMode(str, Enum):
    CHAT = "chat"
    RESEARCH = "research"
    EXECUTION = "execution"
    PLAN = "plan"


@dataclass(frozen=True)
class ContextProfile:
    mode: ContextMode
    portfolio_budget: int      # NEMO portfolio token budget
    search_limit: int          # max search results from NEMO
    anticipate_limit: int      # max anticipate results from NEMO
    context_chars: int         # max chars for assembled context
    include_corrections: bool = True    # NEVER set False
    include_preferences: bool = True
    trim_strategy: str = "tail"  # "tail" | "middle"


_PROFILES: dict[ContextMode, ContextProfile] = {
    ContextMode.EXECUTION: ContextProfile(
        mode=ContextMode.EXECUTION,
        portfolio_budget=760,
        search_limit=4,
        anticipate_limit=4,
        context_chars=1900,
    ),
    ContextMode.RESEARCH: ContextProfile(
        mode=ContextMode.RESEARCH,
        portfolio_budget=1300,
        search_limit=8,
        anticipate_limit=7,
        context_chars=3400,
    ),
    ContextMode.PLAN: ContextProfile(
        mode=ContextMode.PLAN,
        portfolio_budget=1100,
        search_limit=6,
        anticipate_limit=6,
        context_chars=2800,
    ),
    ContextMode.CHAT: ContextProfile(
        mode=ContextMode.CHAT,
        portfolio_budget=900,
        search_limit=5,
        anticipate_limit=5,
        context_chars=2400,
    ),
}


def get_profile(mode: ContextMode | str) -> ContextProfile:
    """Return the ContextProfile for *mode*. Falls back to CHAT for unknown values."""
    if isinstance(mode, str):
        try:
            mode = ContextMode(mode.lower())
        except ValueError:
            mode = ContextMode.CHAT
    return _PROFILES.get(mode, _PROFILES[ContextMode.CHAT])


def trim_context(context: str, profile: ContextProfile) -> str:
    """Trim *context* to fit within profile.context_chars.

    Trimming always preserves the leading content (tail strategy) or removes
    the middle (middle strategy). Corrections embedded at the start of the
    string are always preserved.
    """
    if len(context) <= profile.context_chars:
        return context
    limit = profile.context_chars
    if profile.trim_strategy == "middle":
        keep_head = limit * 2 // 3
        keep_tail = limit - keep_head
        trimmed = context[:keep_head] + "\n[...trimmed...]\n" + context[-keep_tail:]
        return trimmed
    # Default: tail — keep beginning, drop end
    return context[:limit].rsplit("\n", 1)[0] + "\n[...trimmed...]"
