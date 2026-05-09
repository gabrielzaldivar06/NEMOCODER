"""
FR2 Runtime Execution: Role-Specific Error/Connection Handling Contracts

Tests that validate role-aware timeout enforcement, error strategies, and
provider-specific reliability semantics for mutation execution.
"""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from nemo_coding_platform.core.engine_interface import (
    MutationRequest,
    SubprocessEngineProvider,
)
from nemo_coding_platform.core.model_config import default_model_profile
from nemo_coding_platform.core.role_execution_profile import (
    ErrorStrategy,
    RoleExecutionProfile,
    default_execution_profile_for_role,
)


class RoleExecutionProfileTests(unittest.TestCase):
    """Test role execution profile creation and timeout computation."""

    def test_default_profile_for_planner_has_fail_fast_strategy(self):
        """Planner role must use fail-fast error strategy."""
        profile = default_execution_profile_for_role("planner", "subprocess")
        self.assertEqual(profile.error_strategy, ErrorStrategy.FAIL_FAST)
        self.assertTrue(profile.is_fail_fast)

    def test_default_profile_for_editor_has_standard_strategy(self):
        """Editor role must use standard error strategy."""
        profile = default_execution_profile_for_role("editor", "subprocess")
        self.assertEqual(profile.error_strategy, ErrorStrategy.STANDARD)
        self.assertFalse(profile.is_fail_fast)

    def test_default_profile_for_reviewer_has_conservative_strategy(self):
        """Reviewer role must use conservative error strategy."""
        profile = default_execution_profile_for_role("reviewer", "subprocess")
        self.assertEqual(profile.error_strategy, ErrorStrategy.CONSERVATIVE)
        self.assertTrue(profile.is_conservative)

    def test_default_profile_for_summarizer_has_resilient_strategy(self):
        """Summarizer role must use resilient error strategy."""
        profile = default_execution_profile_for_role("summarizer", "subprocess")
        self.assertEqual(profile.error_strategy, ErrorStrategy.RESILIENT)
        self.assertTrue(profile.is_resilient)

    def test_planner_timeout_is_half_of_base(self):
        """Planner gets 50% of base timeout."""
        profile = RoleExecutionProfile("planner", "subprocess", base_timeout_seconds=30.0)
        self.assertEqual(profile.effective_timeout_seconds, 15.0)

    def test_editor_timeout_equals_base(self):
        """Editor gets 100% of base timeout."""
        profile = RoleExecutionProfile("editor", "subprocess", base_timeout_seconds=30.0)
        self.assertEqual(profile.effective_timeout_seconds, 30.0)

    def test_reviewer_timeout_is_one_and_half_times_base(self):
        """Reviewer gets 150% of base timeout."""
        profile = RoleExecutionProfile("reviewer", "subprocess", base_timeout_seconds=30.0)
        self.assertEqual(profile.effective_timeout_seconds, 45.0)

    def test_summarizer_timeout_is_double_base(self):
        """Summarizer gets 200% of base timeout."""
        profile = RoleExecutionProfile("summarizer", "subprocess", base_timeout_seconds=30.0)
        self.assertEqual(profile.effective_timeout_seconds, 60.0)

    def test_subprocess_provider_bounds_5_to_600_seconds(self):
        """Subprocess provider enforces 5-600 second bounds."""
        bounds = RoleExecutionProfile.PROVIDER_TIMEOUT_BOUNDS["subprocess"]
        self.assertEqual(bounds["min"], 5.0)
        self.assertEqual(bounds["max"], 600.0)

    def test_fake_provider_bounds_1_to_300_seconds(self):
        """Fake provider enforces 1-300 second bounds."""
        bounds = RoleExecutionProfile.PROVIDER_TIMEOUT_BOUNDS["fake"]
        self.assertEqual(bounds["min"], 1.0)
        self.assertEqual(bounds["max"], 300.0)

    def test_timeout_clamped_to_min_bound_for_subprocess(self):
        """Timeout below min bound is clamped up for subprocess."""
        profile = RoleExecutionProfile("planner", "subprocess", base_timeout_seconds=1.0)
        # planner: 1.0 * 0.5 = 0.5, but clamped to 5.0 (subprocess min)
        self.assertEqual(profile.effective_timeout_seconds, 5.0)

    def test_timeout_clamped_to_max_bound_for_subprocess(self):
        """Timeout above max bound is clamped down for subprocess."""
        profile = RoleExecutionProfile("summarizer", "subprocess", base_timeout_seconds=400.0)
        # summarizer: 400.0 * 2.0 = 800.0, but clamped to 600.0 (subprocess max)
        self.assertEqual(profile.effective_timeout_seconds, 600.0)

    def test_timeout_clamped_to_min_bound_for_fake(self):
        """Timeout below min bound is clamped up for fake."""
        profile = RoleExecutionProfile("planner", "fake", base_timeout_seconds=0.5)
        # planner: 0.5 * 0.5 = 0.25, but clamped to 1.0 (fake min)
        self.assertEqual(profile.effective_timeout_seconds, 1.0)

    def test_timeout_clamped_to_max_bound_for_fake(self):
        """Timeout above max bound is clamped down for fake."""
        profile = RoleExecutionProfile("summarizer", "fake", base_timeout_seconds=100.0)
        # summarizer: 100.0 * 2.0 = 200.0, which now fits within the fake max bound.
        self.assertEqual(profile.effective_timeout_seconds, 200.0)

    def test_unsupported_role_raises_error(self):
        """Creating profile with unsupported role raises ValueError."""
        with self.assertRaises(ValueError) as ctx:
            RoleExecutionProfile("invalid_role", "subprocess", 30.0)
        self.assertIn("unsupported role", str(ctx.exception))

    def test_unsupported_provider_raises_error(self):
        """Creating profile with unsupported provider raises ValueError."""
        with self.assertRaises(ValueError) as ctx:
            RoleExecutionProfile("planner", "invalid_provider", 30.0)
        self.assertIn("unsupported provider", str(ctx.exception))

    def test_describe_returns_human_readable_string(self):
        """Profile description contains all relevant information."""
        profile = RoleExecutionProfile("editor", "subprocess", 30.0)
        desc = profile.describe()
        self.assertIn("Role=editor", desc)
        self.assertIn("Provider=subprocess", desc)
        self.assertIn("standard", desc)
        self.assertIn("30.0", desc)


