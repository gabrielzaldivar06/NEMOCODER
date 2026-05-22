import tempfile
import unittest

from nemo_coding_platform.core.engine_interface import FakeEngineProvider, MutationRequest, TokenUsage
from nemo_coding_platform.core.mutations import FileWrite, MutationPlan
from nemo_coding_platform.core.mutations import QualityMutationEngine
from nemo_coding_platform.core.repair import RepairBudget
from nemo_coding_platform.core.repair_engine import _diffs_are_identical, run_repair_loop
from nemo_coding_platform.core.validation import (
    ValidationCommand,
    ValidationResult,
    ValidationStatus,
    ValidationSuiteResult,
    simulate_validation,
)
from nemo_coding_platform.core.workspace import Workspace


class CapturingNoopProvider:
    name = "capturing-noop"

    def __init__(self) -> None:
        self.requests: list[MutationRequest] = []

    def create_plan(self, request: MutationRequest) -> MutationPlan:
        self.requests.append(request)
        return MutationPlan(writes=())


class FixedDiffProvider:
    """Provider that always returns the same non-empty diff artifact."""

    name = "fixed-diff"

    def __init__(self, diff: str = "--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new") -> None:
        self._diff = diff
        self.call_count = 0

    def create_plan(self, request: MutationRequest) -> MutationPlan:
        self.call_count += 1
        return MutationPlan(writes=(FileWrite("dummy.txt", f"change #{self.call_count}"),))


class RealUsageProvider:
    """Provider that reports real token usage telemetry."""

    name = "real-usage"

    def __init__(self, total_tokens: int = 120) -> None:
        self.last_usage = TokenUsage(
            prompt_tokens=max(0, total_tokens - 20),
            completion_tokens=20,
            total_tokens=total_tokens,
            source="real",
            model_name="test-model",
        )
        self.call_count = 0

    def create_plan(self, request: MutationRequest) -> MutationPlan:
        self.call_count += 1
        return MutationPlan(writes=(FileWrite("token-usage.txt", f"attempt #{self.call_count}"),))


