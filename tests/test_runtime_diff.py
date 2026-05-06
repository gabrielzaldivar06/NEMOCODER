import tempfile
import unittest
from pathlib import Path

from nemo_coding_platform.core.runtime_diff import diff_snapshots, snapshot_path


class RuntimeDiffTests(unittest.TestCase):
    def test_detects_created_and_updated_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "existing.txt").write_text("before\n", encoding="utf-8")
            before = snapshot_path(root)
            (root / "existing.txt").write_text("after\n", encoding="utf-8")
            (root / "created.txt").write_text("new\n", encoding="utf-8")
            after = snapshot_path(root)

        diff = diff_snapshots(before, after)

        self.assertEqual(diff.created, ("created.txt",))
        self.assertEqual(diff.updated, ("existing.txt",))
        self.assertIn("after/existing.txt", diff.unified_diff)
        self.assertIn("after/created.txt", diff.unified_diff)

    def test_detects_deleted_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "gone.txt").write_text("bye\n", encoding="utf-8")
            before = snapshot_path(root)
            (root / "gone.txt").unlink()
            after = snapshot_path(root)

        diff = diff_snapshots(before, after)

        self.assertEqual(diff.deleted, ("gone.txt",))
        self.assertIn("before/gone.txt", diff.unified_diff)

    def test_ignores_aider_operational_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            before = snapshot_path(root)
            (root / ".aider.chat.history.md").write_text("history\n", encoding="utf-8")
            (root / "real-change.txt").write_text("ok\n", encoding="utf-8")
            after = snapshot_path(root)

        diff = diff_snapshots(before, after)

        self.assertEqual(diff.created, ("real-change.txt",))
        self.assertNotIn(".aider.chat.history.md", diff.changed_files)


if __name__ == "__main__":
    unittest.main()
