import unittest
import os
import tempfile
from pathlib import Path
from nemo_coding_platform.core.post_mutation_lint import lint_python_compile, lint_changed_files, format_lint_evidence


class TestPostMutationLint(unittest.TestCase):
    def test_lint_python_compile_valid(self):
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w") as f:
            f.write("def hello():\n    print('world')\n")
            fname = f.name
        
        try:
            res = lint_python_compile(fname, "hello.py")
            self.assertIsNone(res)
        finally:
            os.unlink(fname)

    def test_lint_python_compile_invalid(self):
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w") as f:
            # Syntax error: missing colon
            f.write("def hello()\n    print('world')\n")
            fname = f.name
        
        try:
            res = lint_python_compile(fname, "hello.py")
            self.assertIsNotNone(res)
            self.assertIn("SyntaxError", res.errors)
            self.assertIn("hello.py", res.file)
            self.assertEqual(res.line_numbers[0], 1)
        finally:
            os.unlink(fname)

    def test_lint_changed_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            valid_py = base / "valid.py"
            invalid_py = base / "invalid.py"
            valid_py.write_text("x = 1")
            invalid_py.write_text("x = ") # Syntax error
            
            results = lint_changed_files(("valid.py", "invalid.py", "other.txt"), tmpdir)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].file, "invalid.py")

    def test_format_lint_evidence(self):
        from nemo_coding_platform.core.post_mutation_lint import LintResult
        results = [
            LintResult(file="bad.py", errors="Error on line 1", line_numbers=(1,))
        ]
        evidence = format_lint_evidence(results)
        self.assertIn("# Linting Errors Found", evidence)
        self.assertIn("bad.py", evidence)
        self.assertIn("Error on line 1", evidence)


if __name__ == "__main__":
    unittest.main()