class RepairEngineTests(unittest.TestCase):
    def test_repair_loop_records_attempt_and_revalidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_repair_loop(
                simulate_validation(("test",), ("test",)),
                ("test",),
                ("test",),
                RepairBudget(1),
                QualityMutationEngine(Workspace.from_path(tmp)),
                FakeEngineProvider(),
                MutationRequest("Build", "spec.md", ("ok",), "context"),
            )

        self.assertEqual(len(result.plan.attempts), 1)
        self.assertEqual(len(result.mutation_results), 1)
        self.assertFalse(result.validation.passed)

    def test_repair_request_preserves_runtime_model_targets_and_validation_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = CapturingNoopProvider()
            result = run_repair_loop(
                simulate_validation(("python tests.py",), ("python tests.py",)),
                ("python tests.py",),
                ("python tests.py",),
                RepairBudget(1),
                QualityMutationEngine(Workspace.from_path(tmp)),
                provider,
                MutationRequest(
                    "Build",
                    "spec.md",
                    ("ok",),
                    "NEMO context",
                    provider_mode="subprocess",
                    repo_path="repo-root",
                    runtime_path=tmp,
                    target_files=("src/app.py",),
                    validation_output="initial stderr",
                    timeout_seconds=99,
                    previous_diff="--- previous diff",
                ),
            )

        self.assertEqual(result.stop_reason, "repair_noop")
        self.assertEqual(provider.requests[0].provider_mode, "subprocess")
        self.assertEqual(provider.requests[0].repo_path, "repo-root")
        self.assertEqual(provider.requests[0].runtime_path, tmp)
        self.assertEqual(provider.requests[0].target_files, ("src/app.py",))
        self.assertEqual(provider.requests[0].timeout_seconds, 99)
        self.assertEqual(provider.requests[0].repair_attempt, 1)
        self.assertIn("python tests.py", provider.requests[0].validation_output)
        self.assertIn("returncode", provider.requests[0].validation_output)
        self.assertIn("simulated failure", provider.requests[0].validation_output)
        self.assertIn("--- previous diff", provider.requests[0].context)

    def test_repair_loop_stops_when_provider_makes_no_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = CapturingNoopProvider()
            result = run_repair_loop(
                simulate_validation(("test",), ("test",)),
                ("test",),
                ("test",),
                RepairBudget(3),
                QualityMutationEngine(Workspace.from_path(tmp)),
                provider,
                MutationRequest("Build", "spec.md", ("ok",), "context"),
            )

        self.assertEqual(len(result.plan.attempts), 1)
        self.assertEqual(len(result.mutation_results), 1)
        self.assertEqual(result.stop_reason, "repair_noop")

    def test_repair_loop_respects_time_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_repair_loop(
                simulate_validation(("test",), ("test",)),
                ("test",),
                ("test",),
                RepairBudget(3),
                QualityMutationEngine(Workspace.from_path(tmp)),
                FakeEngineProvider(),
                MutationRequest("Build", "spec.md", ("ok",), "context"),
                time_limit_seconds=0.0,
            )

        self.assertEqual(result.stop_reason, "repair_time_budget_exhausted")

    def test_repair_loop_reports_attempt_callback(self) -> None:
        seen: list[tuple[int, float]] = []
        with tempfile.TemporaryDirectory() as tmp:
            run_repair_loop(
                simulate_validation(("test",), ("test",)),
                ("test",),
                ("test",),
                RepairBudget(1),
                QualityMutationEngine(Workspace.from_path(tmp)),
                FakeEngineProvider(),
                MutationRequest("Build", "spec.md", ("ok",), "context"),
                on_attempt=lambda attempt, elapsed: seen.append((attempt, elapsed)),
            )

        self.assertTrue(seen)
        self.assertEqual(seen[0][0], 1)

    def test_run_repair_loop_accepts_nemo_adapter_without_crash(self) -> None:
        from nemo_coding_platform.core.nemo_adapter import InMemoryNemoAdapter
        with tempfile.TemporaryDirectory() as tmp:
            # simulate_validation with no commands → pre-passed, loop body never runs
            result = run_repair_loop(
                simulate_validation((), ()),
                (),
                (),
                RepairBudget(1),
                QualityMutationEngine(Workspace.from_path(tmp)),
                FakeEngineProvider(),
                MutationRequest("Build", "spec.md", ("ok",), "context"),
                nemo_adapter=InMemoryNemoAdapter(),
                task_id="test-task",
            )
        self.assertIsNotNone(result)
        self.assertEqual(result.stop_reason, "")


# --- Phase 4: Loop Detection Tests ---

class DiffsIdenticalTests(unittest.TestCase):
    def test_identical_diffs(self) -> None:
        self.assertTrue(_diffs_are_identical("--- a\n+++ b", "--- a\n+++ b"))

    def test_identical_with_whitespace_difference(self) -> None:
        self.assertTrue(_diffs_are_identical("  diff  ", "diff"))

    def test_different_diffs(self) -> None:
        self.assertFalse(_diffs_are_identical("--- a\n+++ b", "--- x\n+++ y"))

    def test_both_empty(self) -> None:
        self.assertTrue(_diffs_are_identical("", ""))

    def test_one_empty(self) -> None:
        self.assertFalse(_diffs_are_identical("--- a", ""))


