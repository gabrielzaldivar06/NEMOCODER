import tempfile
import unittest
from pathlib import Path

from nemo_coding_platform.core.review_gate import apply_merge_plan, build_merge_plan


def ready_payload(repo: Path, sandbox: Path, changed_files: list[str]) -> dict[str, object]:
    return {
        "task": {"id": "task-1", "repo_path": str(repo)},
        "run": {"id": "run-1", "sandbox_path": str(sandbox)},
        "timeline": [
            {"kind": "checkpoint"},
            {"kind": "memory_written"},
        ],
        "artifacts": [{"artifact_type": "review_package"}],
        "validation": {"results": [{"status": "passed"}]},
        "mutation_result": {"changed_files": changed_files},
    }


class ReviewGateTests(unittest.TestCase):
    def test_build_merge_plan_for_create_and_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            repo.mkdir()
            sandbox.mkdir()
            (repo / "existing.txt").write_text("old", encoding="utf-8")
            (sandbox / "existing.txt").write_text("new", encoding="utf-8")
            (sandbox / "created.txt").write_text("created", encoding="utf-8")

            plan = build_merge_plan(ready_payload(repo, sandbox, ["existing.txt", "created.txt"]))

        self.assertTrue(plan.mergeable)
        self.assertEqual([item.operation for item in plan.files], ["update", "create"])
        self.assertEqual(plan.changed_files, ("existing.txt", "created.txt"))

    def test_plan_blocks_non_ready_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            repo.mkdir()
            sandbox.mkdir()
            (sandbox / "created.txt").write_text("created", encoding="utf-8")
            payload = ready_payload(repo, sandbox, ["created.txt"])
            payload["validation"] = {"results": [{"status": "failed"}]}

            plan = build_merge_plan(payload)

        self.assertFalse(plan.mergeable)
        self.assertIn("run_not_ready", plan.risk_flags)
        self.assertIn("validation_failed", plan.risk_flags)

    def test_plan_blocks_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            repo.mkdir()
            sandbox.mkdir()

            plan = build_merge_plan(ready_payload(repo, sandbox, ["../escape.txt"]))

        self.assertFalse(plan.mergeable)
        self.assertIn("path_escapes_workspace", plan.risk_flags)

    def test_plan_blocks_missing_source_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            repo.mkdir()
            sandbox.mkdir()

            plan = build_merge_plan(ready_payload(repo, sandbox, ["missing.txt"]))

        self.assertFalse(plan.mergeable)
        self.assertIn("missing_source_file", plan.risk_flags)

    def test_apply_requires_explicit_review_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            repo.mkdir()
            sandbox.mkdir()
            (sandbox / "created.txt").write_text("created", encoding="utf-8")
            plan = build_merge_plan(ready_payload(repo, sandbox, ["created.txt"]))

            with self.assertRaises(PermissionError):
                apply_merge_plan(plan, approve_review=False)

    def test_apply_copies_only_planned_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            repo.mkdir()
            sandbox.mkdir()
            (sandbox / "created.txt").write_text("created", encoding="utf-8")
            (sandbox / "unplanned.txt").write_text("do not copy", encoding="utf-8")
            plan = build_merge_plan(ready_payload(repo, sandbox, ["created.txt"]))

            result = apply_merge_plan(plan, approve_review=True)

            self.assertEqual(result.applied_files, ("created.txt",))
            self.assertEqual((repo / "created.txt").read_text(encoding="utf-8"), "created")
            self.assertFalse((repo / "unplanned.txt").exists())

    def test_apply_result_renders_audit_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            repo.mkdir()
            sandbox.mkdir()
            (sandbox / "created.txt").write_text("created", encoding="utf-8")
            plan = build_merge_plan(ready_payload(repo, sandbox, ["created.txt"]))

            result = apply_merge_plan(plan, approve_review=True)
            markdown = result.to_markdown()

        self.assertIn("# Apply Report", markdown)
        self.assertIn("review_approved=true", markdown)
        self.assertIn("- created.txt", markdown)


if __name__ == "__main__":
    unittest.main()
