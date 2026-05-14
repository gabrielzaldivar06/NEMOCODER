import subprocess
import tempfile
import unittest
from pathlib import Path

from nemo_coding_platform.core.worktree_runtime import (
    assert_not_main_workspace,
    cleanup_git_worktree,
    create_git_worktree,
    create_worktree_runtime,
    initialize_git_worktree_runtime,
    initialize_runtime_dirs,
    merge_worktree_to_main,
    require_merge_review,
    reset_runtime_dirs,
    snapshot_runtime_files,
    worktree_branch_name,
    worktree_diff,
    write_runtime_file,
)


def _make_git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with one commit at tmp_path."""
    subprocess.run(["git", "init", "-b", "main"], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), check=True, capture_output=True)
    (tmp_path / "README.md").write_text("hello")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), check=True, capture_output=True)
    return tmp_path


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


class WorktreeBranchNameTests(unittest.TestCase):
    def test_branch_name_format(self) -> None:
        self.assertEqual(worktree_branch_name("task-1-run-1"), "wt/task-1-run-1")

    def test_branch_name_passes_through_runtime_id(self) -> None:
        self.assertEqual(worktree_branch_name("a-b-c"), "wt/a-b-c")


class CreateWorktreeRuntimeUseGitTests(unittest.TestCase):
    def test_use_git_places_worktree_under_repo_root_dot_worktrees(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "runtimes" / "mission-control"
            runtime_root.mkdir(parents=True)
            spec = create_worktree_runtime("task1", "run1", runtime_root, use_git=True)
            # worktree_path should be repo_root/.worktrees/<runtime_id>
            # repo_root = runtime_root.parent.parent = tmp
            expected_base = Path(tmp) / ".worktrees"
            self.assertEqual(spec.worktree_path.parent, expected_base.resolve())
            self.assertEqual(spec.runtime_id, "task1-run1")

    def test_use_git_false_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spec = create_worktree_runtime("t", "r", tmp)
            self.assertEqual(spec.worktree_path, Path(tmp).resolve() / "t-r")


class GitWorktreeFunctionTests(unittest.TestCase):
    def test_create_git_worktree_creates_branch_and_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_git_repo(Path(tmp))
            wt_path = repo / ".worktrees" / "task1-run1"
            branch = "wt/task1-run1"
            create_git_worktree(repo, wt_path, branch)

            self.assertTrue(wt_path.is_dir())
            self.assertTrue((wt_path / "README.md").exists())
            result = subprocess.run(
                ["git", "branch"], cwd=str(repo), capture_output=True, text=True
            )
            self.assertIn(branch, result.stdout)

    def test_worktree_diff_shows_changes_in_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_git_repo(Path(tmp))
            wt_path = repo / ".worktrees" / "task1-run1"
            branch = "wt/task1-run1"
            create_git_worktree(repo, wt_path, branch)

            (wt_path / "new_file.py").write_text("x = 1\n")
            subprocess.run(["git", "add", "new_file.py"], cwd=str(wt_path), check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "add file"], cwd=str(wt_path), check=True, capture_output=True)

            diff = worktree_diff(repo, branch, base_branch="main")
            self.assertIn("new_file.py", diff)
            self.assertIn("+x = 1", diff)

    def test_worktree_diff_empty_when_no_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_git_repo(Path(tmp))
            wt_path = repo / ".worktrees" / "task1-run1"
            branch = "wt/task1-run1"
            create_git_worktree(repo, wt_path, branch)

            diff = worktree_diff(repo, branch, base_branch="main")
            self.assertEqual(diff, "")

    def test_merge_worktree_to_main_applies_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_git_repo(Path(tmp))
            wt_path = repo / ".worktrees" / "task1-run1"
            branch = "wt/task1-run1"
            create_git_worktree(repo, wt_path, branch)

            (wt_path / "merged.py").write_text("result = 42\n")
            subprocess.run(["git", "add", "merged.py"], cwd=str(wt_path), check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "add merged"], cwd=str(wt_path), check=True, capture_output=True)

            ok = merge_worktree_to_main(repo, branch, "test merge")
            self.assertTrue(ok)
            self.assertTrue((repo / "merged.py").exists())

    def test_cleanup_git_worktree_removes_dir_and_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_git_repo(Path(tmp))
            wt_path = repo / ".worktrees" / "task1-run1"
            branch = "wt/task1-run1"
            create_git_worktree(repo, wt_path, branch)

            cleanup_git_worktree(repo, wt_path, branch)

            self.assertFalse(wt_path.exists())
            result = subprocess.run(
                ["git", "branch"], cwd=str(repo), capture_output=True, text=True
            )
            self.assertNotIn(branch, result.stdout)

    def test_initialize_git_worktree_runtime_returns_spec(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_git_repo(Path(tmp))
            runtime_root = repo / ".spacecode-runtimes" / "mission-control"
            runtime_root.mkdir(parents=True)
            spec = create_worktree_runtime("task1", "run1", runtime_root, use_git=True)
            branch = worktree_branch_name(spec.runtime_id)

            returned = initialize_git_worktree_runtime(spec, repo, branch)
            self.assertIs(returned, spec)
            self.assertTrue(spec.worktree_path.is_dir())


if __name__ == "__main__":
    unittest.main()