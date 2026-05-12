import unittest

from nemo_coding_platform.core.engine_interface import MutationResult
from nemo_coding_platform.core.contracts import ExecutionPhase, RuntimeState
from nemo_coding_platform.core.mutations import MutationPlan
from nemo_coding_platform.core.product import AutonomyLevel
from nemo_coding_platform.core.review_package import ReviewStatus, build_review_package
from nemo_coding_platform.core.task_run import MemoryTrace, Run, Task
from nemo_coding_platform.core.validation import ValidationCommand, ValidationResult, ValidationStatus, ValidationSuiteResult, simulate_validation


class ReviewPackageTests(unittest.TestCase):
    def test_review_package_renders_required_sections(self) -> None:
        task = Task("t1", ".", "Task", "Objective", AutonomyLevel.FULL_HANDOFF)
        run = Run("r1", "t1", RuntimeState.REVIEWING, ExecutionPhase.REVIEW, "rt", ".runtime", "local", "perm", "val")
        validation = simulate_validation(("python -m unittest",))
        traces = (MemoryTrace("m1", "r1", "store_conversation", "conversation", "memory_write", "stored"),)

        package = build_review_package(task, run, "diff", validation, traces)
        markdown = package.to_markdown()

        self.assertEqual(package.status, ReviewStatus.READY)
        self.assertIn("## Diff", markdown)
        self.assertIn("## Validation", markdown)
        self.assertIn("## Memory", markdown)
        self.assertIn("## Risks", markdown)

    def test_review_package_renders_diff_validation_model_and_repair_attempts(self) -> None:
        task = Task("t1", ".", "Task", "Objective", AutonomyLevel.FULL_HANDOFF)
        run = Run("r1", "t1", RuntimeState.REVIEWING, ExecutionPhase.REVIEW, "rt", ".runtime", "local", "perm", "val")
        validation = ValidationSuiteResult(
            (
                ValidationResult(ValidationCommand("python validate.py"), ValidationStatus.PASSED, "ok", 0),
            )
        )
        mutation = MutationResult(
            "subprocess-space-code",
            MutationPlan(writes=(), approved=True, dry_run_completed=True),
            (),
            (),
            "changed 2 file(s)",
            provider_mode="subprocess",
            changed_files=("src/app.py", "tests/test_app.py"),
            diff_artifact="--- before/src/app.py\n+++ after/src/app.py",
        )

        package = build_review_package(task, run, "summary", validation, (), mutation_result=mutation, repair_attempts=2)
        markdown = package.to_markdown()

        self.assertIn("src/app.py", markdown)
        self.assertIn("tests/test_app.py", markdown)
        self.assertIn("python validate.py", markdown)
        self.assertIn("returncode=0", markdown)
        self.assertIn("repair_attempts=2", markdown)
        self.assertIn("--- before/src/app.py", markdown)


if __name__ == "__main__":
    unittest.main()