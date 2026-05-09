import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

from nemo_coding_platform.core.review_gate import apply_merge_plan, build_merge_plan, rollback_apply_result


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
        self.assertEqual(plan.files[0].source_hash, sha256(b"new").hexdigest())
        self.assertEqual(plan.files[0].target_hash, sha256(b"old").hexdigest())

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

    def test_plan_blocks_simulated_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            repo.mkdir()
            sandbox.mkdir()
            (sandbox / "created.txt").write_text("created", encoding="utf-8")
            payload = ready_payload(repo, sandbox, ["created.txt"])
            payload["validation"] = {
                "results": [
                    {"status": "passed", "returncode": None, "output": "simulated pass"},
                ]
            }

            plan = build_merge_plan(payload)

        self.assertFalse(plan.mergeable)
        self.assertIn("simulated_validation", plan.risk_flags)

    def test_plan_blocks_context_window_exceeded_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            repo.mkdir()
            sandbox.mkdir()
            (sandbox / "created.txt").write_text("created", encoding="utf-8")
            payload = ready_payload(repo, sandbox, ["created.txt"])
            payload["mutation_result"] = {
                "changed_files": ["created.txt"],
                "stdout": "MidStreamFallbackError: Context size has been exceeded",
                "stderr": "",
            }

            plan = build_merge_plan(payload)

        self.assertFalse(plan.mergeable)
        self.assertIn("context_window_exceeded", plan.risk_flags)

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

    def test_plan_flags_protected_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            repo.mkdir()
            sandbox.mkdir()
            (sandbox / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")

            plan = build_merge_plan(ready_payload(repo, sandbox, ["pyproject.toml"]))

        self.assertFalse(plan.mergeable)
        self.assertIn("protected_path:pyproject.toml", plan.risk_flags)
        self.assertIn("protected_path:pyproject.toml", plan.files[0].risk_flags)

    def test_plan_flags_binary_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            repo.mkdir()
            sandbox.mkdir()
            (sandbox / "asset.bin").write_bytes(b"abc\0def")

            plan = build_merge_plan(ready_payload(repo, sandbox, ["asset.bin"]))

        self.assertFalse(plan.mergeable)
        self.assertIn("binary_file:asset.bin", plan.risk_flags)
        self.assertIn("binary_file:asset.bin", plan.files[0].risk_flags)

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

    def test_trusted_autonomy_applies_ready_risk_free_plan_without_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            repo.mkdir()
            sandbox.mkdir()
            (sandbox / "created.txt").write_text("created", encoding="utf-8")
            plan = build_merge_plan(ready_payload(repo, sandbox, ["created.txt"]))

            result = apply_merge_plan(plan, autonomy_profile="trusted")

        self.assertEqual(result.applied_files, ("created.txt",))
        self.assertFalse(result.review_approved)
        self.assertTrue(result.auto_applied)
        self.assertEqual(result.autonomy_profile, "trusted")

    def test_trusted_autonomy_does_not_apply_risky_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            repo.mkdir()
            sandbox.mkdir()
            (sandbox / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
            plan = build_merge_plan(ready_payload(repo, sandbox, ["pyproject.toml"]))

            with self.assertRaises(PermissionError) as raised:
                apply_merge_plan(plan, autonomy_profile="trusted")

        self.assertIn("merge_plan_not_mergeable", str(raised.exception))

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

    def test_apply_backs_up_updated_files_before_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            repo.mkdir()
            sandbox.mkdir()
            (repo / "existing.txt").write_text("old", encoding="utf-8")
            (sandbox / "existing.txt").write_text("new", encoding="utf-8")
            plan = build_merge_plan(ready_payload(repo, sandbox, ["existing.txt"]))

            result = apply_merge_plan(plan, approve_review=True)

            backup_path = Path(result.backup_dir or "") / "existing.txt"
            backup_content = backup_path.read_text(encoding="utf-8")
            repo_content = (repo / "existing.txt").read_text(encoding="utf-8")

        self.assertEqual(result.backup_files, ("existing.txt",))
        self.assertEqual(backup_content, "old")
        self.assertEqual(repo_content, "new")

    def test_apply_blocks_if_target_changed_after_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            repo.mkdir()
            sandbox.mkdir()
            (repo / "existing.txt").write_text("old", encoding="utf-8")
            (sandbox / "existing.txt").write_text("new", encoding="utf-8")
            plan = build_merge_plan(ready_payload(repo, sandbox, ["existing.txt"]))
            (repo / "existing.txt").write_text("changed by human", encoding="utf-8")

            with self.assertRaises(PermissionError) as raised:
                apply_merge_plan(plan, approve_review=True)

        self.assertIn("target changed", str(raised.exception))

    def test_rollback_restores_updates_and_deletes_creates(self) -> None:
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
            applied = apply_merge_plan(plan, approve_review=True)

            rollback = rollback_apply_result(applied, approve_review=True)
            existing_content = (repo / "existing.txt").read_text(encoding="utf-8")
            created_exists = (repo / "created.txt").exists()

        self.assertEqual(rollback.restored_files, ("existing.txt",))
        self.assertEqual(rollback.deleted_files, ("created.txt",))
        self.assertEqual(existing_content, "old")
        self.assertFalse(created_exists)

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
        self.assertIn("## Backups", markdown)


if __name__ == "__main__":
    unittest.main()