class MutationRequestRoleTests(unittest.TestCase):
    """Test MutationRequest with role field."""

    def test_mutation_request_accepts_role_field(self):
        """MutationRequest must accept optional role field."""
        request = MutationRequest(
            objective="test",
            spec_path="spec.md",
            acceptance_criteria=("pass test",),
            context="test context",
            role="editor",
        )
        self.assertEqual(request.role, "editor")

    def test_mutation_request_role_defaults_to_empty(self):
        """MutationRequest role defaults to empty string when not specified."""
        request = MutationRequest(
            objective="test",
            spec_path="spec.md",
            acceptance_criteria=("pass test",),
            context="test context",
        )
        self.assertEqual(request.role, "")

    def test_mutation_request_with_all_roles(self):
        """MutationRequest accepts all supported roles."""
        for role in ("planner", "editor", "reviewer", "summarizer"):
            request = MutationRequest(
                objective="test",
                spec_path="spec.md",
                acceptance_criteria=("pass test",),
                context="test context",
                role=role,
            )
            self.assertEqual(request.role, role)


class SubprocessProviderRoleTimeoutTests(unittest.TestCase):
    """Test role-aware timeout enforcement in SubprocessEngineProvider."""

    def setUp(self):
        self.provider = SubprocessEngineProvider(cwd=Path("."))
        self.profile = default_model_profile()
        self.temp_dir = Path(".")

    @patch("nemo_coding_platform.core.engine_interface.subprocess.run")
    @patch("nemo_coding_platform.core.engine_interface.write_engine_message")
    def test_planner_role_receives_reduced_timeout(self, mock_write, mock_run):
        """Planner role should receive 50% of base timeout."""
        mock_write.return_value = Path("message.md")
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        request = MutationRequest(
            objective="test",
            spec_path="spec.md",
            acceptance_criteria=("pass",),
            context="ctx",
            timeout_seconds=30.0,
            role="planner",
            model_profile=self.profile,
        )

        self.provider.create_plan(request)

        # Verify subprocess.run was called with planner timeout (30.0 * 0.5 = 15.0)
        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args.kwargs
        self.assertEqual(call_kwargs["timeout"], 15.0)

    @patch("nemo_coding_platform.core.engine_interface.subprocess.run")
    @patch("nemo_coding_platform.core.engine_interface.write_engine_message")
    def test_editor_role_receives_base_timeout(self, mock_write, mock_run):
        """Editor role should receive 100% of base timeout."""
        mock_write.return_value = Path("message.md")
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        request = MutationRequest(
            objective="test",
            spec_path="spec.md",
            acceptance_criteria=("pass",),
            context="ctx",
            timeout_seconds=30.0,
            role="editor",
            model_profile=self.profile,
        )

        self.provider.create_plan(request)

        call_kwargs = mock_run.call_args.kwargs
        self.assertEqual(call_kwargs["timeout"], 30.0)

    @patch("nemo_coding_platform.core.engine_interface.subprocess.run")
    @patch("nemo_coding_platform.core.engine_interface.write_engine_message")
    def test_reviewer_role_receives_extended_timeout(self, mock_write, mock_run):
        """Reviewer role should receive 150% of base timeout."""
        mock_write.return_value = Path("message.md")
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        request = MutationRequest(
            objective="test",
            spec_path="spec.md",
            acceptance_criteria=("pass",),
            context="ctx",
            timeout_seconds=30.0,
            role="reviewer",
            model_profile=self.profile,
        )

        self.provider.create_plan(request)

        call_kwargs = mock_run.call_args.kwargs
        self.assertEqual(call_kwargs["timeout"], 45.0)

    @patch("nemo_coding_platform.core.engine_interface.subprocess.run")
    @patch("nemo_coding_platform.core.engine_interface.write_engine_message")
    def test_summarizer_role_receives_maximum_timeout(self, mock_write, mock_run):
        """Summarizer role should receive 200% of base timeout."""
        mock_write.return_value = Path("message.md")
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        request = MutationRequest(
            objective="test",
            spec_path="spec.md",
            acceptance_criteria=("pass",),
            context="ctx",
            timeout_seconds=30.0,
            role="summarizer",
            model_profile=self.profile,
        )

        self.provider.create_plan(request)

        call_kwargs = mock_run.call_args.kwargs
        self.assertEqual(call_kwargs["timeout"], 60.0)

    @patch("nemo_coding_platform.core.engine_interface.subprocess.run")
    @patch("nemo_coding_platform.core.engine_interface.write_engine_message")
    def test_no_role_uses_base_timeout_unchanged(self, mock_write, mock_run):
        """Request with empty role should use base timeout unchanged."""
        mock_write.return_value = Path("message.md")
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        request = MutationRequest(
            objective="test",
            spec_path="spec.md",
            acceptance_criteria=("pass",),
            context="ctx",
            timeout_seconds=30.0,
            role="",
            model_profile=self.profile,
        )

        self.provider.create_plan(request)

        call_kwargs = mock_run.call_args.kwargs
        self.assertEqual(call_kwargs["timeout"], 30.0)

    @patch("nemo_coding_platform.core.engine_interface.subprocess.run")
    @patch("nemo_coding_platform.core.engine_interface.write_engine_message")
    def test_timeout_error_message_includes_role(self, mock_write, mock_run):
        """Timeout error message should include role name."""
        import subprocess

        mock_write.return_value = Path("message.md")
        mock_run.side_effect = subprocess.TimeoutExpired("cmd", 15.0)

        request = MutationRequest(
            objective="test",
            spec_path="spec.md",
            acceptance_criteria=("pass",),
            context="ctx",
            timeout_seconds=30.0,
            role="planner",
            model_profile=self.profile,
        )

        self.provider.create_plan(request)

        self.assertIn("role=planner", self.provider.last_stderr)
        self.assertIn("timed out after", self.provider.last_stderr)

    @patch("nemo_coding_platform.core.engine_interface.subprocess.run")
    @patch("nemo_coding_platform.core.engine_interface.write_engine_message")
    def test_provider_different_timeouts_per_role(self, mock_write, mock_run):
        """Test that different roles with same base timeout get different effective timeouts."""
        mock_write.return_value = Path("message.md")
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        base_timeout = 30.0
        roles_and_expected = [
            ("planner", 15.0),  # 30 * 0.5 = 15
            ("editor", 30.0),   # 30 * 1.0 = 30
            ("reviewer", 45.0), # 30 * 1.5 = 45
            ("summarizer", 60.0),  # 30 * 2.0 = 60
        ]

        for role, expected_timeout in roles_and_expected:
            mock_run.reset_mock()
            request = MutationRequest(
                objective="test",
                spec_path="spec.md",
                acceptance_criteria=("pass",),
                context="ctx",
                timeout_seconds=base_timeout,
                role=role,
                model_profile=self.profile,
            )
            self.provider.create_plan(request)
            call_kwargs = mock_run.call_args.kwargs
            self.assertEqual(
                call_kwargs["timeout"],
                expected_timeout,
                f"Role {role} should get {expected_timeout}s, got {call_kwargs['timeout']}s",
            )


class MutationResultRoleTrackingTests(unittest.TestCase):
    """Test that MutationResult tracks the role that performed the mutation."""

    def test_mutation_result_accepts_role_field(self):
        """MutationResult must accept role field."""
        from nemo_coding_platform.core.mutations import MutationPlan

        result = MutationPlan(writes=())
        # Create a minimal mutation result to test role tracking
        from nemo_coding_platform.core.engine_interface import MutationResult

        mutation_result = MutationResult(
            provider="test-provider",
            plan=result,
            dry_run_files=(),
            applied_files=(),
            summary="test",
            role="editor",
        )
        self.assertEqual(mutation_result.role, "editor")

    def test_mutation_result_role_defaults_to_empty(self):
        """MutationResult role defaults to empty string."""
        from nemo_coding_platform.core.mutations import MutationPlan
        from nemo_coding_platform.core.engine_interface import MutationResult

        result = MutationPlan(writes=())
        mutation_result = MutationResult(
            provider="test-provider",
            plan=result,
            dry_run_files=(),
            applied_files=(),
            summary="test",
        )
        self.assertEqual(mutation_result.role, "")


if __name__ == "__main__":
    unittest.main()