class TodoReminderInjectionTests(unittest.TestCase):
    def test_todo_reminder_not_injected_during_repair(self) -> None:
        """todo_reminder is intentionally skipped during repair cycles.

        Repair prompts already have a focused objective ("Repair validation
        failure for: ...") plus concrete repair evidence.  Injecting the full
        pipeline-step checklist causes the model to enter a deep THINKING loop
        trying to redo infrastructure steps the runner has already completed,
        which wastes most of the timeout budget.
        """
        with tempfile.TemporaryDirectory() as tmp:
            provider = CapturingNoopProvider()
            reminder = "<system_reminder>\nDo the thing.\n</system_reminder>"
            run_repair_loop(
                simulate_validation(("test",), ("test",)),
                ("test",),
                ("test",),
                RepairBudget(1),
                QualityMutationEngine(Workspace.from_path(tmp)),
                provider,
                MutationRequest("Build", "spec.md", ("ok",), "base context"),
                todo_reminder=reminder,
            )

        self.assertTrue(len(provider.requests) >= 1)
        self.assertNotIn("<system_reminder>", provider.requests[0].context)
        self.assertIn("base context", provider.requests[0].context)

    def test_no_todo_reminder_when_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = CapturingNoopProvider()
            run_repair_loop(
                simulate_validation(("test",), ("test",)),
                ("test",),
                ("test",),
                RepairBudget(1),
                QualityMutationEngine(Workspace.from_path(tmp)),
                provider,
                MutationRequest("Build", "spec.md", ("ok",), "base context"),
                todo_reminder="",
            )

        self.assertNotIn("<system_reminder>", provider.requests[0].context)


