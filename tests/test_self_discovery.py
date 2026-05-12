import json
import tempfile
import unittest
from pathlib import Path

from nemo_coding_platform.core.permission_engine import PermissionAction, load_ruleset_from_file
from nemo_coding_platform.core.self_discovery import ensure_self_mod_permissions_file, find_spacecode_repo, is_spacecode_repo


class SelfDiscoveryTests(unittest.TestCase):
    def test_find_spacecode_repo_from_nested_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src" / "nemo_coding_platform" / "core").mkdir(parents=True)
            (root / "tests").mkdir()
            (root / "pyproject.toml").write_text("[project]\nname = 'nemo_coding_platform'\n", encoding="utf-8")

            found = find_spacecode_repo(root / "src" / "nemo_coding_platform" / "core")

        self.assertEqual(found, root.resolve())

    def test_is_spacecode_repo_requires_package_tests_and_pyproject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertFalse(is_spacecode_repo(root))
            (root / "src" / "nemo_coding_platform").mkdir(parents=True)
            (root / "tests").mkdir()
            (root / "pyproject.toml").write_text("name = 'nemo_coding_platform'", encoding="utf-8")

            self.assertTrue(is_spacecode_repo(root))

    def test_self_mod_permission_template_blocks_sensitive_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = ensure_self_mod_permissions_file(tmp)
            data = json.loads(path.read_text(encoding="utf-8"))
            ruleset = load_ruleset_from_file(path)

        self.assertIn(".git/**", data["write_file"])
        self.assertEqual(ruleset.evaluate("write_file", "src/nemo_coding_platform/core/example.py"), PermissionAction.ALLOW)
        self.assertEqual(ruleset.evaluate("write_file", ".git/config"), PermissionAction.DENY)
        self.assertEqual(ruleset.evaluate("write_file", ".env"), PermissionAction.DENY)


if __name__ == "__main__":
    unittest.main()