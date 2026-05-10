"""Tests for core/context_policy.py — ContextMode, ContextProfile, get_profile, trim_context.

Critical invariant: corrections NEVER trimmed, include_corrections is always True.
"""
import pytest

from nemo_coding_platform.core.context_policy import (
    ContextMode,
    ContextProfile,
    get_profile,
    trim_context,
)


class TestGetProfile:
    def test_returns_profile_for_each_mode(self):
        for mode in ContextMode:
            profile = get_profile(mode)
            assert isinstance(profile, ContextProfile)
            assert profile.mode == mode

    def test_string_mode_accepted(self):
        profile = get_profile("execution")
        assert profile.mode == ContextMode.EXECUTION

    def test_unknown_string_falls_back_to_chat(self):
        profile = get_profile("unknown_mode_xyz")
        assert profile.mode == ContextMode.CHAT

    def test_corrections_always_included(self):
        """CRITICAL INVARIANT: include_corrections must always be True."""
        for mode in ContextMode:
            profile = get_profile(mode)
            assert profile.include_corrections is True, (
                f"Mode {mode} has include_corrections=False — this violates the invariant."
            )

    def test_execution_has_lower_budget_than_research(self):
        exec_p = get_profile(ContextMode.EXECUTION)
        res_p = get_profile(ContextMode.RESEARCH)
        assert exec_p.portfolio_budget < res_p.portfolio_budget
        assert exec_p.context_chars < res_p.context_chars

    def test_all_profiles_have_positive_budgets(self):
        for mode in ContextMode:
            profile = get_profile(mode)
            assert profile.portfolio_budget > 0
            assert profile.search_limit > 0
            assert profile.context_chars > 0


class TestTrimContext:
    def test_no_trim_when_within_limit(self):
        profile = get_profile(ContextMode.CHAT)
        short = "hello world"
        assert trim_context(short, profile) == short

    def test_tail_strategy_trims_end(self):
        profile = ContextProfile(
            mode=ContextMode.CHAT,
            portfolio_budget=100,
            search_limit=5,
            anticipate_limit=5,
            context_chars=20,
            trim_strategy="tail",
        )
        long_text = "A" * 10 + "\n" + "B" * 100
        result = trim_context(long_text, profile)
        assert len(result) <= 40  # generous bound
        assert "trimmed" in result

    def test_middle_strategy_preserves_start(self):
        profile = ContextProfile(
            mode=ContextMode.CHAT,
            portfolio_budget=100,
            search_limit=5,
            anticipate_limit=5,
            context_chars=30,
            trim_strategy="middle",
        )
        text = "START:" + "X" * 200 + ":END"
        result = trim_context(text, profile)
        assert result.startswith("START:")
        assert "trimmed" in result

    def test_middle_strategy_preserves_tail(self):
        profile = ContextProfile(
            mode=ContextMode.CHAT,
            portfolio_budget=100,
            search_limit=5,
            anticipate_limit=5,
            context_chars=30,
            trim_strategy="middle",
        )
        text = "START " + "X" * 200 + " END"
        result = trim_context(text, profile)
        assert "END" in result