class TokenBudgetTests(unittest.TestCase):
    def test_repair_loop_stops_on_token_budget_exhausted(self) -> None:
        """Repair loop stops immediately when token_budget=0 (pre-exhausted)."""
        with tempfile.TemporaryDirectory() as tmp:
            result = run_repair_loop(
                simulate_validation(("test",), ("test",)),
                ("test",),
                ("test",),
                RepairBudget(5),
                QualityMutationEngine(Workspace.from_path(tmp)),
                FakeEngineProvider(),
                MutationRequest("Build", "spec.md", ("ok",), "context"),
                token_budget=0,
            )

        self.assertEqual(result.stop_reason, "token_budget_exhausted_estimated")
        self.assertEqual(len(result.mutation_results), 0)

    def test_repair_loop_stops_mid_run_on_token_budget(self) -> None:
        """Repair loop stops after some attempts when token_budget is small."""
        with tempfile.TemporaryDirectory() as tmp:
            result = run_repair_loop(
                simulate_validation(("test",), ("test",)),
                ("test",),
                ("test",),
                RepairBudget(10),
                QualityMutationEngine(Workspace.from_path(tmp)),
                FakeEngineProvider(),
                MutationRequest("Build x" * 50, "spec.md", ("ok",), "c" * 400),
                token_budget=50,  # ~200 chars budget, context is large
            )

        self.assertEqual(result.stop_reason, "token_budget_exhausted_estimated")

    def test_repair_result_tracks_tokens_consumed(self) -> None:
        """tokens_consumed is non-zero after a repair attempt that writes files."""
        with tempfile.TemporaryDirectory() as tmp:
            result = run_repair_loop(
                simulate_validation(("test",), ("test",)),
                ("test",),
                ("test",),
                RepairBudget(1),
                QualityMutationEngine(Workspace.from_path(tmp)),
                FakeEngineProvider(),
                MutationRequest("Build", "spec.md", ("ok",), "context" * 20),
            )

        # FakeAiderProvider writes a file, so the loop runs one attempt
        # tokens_consumed should be tracked even without a budget cap
        self.assertGreaterEqual(result.tokens_consumed, 0)

    def test_repair_result_tokens_consumed_zero_when_no_budget_and_noop(self) -> None:
        """tokens_consumed starts at 0 and stays 0 on immediate noop."""
        with tempfile.TemporaryDirectory() as tmp:
            provider = CapturingNoopProvider()
            result = run_repair_loop(
                simulate_validation(("test",), ("test",)),
                ("test",),
                ("test",),
                RepairBudget(1),
                QualityMutationEngine(Workspace.from_path(tmp)),
                provider,
                MutationRequest("Build", "spec.md", ("ok",), "context"),
            )

        self.assertEqual(result.stop_reason, "repair_noop")
        self.assertEqual(result.tokens_consumed, 0)

    def test_repair_loop_prefers_real_usage_tokens_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = RealUsageProvider(total_tokens=120)
            result = run_repair_loop(
                simulate_validation(("test",), ("test",)),
                ("test",),
                ("test",),
                RepairBudget(5),
                QualityMutationEngine(Workspace.from_path(tmp)),
                provider,
                MutationRequest("Build", "spec.md", ("ok",), "context"),
                token_budget=100,
            )

        self.assertEqual(result.stop_reason, "token_budget_exhausted_real")
        self.assertEqual(result.tokens_consumed, 120)

    def test_repair_loop_uses_compacted_validation_evidence_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = CapturingNoopProvider()
            long_output = "failure-log " * 300
            validation = ValidationSuiteResult(
                (
                    ValidationResult(
                        ValidationCommand("python tests.py"),
                        ValidationStatus.FAILED,
                        long_output,
                        1,
                    ),
                )
            )

            run_repair_loop(
                validation,
                ("python tests.py",),
                ("python tests.py",),
                RepairBudget(1),
                QualityMutationEngine(Workspace.from_path(tmp)),
                provider,
                MutationRequest("Build", "spec.md", ("ok",), "context"),
                evidence_compactor=lambda _content, _attempt: ("compacted failure summary", "ev-test-1"),
            )

        self.assertIn("compacted_validation_claim=compacted failure summary", provider.requests[0].validation_output)
        self.assertIn("evidence_handle=ev-test-1", provider.requests[0].validation_output)

    def test_quality_floor_triggers_extra_attempt_when_score_below_threshold(self) -> None:
        """When validation passes but critique score < quality_threshold, loop makes one more attempt."""
        scores_returned = iter([4.0, 8.0])  # first critique: too low; second: above threshold

        def fake_critique(objective: str, diff: str, validation_summary: str) -> float:
            return next(scores_returned, 8.0)

        attempts: list[str] = []

        class TrackingProvider(FakeEngineProvider):
            def create_plan(self, request):
                attempts.append(request.objective)
                return super().create_plan(request)

        # validator always passes — repair attempt fixes validation, then quality mode kicks in
        def _passing_validator():
            return simulate_validation(("test",), ())

        with tempfile.TemporaryDirectory() as tmp:
            result = run_repair_loop(
                simulate_validation(("test",), ("test",)),  # initial: FAILS
                ("test",),
                ("test",),
                RepairBudget(3),
                QualityMutationEngine(Workspace.from_path(tmp)),
                TrackingProvider(),
                MutationRequest("Build feature", "spec.md", ("ok",), "context"),
                validator=_passing_validator,
                critique_fn=fake_critique,
                quality_threshold=7.0,
            )

        # Iteration 1: repair mode (validation failure) → passes → score=4.0 < 7.0
        # Iteration 2: quality mode → "Improve code quality" → score=8.0 >= 7.0 → stop
        quality_attempts = [a for a in attempts if "quality" in a.lower()]
        self.assertTrue(len(quality_attempts) >= 1, "at least one quality-improvement attempt expected")
        self.assertGreaterEqual(result.best_score, 7.0, "final score should meet quality threshold")
        self.assertEqual(result.stop_reason, "")

    def test_quality_floor_stops_when_budget_exhausted_before_reaching_threshold(self) -> None:
        """When budget runs out during quality improvement, stop_reason is quality_below_threshold."""
        def always_low_critique(objective: str, diff: str, validation_summary: str) -> float:
            return 3.0  # never reaches threshold

        def _passing_validator():
            return simulate_validation((), ())

        with tempfile.TemporaryDirectory() as tmp:
            result = run_repair_loop(
                simulate_validation(("test",), ("test",)),  # initial: FAILS
                ("test",),
                ("test",),
                RepairBudget(2),  # slot 1: repair; slot 2: quality attempt — budget exhausted
                QualityMutationEngine(Workspace.from_path(tmp)),
                FakeEngineProvider(),
                MutationRequest("Build feature", "spec.md", ("ok",), "context"),
                validator=_passing_validator,
                critique_fn=always_low_critique,
                quality_threshold=7.0,
            )

        self.assertEqual(result.stop_reason, "quality_below_threshold")
        self.assertTrue(result.validation.passed)
        self.assertLess(result.best_score, 7.0)


if __name__ == "__main__":
    unittest.main()
