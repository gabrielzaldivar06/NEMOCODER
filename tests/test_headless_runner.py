import unittest
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from nemo_coding_platform.core.engine_interface import FakeEngineProvider
from nemo_coding_platform.core.evals import score_headless_result
from nemo_coding_platform.core.headless_handoff import HandoffRequest
from nemo_coding_platform.core.headless_runner import _bounded_nemo_context, execute_headless_handoff
from nemo_coding_platform.core.memory import MemoryAtomType
from nemo_coding_platform.core.memory_persistence import PersistentMemoryStore
from nemo_coding_platform.core.mutations import FileWrite, MutationPlan
from nemo_coding_platform.core.nemo_adapter import PersistentNemoAdapter
from nemo_coding_platform.core.worktree_runtime import snapshot_runtime_files
from nemo_coding_platform.core.task_run import ArtifactType, EventKind
from nemo_coding_platform.core.validation import ValidationCommand, ValidationResult, ValidationStatus, ValidationSuiteResult


def _execute_fake_handoff(request: HandoffRequest, *args, **kwargs):
    kwargs.setdefault("provider_mode", "fake")
    return execute_headless_handoff(request, *args, **kwargs)


class HeadlessRunnerTests(unittest.TestCase):
    def test_bounded_nemo_context_keeps_short_text_unchanged(self) -> None:
        text = "short context"
        self.assertEqual(_bounded_nemo_context(text, max_chars=50), text)

    def test_bounded_nemo_context_truncates_and_marks_payload(self) -> None:
        text = "A" * 120
        bounded = _bounded_nemo_context(text, max_chars=40)

        self.assertIn("[truncated_nemo_context", bounded)
        self.assertIn("limit=40", bounded)
        self.assertLessEqual(len(bounded), 120)

    def test_headless_run_produces_replayable_review_gated_result(self) -> None:
        result = _execute_fake_handoff(
            HandoffRequest("Build feature", ".", ("passes tests",), ("python -m unittest",))
        )

        self.assertTrue(result.timeline.has_event_kind(EventKind.CONTEXT_BOOTSTRAPPED))
        self.assertTrue(result.timeline.has_event_kind(EventKind.CHECKPOINT))
        self.assertTrue(result.timeline.has_event_kind(EventKind.MEMORY_WRITTEN))
        self.assertTrue(any(artifact.artifact_type == ArtifactType.CHECKPOINT for artifact in result.artifacts))
        self.assertTrue(any(artifact.artifact_type == ArtifactType.REVIEW_PACKAGE for artifact in result.artifacts))
        self.assertEqual(score_headless_result(result).score, 1.0)

    def test_headless_run_writes_checkpoint_artifact(self) -> None:
        result = _execute_fake_handoff(
            HandoffRequest("Build feature", ".", ("passes tests",), ("python -m unittest",)),
            task_id="checkpoint-task",
            run_id="checkpoint-run",
        )

        checkpoint = Path(result.run.sandbox_path) / "checkpoint.md"
        content = checkpoint.read_text(encoding="utf-8")

        self.assertIn("# Checkpoint", content)
        self.assertIn("validation passed=1/1", content)
        self.assertIn("generated-implementation.md", content)
        self.assertIn("checkpoint.md", result.runtime_files)

    def test_bounded_simulation_writes_phase_checkpoints(self) -> None:
        result = _execute_fake_handoff(
            HandoffRequest("Build feature", ".", ("passes tests",), ("python -m unittest",)),
            task_id="bounded-task",
            run_id="bounded-run",
            bounded_simulation=True,
        )

        checkpoint_refs = tuple(event.payload_ref for event in result.timeline.events if event.kind == EventKind.CHECKPOINT)
        artifact_paths = tuple(artifact.path for artifact in result.artifacts if artifact.artifact_type == ArtifactType.CHECKPOINT)

        self.assertEqual(checkpoint_refs, ("checkpoint-plan.md", "checkpoint-execute.md", "checkpoint-review.md"))
        self.assertEqual(artifact_paths, checkpoint_refs)
        self.assertIn("checkpoint-plan.md", result.runtime_files)
        self.assertIn("checkpoint-execute.md", result.runtime_files)
        self.assertIn("checkpoint-review.md", result.runtime_files)
        self.assertTrue(any(item.call.tool_name == "store_conversation" and "checkpoint-execute.md" in item.call.arguments.get("summary", "") for item in result.nemo_results))

    def test_headless_run_records_validation_failure_in_score(self) -> None:
        result = _execute_fake_handoff(
            HandoffRequest("Build feature", ".", ("passes tests",), ("python -m unittest",), repair_budget=1),
            ("python -m unittest",),
        )

        self.assertFalse(result.validation.passed)
        self.assertLess(score_headless_result(result).score, 1.0)

    def test_headless_run_accepts_custom_ids_and_summary_dict(self) -> None:
        result = _execute_fake_handoff(
            HandoffRequest("Build feature", ".", ("passes tests",), ("python -m unittest",)),
            task_id="task-custom",
            run_id="run-custom",
        )

        self.assertEqual(result.to_summary_dict()["task_id"], "task-custom")
        self.assertEqual(result.to_summary_dict()["run_id"], "run-custom")

    def test_headless_run_records_nemo_calls_and_runtime_files(self) -> None:
        result = _execute_fake_handoff(
            HandoffRequest("Build feature", ".", ("passes tests",), ("python -m unittest",)),
            task_id="runtime-files",
            run_id="run-files",
        )

        self.assertEqual([item.call.tool_name for item in result.nemo_results], ["prime_context", "build_context_portfolio", "record_context_feedback", "compress_context_artifact", "store_conversation"])
        files = snapshot_runtime_files(type("Spec", (), {"worktree_path": __import__("pathlib").Path(result.run.sandbox_path)})())
        self.assertIn("generated-implementation.md", files)
        self.assertIn("review-package.md", files)
        self.assertIn("validation.txt", files)
        self.assertIn("memory.md", files)

    def test_headless_run_can_use_real_validation(self) -> None:
        result = _execute_fake_handoff(
            HandoffRequest("Build feature", ".", ("passes tests",), (f"{sys.executable} --version",)),
            real_validation=True,
        )

        self.assertTrue(result.validation.passed)

    def test_headless_run_writes_python_validation_script_inside_runtime(self) -> None:
        result = _execute_fake_handoff(
            HandoffRequest("Build feature", ".", ("passes tests",), (f"{sys.executable} --version",)),
            real_validation=True,
            validation_python_scripts=("assert 1 + 1 == 2\n",),
            task_id="validation-script",
            run_id="run-script",
        )

        self.assertTrue(result.validation.passed)
        self.assertIn("validation_script_1.py", result.runtime_files)
        validation_text = (Path(result.run.sandbox_path) / "validation.txt").read_text(encoding="utf-8")
        self.assertIn("validation_script_1.py", validation_text)
        self.assertIn("returncode=0", validation_text)

    def test_headless_run_creates_repair_attempt_on_validation_failure(self) -> None:
        result = _execute_fake_handoff(
            HandoffRequest("Build feature", ".", ("passes tests",), ("python -m unittest",), repair_budget=1),
            ("python -m unittest",),
        )

        self.assertIsNotNone(result.repair_plan)
        self.assertEqual(len(result.repair_plan.attempts), 1)

    def test_headless_run_records_provider_and_portfolio(self) -> None:
        result = _execute_fake_handoff(
            HandoffRequest("Build feature", ".", ("passes tests",), ("python -m unittest",)),
        )

        self.assertIsNotNone(result.mutation_result)
        self.assertEqual(result.mutation_result.provider, "fake-space-code")
        self.assertEqual(result.mutation_result.model_profile.model, "nvidia.agentic.coder-4b")
        self.assertIn("generated-implementation.md", result.mutation_result.changed_files)
        self.assertIsNotNone(result.portfolio)
        self.assertIn("context", result.portfolio)

    def test_headless_run_blocks_validation_when_provider_makes_no_changes(self) -> None:
        class NoopProvider:
            name = "noop-provider"

            def create_plan(self, _request):
                from nemo_coding_platform.core.mutations import MutationPlan

                return MutationPlan(writes=())

        result = execute_headless_handoff(
            HandoffRequest("Build feature", ".", ("passes tests",), (f"{sys.executable} --version",), repair_budget=0),
            real_validation=True,
            mutation_provider=NoopProvider(),
        )

        self.assertFalse(result.validation.passed)
        self.assertIn("no changed files", result.validation.results[0].output)
        self.assertEqual(score_headless_result(result).grade, "blocked")

    def test_headless_run_counts_successful_repair_changes_as_effective_mutation(self) -> None:
        class NoopThenRepairProvider:
            name = "noop-then-repair"

            def __init__(self) -> None:
                self.calls = 0

            def create_plan(self, _request):
                self.calls += 1
                if self.calls == 1:
                    return MutationPlan(writes=())
                return MutationPlan(writes=(FileWrite("src/repaired.py", "def ok():\n    return True\n"),))

        result = execute_headless_handoff(
            HandoffRequest("Build feature", ".", ("passes tests",), ("python -m unittest",), repair_budget=1),
            mutation_provider=NoopThenRepairProvider(),
        )

        self.assertTrue(result.validation.passed)
        self.assertEqual(result.effective_changed_files, ("src/repaired.py",))
        self.assertEqual(score_headless_result(result).grade, "ready")
        self.assertIn("src/repaired.py", result.review_package.to_markdown())

    def test_headless_run_passes_target_files_to_provider(self) -> None:
        class RecordingProvider(FakeEngineProvider):
            def __init__(self) -> None:
                self.seen_targets: tuple[str, ...] = ()

            def create_plan(self, request):
                self.seen_targets = request.target_files
                return super().create_plan(request)

        provider = RecordingProvider()
        _execute_fake_handoff(
            HandoffRequest("Build feature", ".", ("passes tests",), ("python -m unittest",)),
            mutation_provider=provider,
            target_files=("src/demo.py", "tests/test_demo.py"),
        )

        self.assertEqual(provider.seen_targets, ("src/demo.py", "tests/test_demo.py"))

    def test_subprocess_output_is_linked_from_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            command = (
                sys.executable,
                "-c",
                "from pathlib import Path; Path('subprocess-created.txt').write_text('ok', encoding='utf-8'); print('stdout-marker')",
            )
            result = execute_headless_handoff(
                HandoffRequest("Build feature", tmp, ("passes tests",), ("python -m unittest",)),
                provider_mode="subprocess",
                engine_command=command,
                task_id="subprocess-output-task",
                run_id="subprocess-output-run",
                bounded_simulation=True,
            )

        mutation_events = [event for event in result.timeline.events if event.kind == EventKind.MUTATION_CREATED]
        self.assertEqual(mutation_events[0].payload_ref, "engine-output.txt")
        self.assertIn("engine-output.txt", result.runtime_files)
        self.assertTrue(any(artifact.artifact_type == ArtifactType.ENGINE_OUTPUT for artifact in result.artifacts))
        output = (Path(result.run.sandbox_path) / "engine-output.txt").read_text(encoding="utf-8")
        self.assertIn("stdout-marker", output)

    def test_headless_run_retries_with_chunked_objective_after_context_overflow(self) -> None:
        class OverflowThenChunkProvider:
            name = "overflow-then-chunk"

            def __init__(self) -> None:
                self.calls = 0
                self.objectives: list[str] = []
                self.last_returncode: int | None = None
                self.last_stdout = ""
                self.last_stderr = ""

            def create_plan(self, request):
                self.calls += 1
                self.objectives.append(request.objective)
                if self.calls == 1:
                    self.last_returncode = 1
                    self.last_stdout = "MidStreamFallbackError: Context size has been exceeded"
                    self.last_stderr = ""
                    return MutationPlan(writes=())
                self.last_returncode = 0
                self.last_stdout = "retry-success"
                self.last_stderr = ""
                return MutationPlan(writes=(FileWrite("flower_ascii_iterations.md", "## Iteration 01\nDRAW\n"),))

        provider = OverflowThenChunkProvider()
        _execute_fake_handoff(
            HandoffRequest(
                "Produce an ASCII flower through 50 strict improvement iterations and save full log.",
                ".",
                ("file created",),
                ("python -m unittest",),
            ),
            mutation_provider=provider,
        )

        self.assertEqual(provider.calls, 2)
        self.assertIn("CHUNKED EXECUTION MODE", provider.objectives[1])

    def test_headless_run_does_not_adopt_retry_without_changed_files(self) -> None:
        class OverflowThenNoopRetryProvider:
            name = "overflow-then-noop-retry"

            def __init__(self) -> None:
                self.calls = 0
                self.objectives: list[str] = []
                self.last_returncode: int | None = None
                self.last_stdout = ""
                self.last_stderr = ""

            def create_plan(self, request):
                self.calls += 1
                self.objectives.append(request.objective)
                if self.calls == 1:
                    self.last_returncode = 1
                    self.last_stdout = "MidStreamFallbackError: Context size has been exceeded"
                    self.last_stderr = ""
                    return MutationPlan(writes=())
                self.last_returncode = 0
                self.last_stdout = "retry-success-noop"
                self.last_stderr = ""
                return MutationPlan(writes=())

        provider = OverflowThenNoopRetryProvider()
        result = _execute_fake_handoff(
            HandoffRequest(
                "Produce an ASCII flower through 50 strict improvement iterations and save full log.",
                ".",
                ("file created",),
                ("python -m unittest",),
                repair_budget=0,
            ),
            mutation_provider=provider,
        )

        self.assertEqual(provider.calls, 2)
        self.assertFalse(result.validation.passed)
        self.assertIn("no changed files", result.validation.results[0].output)

    def test_headless_run_fails_closed_when_real_validation_is_simulated(self) -> None:
        class OneFileProvider:
            name = "one-file-provider"

            def __init__(self) -> None:
                self.last_returncode = 0
                self.last_stdout = "ok"
                self.last_stderr = ""

            def create_plan(self, _request):
                return MutationPlan(writes=(FileWrite("generated-implementation.md", "ok\n"),))

        provider = OneFileProvider()
        simulated_suite = ValidationSuiteResult(
            (
                ValidationResult(
                    ValidationCommand("python -m unittest"),
                    ValidationStatus.PASSED,
                    "simulated pass",
                    None,
                ),
            )
        )

        with patch("nemo_coding_platform.core.headless_runner.run_validation_suite", return_value=simulated_suite):
            result = execute_headless_handoff(
                HandoffRequest(
                    "Build feature",
                    ".",
                    ("passes tests",),
                    ("python -m unittest",),
                    repair_budget=0,
                ),
                real_validation=True,
                mutation_provider=provider,
                provider_mode="fake",
            )

        self.assertFalse(result.validation.passed)
        self.assertTrue(any("integrity guard" in item.command.command for item in result.validation.results))
        self.assertTrue(any("simulated validation detected" in (item.output or "") for item in result.validation.results))

    def test_headless_run_persists_structured_nemo_memory_with_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentMemoryStore(Path(tmp) / "memory.sqlite")
            adapter = PersistentNemoAdapter(store)
            result = _execute_fake_handoff(
                HandoffRequest("Build feature", ".", ("passes tests",), ("python -m unittest",)),
                nemo_adapter=adapter,
                task_id="memory-task",
                run_id="memory-run",
                bounded_simulation=True,
            )
            atoms = store.search_atoms(topic="Headless Handoff", limit=20)
            stats = store.stats()

        self.assertTrue(result.validation.passed)
        self.assertGreaterEqual(stats["evidence_count"], 1)
        self.assertTrue(any(atom.atom.atom_type == MemoryAtomType.ARTIFACT_STATE and "checkpoint-execute.md" in atom.atom.content for atom in atoms))
        review_atoms = [atom for atom in atoms if atom.atom.atom_type == MemoryAtomType.SESSION_SUMMARY and "memory-run" in atom.atom.content]
        self.assertTrue(review_atoms)
        self.assertIsNotNone(review_atoms[0].atom.evidence_handle)

    def test_headless_run_reuses_prior_nemo_memory_on_next_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentMemoryStore(Path(tmp) / "memory.sqlite")
            first_adapter = PersistentNemoAdapter(store)
            _execute_fake_handoff(
                HandoffRequest("Build feature", ".", ("passes tests",), ("python -m unittest",)),
                nemo_adapter=first_adapter,
                task_id="reuse-task",
                run_id="reuse-run-1",
            )
            second_adapter = PersistentNemoAdapter(store)
            second = _execute_fake_handoff(
                HandoffRequest("Build another feature", ".", ("passes tests",), ("python -m unittest",)),
                nemo_adapter=second_adapter,
                task_id="reuse-task-2",
                run_id="reuse-run-2",
            )

        prime_payload = second.nemo_results[0].payload
        self.assertEqual(prime_payload["source"], "persistent_store")
        self.assertIn("reuse-run-1", prime_payload["context"])


if __name__ == "__main__":
    unittest.main()