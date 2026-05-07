from __future__ import annotations

import unittest
from pathlib import Path

from nemo_coding_platform.core.workspace import (
    NEMOCODE_VIRTUAL_WORKSPACE,
    Workspace,
    mask_host_paths,
)


class TestMaskHostPaths(unittest.TestCase):
    def _workspace(self, path: str) -> Workspace:
        return Workspace(root=Path(path))

    def test_masks_windows_backslash_path(self) -> None:
        ws = self._workspace(r"C:\dev\dev4")
        output = r"Error in C:\dev\dev4\src\foo.py line 5"
        result = mask_host_paths(output, ws)
        self.assertNotIn(r"C:\dev\dev4", result)
        self.assertIn(NEMOCODE_VIRTUAL_WORKSPACE, result)
        # The filename should still be present somewhere in the result
        self.assertIn("foo.py", result)

    def test_masks_posix_slash_path(self) -> None:
        ws = self._workspace(r"C:\dev\dev4")
        output = "File changed: C:/dev/dev4/src/bar.py"
        result = mask_host_paths(output, ws)
        self.assertNotIn("C:/dev/dev4", result)
        self.assertIn(NEMOCODE_VIRTUAL_WORKSPACE, result)

    def test_masks_exact_root_path(self) -> None:
        ws = self._workspace(r"C:\dev\dev4")
        output = r"Working directory: C:\dev\dev4"
        result = mask_host_paths(output, ws)
        self.assertEqual(result, f"Working directory: {NEMOCODE_VIRTUAL_WORKSPACE}")

    def test_no_false_positives(self) -> None:
        ws = self._workspace(r"C:\dev\dev4")
        output = "No paths here. Just regular text."
        result = mask_host_paths(output, ws)
        self.assertEqual(result, output)

    def test_empty_output(self) -> None:
        ws = self._workspace(r"C:\dev\dev4")
        self.assertEqual(mask_host_paths("", ws), "")

    def test_multiple_occurrences(self) -> None:
        ws = self._workspace(r"C:\dev\dev4")
        output = r"C:\dev\dev4\a.py and C:\dev\dev4\b.py both changed"
        result = mask_host_paths(output, ws)
        self.assertNotIn(r"C:\dev\dev4", result)
        self.assertEqual(result.count(NEMOCODE_VIRTUAL_WORKSPACE), 2)

    def test_preserves_unrelated_text(self) -> None:
        ws = self._workspace(r"C:\dev\dev4")
        output = r"OK: C:\dev\dev4\foo.py — status: passing"
        result = mask_host_paths(output, ws)
        self.assertIn("status: passing", result)


if __name__ == "__main__":
    unittest.main()
