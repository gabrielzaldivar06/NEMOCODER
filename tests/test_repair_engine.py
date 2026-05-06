import tempfile
import unittest

from nemo_coding_platform.core.aider_interface import FakeAiderProvider, MutationRequest
from nemo_coding_platform.core.mutations import MutationPlan
from nemo_coding_platform.core.mutations import QualityMutationEngine
from nemo_coding_platform.core.repair import RepairBudget
from nemo_coding_platform.core.repair_engine import run_repair_loop
from nemo_coding_platform.core.validation import simulate_validation
from nemo_coding_platform.core.workspace import Workspace


class CapturingNoopProvider:
    name = "capturing-noop"

    def __init__(self) -> None:
        self.requests: list[MutationRequest] = []

    def create_plan(self, request: MutationRequest) -> MutationPlan:
        self.requests.append(request)
        return MutationPlan(writes=())


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


if __name__ == "__main__":
    unittest.main()
