import tempfile
import unittest

from nemo_coding_platform.core.aider_interface import FakeAiderProvider, MutationRequest
from nemo_coding_platform.core.mutations import FileWrite, MutationPlan
from nemo_coding_platform.core.mutations import QualityMutationEngine
from nemo_coding_platform.core.repair import RepairBudget
from nemo_coding_platform.core.repair_engine import _diffs_are_identical, run_repair_loop
from nemo_coding_platform.core.validation import simulate_validation
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


class RepairEngineTests(unittest.TestCase):
    def test_repair_loop_records_attempt_and_revalidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_repair_loop(
                simulate_validation(("test",), ("test",)),
                ("test",),
                ("test",),
                RepairBudget(1),
                QualityMutationEngine(Workspace.from_path(tmp)),
                FakeAiderProvider(),
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
    def test_todo_reminder_prepended_on_first_attempt(self) -> None:
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
        self.assertIn("<system_reminder>", provider.requests[0].context)
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


if __name__ == "__main__":
    unittest.main()
