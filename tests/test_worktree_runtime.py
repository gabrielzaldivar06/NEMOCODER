import tempfile
import unittest
from pathlib import Path

from nemo_coding_platform.core.worktree_runtime import (
    assert_not_main_workspace,
    create_worktree_runtime,
    initialize_runtime_dirs,
    require_merge_review,
    reset_runtime_dirs,
    snapshot_runtime_files,
    write_runtime_file,
)


class WorktreeRuntimeTests(unittest.TestCase):
    def test_runtime_path_is_derived_from_task_and_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spec = create_worktree_runtime("task 1", "run 1", tmp)

            self.assertEqual(spec.runtime_id, "task-1-run-1")
            self.assertEqual(spec.worktree_path, Path(tmp).resolve() / "task-1-run-1")

    def test_resolve_inside_rejects_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spec = create_worktree_runtime("task", "run", tmp)

            with self.assertRaises(ValueError):
                spec.resolve_inside("../outside.txt")

    def test_autonomous_runtime_cannot_target_main_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PermissionError):
                assert_not_main_workspace(tmp, tmp)

    def test_merge_requires_review(self) -> None:
        with self.assertRaises(PermissionError):
            require_merge_review(False)
        require_merge_review(True)

    def test_runtime_filesystem_operations_write_and_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spec = initialize_runtime_dirs(create_worktree_runtime("task", "run", tmp))
            write_runtime_file(spec, "src/app.py", "print('ok')\n")
            write_runtime_file(spec, ".venv/ignored.py", "ignored")

            self.assertEqual(snapshot_runtime_files(spec), ("src/app.py",))

    def test_reset_runtime_dirs_recreates_existing_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spec = initialize_runtime_dirs(create_worktree_runtime("task", "run", tmp))
            write_runtime_file(spec, "old.txt", "old")

            reset = reset_runtime_dirs(spec)

            self.assertEqual(reset.worktree_path, spec.worktree_path)
            self.assertTrue(reset.worktree_path.exists())
            self.assertFalse((reset.worktree_path / "old.txt").exists())

    def test_reset_runtime_dirs_falls_back_when_existing_runtime_is_locked(self) -> None:
        def locked_remover(_path):
            raise PermissionError("locked")

        with tempfile.TemporaryDirectory() as tmp:
            spec = initialize_runtime_dirs(create_worktree_runtime("task", "run", tmp))

            reset = reset_runtime_dirs(spec, remover=locked_remover)

            self.assertNotEqual(reset.worktree_path, spec.worktree_path)
            self.assertTrue(reset.runtime_id.startswith("task-run-"))
            self.assertTrue(reset.worktree_path.exists())


if __name__ == "__main__":
    unittest.main()