import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from nemo_coding_platform.cli import main
from nemo_coding_platform.core.memory import MemoryAtomType
from nemo_coding_platform.core.memory_persistence import PersistentMemoryStore


def write_ready_run(path: Path, repo: Path, sandbox: Path, changed_files: list[str]) -> None:
    payload = {
        "task": {"id": "task-1", "repo_path": str(repo)},
        "run": {"id": "run-1", "sandbox_path": str(sandbox)},
        "timeline": [{"kind": "checkpoint"}, {"kind": "memory_written"}],
        "artifacts": [{"artifact_type": "review_package"}],
        "validation": {"results": [{"status": "passed"}]},
        "mutation_result": {"changed_files": changed_files},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


class ReviewGateCliTests(unittest.TestCase):
    def test_review_run_json_outputs_merge_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            repo.mkdir()
            sandbox.mkdir()
            (sandbox / "created.txt").write_text("created", encoding="utf-8")
            run_json = root / "run.json"
            write_ready_run(run_json, repo, sandbox, ["created.txt"])
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                code = main(["review-run-json", str(run_json), "--json"])
            payload = json.loads(output.getvalue())

        self.assertEqual(code, 0)
        self.assertTrue(payload["mergeable"])
        self.assertEqual(payload["files"][0]["operation"], "create")

    def test_review_run_json_saves_merge_plan_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            repo.mkdir()
            sandbox.mkdir()
            (sandbox / "created.txt").write_text("created", encoding="utf-8")
            run_json = root / "run.json"
            plan_path = root / "merge-plan.md"
            write_ready_run(run_json, repo, sandbox, ["created.txt"])

            with contextlib.redirect_stdout(io.StringIO()):
                code = main(["review-run-json", str(run_json), "--save-plan", str(plan_path), "--json"])

            content = plan_path.read_text(encoding="utf-8")

        self.assertEqual(code, 0)
        self.assertIn("# Merge Plan", content)
        self.assertIn("create: created.txt", content)

    def test_apply_run_json_blocks_without_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            repo.mkdir()
            sandbox.mkdir()
            (sandbox / "created.txt").write_text("created", encoding="utf-8")
            run_json = root / "run.json"
            write_ready_run(run_json, repo, sandbox, ["created.txt"])
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                code = main(["apply-run-json", str(run_json), "--json"])
            payload = json.loads(output.getvalue())

        self.assertEqual(code, 1)
        self.assertIn("review approval required", payload["error"])
        self.assertFalse((repo / "created.txt").exists())

    def test_apply_run_json_applies_after_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            repo.mkdir()
            sandbox.mkdir()
            (sandbox / "created.txt").write_text("created", encoding="utf-8")
            run_json = root / "run.json"
            write_ready_run(run_json, repo, sandbox, ["created.txt"])
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                code = main(["apply-run-json", str(run_json), "--approve-review", "--json"])
            payload = json.loads(output.getvalue())

            content = (repo / "created.txt").read_text(encoding="utf-8")

        self.assertEqual(code, 0)
        self.assertEqual(payload["applied_files"], ["created.txt"])
        self.assertEqual(content, "created")

    def test_apply_run_json_writes_update_backup_to_requested_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            backup_dir = root / "backups"
            repo.mkdir()
            sandbox.mkdir()
            (repo / "existing.txt").write_text("old", encoding="utf-8")
            (sandbox / "existing.txt").write_text("new", encoding="utf-8")
            run_json = root / "run.json"
            write_ready_run(run_json, repo, sandbox, ["existing.txt"])
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                code = main(["apply-run-json", str(run_json), "--approve-review", "--backup-dir", str(backup_dir), "--json"])
            payload = json.loads(output.getvalue())
            backup_content = (backup_dir / "existing.txt").read_text(encoding="utf-8")
            repo_content = (repo / "existing.txt").read_text(encoding="utf-8")

        self.assertEqual(code, 0)
        self.assertEqual(payload["backup_files"], ["existing.txt"])
        self.assertEqual(backup_content, "old")
        self.assertEqual(repo_content, "new")

    def test_apply_run_json_writes_apply_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            repo.mkdir()
            sandbox.mkdir()
            (sandbox / "created.txt").write_text("created", encoding="utf-8")
            run_json = root / "run.json"
            report_path = root / "apply-report.md"
            write_ready_run(run_json, repo, sandbox, ["created.txt"])

            with contextlib.redirect_stdout(io.StringIO()):
                code = main(["apply-run-json", str(run_json), "--approve-review", "--save-apply-report", str(report_path), "--json"])

            content = report_path.read_text(encoding="utf-8")

        self.assertEqual(code, 0)
        self.assertIn("# Apply Report", content)
        self.assertIn("- created.txt", content)

    def test_apply_run_json_saves_apply_json_for_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            repo.mkdir()
            sandbox.mkdir()
            (repo / "existing.txt").write_text("old", encoding="utf-8")
            (sandbox / "existing.txt").write_text("new", encoding="utf-8")
            (sandbox / "created.txt").write_text("created", encoding="utf-8")
            run_json = root / "run.json"
            apply_json = root / "apply.json"
            write_ready_run(run_json, repo, sandbox, ["existing.txt", "created.txt"])

            with contextlib.redirect_stdout(io.StringIO()):
                code = main(["apply-run-json", str(run_json), "--approve-review", "--save-apply-json", str(apply_json), "--json"])
            payload = json.loads(apply_json.read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        self.assertEqual(payload["updated_files"], ["existing.txt"])
        self.assertEqual(payload["created_files"], ["created.txt"])
        self.assertEqual(payload["repo_path"], str(repo))

    def test_rollback_apply_json_restores_and_deletes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            repo.mkdir()
            sandbox.mkdir()
            (repo / "existing.txt").write_text("old", encoding="utf-8")
            (sandbox / "existing.txt").write_text("new", encoding="utf-8")
            (sandbox / "created.txt").write_text("created", encoding="utf-8")
            run_json = root / "run.json"
            apply_json = root / "apply.json"
            report_path = root / "rollback-report.md"
            write_ready_run(run_json, repo, sandbox, ["existing.txt", "created.txt"])
            with contextlib.redirect_stdout(io.StringIO()):
                main(["apply-run-json", str(run_json), "--approve-review", "--save-apply-json", str(apply_json), "--json"])

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["rollback-apply-json", str(apply_json), "--approve-review", "--save-rollback-report", str(report_path), "--json"])
            payload = json.loads(output.getvalue())
            existing_content = (repo / "existing.txt").read_text(encoding="utf-8")
            created_exists = (repo / "created.txt").exists()
            report = report_path.read_text(encoding="utf-8")

        self.assertEqual(code, 0)
        self.assertEqual(payload["restored_files"], ["existing.txt"])
        self.assertEqual(payload["deleted_files"], ["created.txt"])
        self.assertEqual(existing_content, "old")
        self.assertFalse(created_exists)
        self.assertIn("# Rollback Report", report)

    def test_apply_run_json_writes_nemo_memory_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            memory_db = root / "memory.sqlite"
            repo.mkdir()
            sandbox.mkdir()
            (sandbox / "created.txt").write_text("created", encoding="utf-8")
            run_json = root / "run.json"
            write_ready_run(run_json, repo, sandbox, ["created.txt"])

            with contextlib.redirect_stdout(io.StringIO()):
                code = main(["apply-run-json", str(run_json), "--approve-review", "--memory-db", str(memory_db), "--json"])
            atoms = PersistentMemoryStore(memory_db).search_atoms(topic="Review-To-Main Gate", limit=10)

        self.assertEqual(code, 0)
        self.assertTrue(any(atom.atom.atom_type == MemoryAtomType.DECISION for atom in atoms))
        self.assertTrue(any("Review-to-main apply completed" in atom.atom.content for atom in atoms))

    def test_apply_run_json_can_skip_nemo_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            memory_db = root / "memory.sqlite"
            repo.mkdir()
            sandbox.mkdir()
            (sandbox / "created.txt").write_text("created", encoding="utf-8")
            run_json = root / "run.json"
            write_ready_run(run_json, repo, sandbox, ["created.txt"])

            with contextlib.redirect_stdout(io.StringIO()):
                code = main(["apply-run-json", str(run_json), "--approve-review", "--memory-db", str(memory_db), "--no-memory-db", "--json"])

        self.assertEqual(code, 0)
        self.assertFalse(memory_db.exists())


if __name__ == "__main__":
    unittest.main()
