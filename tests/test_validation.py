import sys
import tempfile
import unittest

from pathlib import Path

from nemo_coding_platform.core.validation import format_validation_report, run_validation_suite, simulate_validation, write_python_validation_script


class ValidationTests(unittest.TestCase):
    def test_simulated_validation_passes_by_default(self) -> None:
        result = simulate_validation(("python -m unittest",))

        self.assertTrue(result.passed)
        self.assertIn("passed=1/1", result.summary())

    def test_simulated_validation_can_fail(self) -> None:
        result = simulate_validation(("python -m unittest",), ("python -m unittest",))

        self.assertFalse(result.passed)

    def test_real_validation_runner_captures_success_and_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_validation_suite(
                (
                    f"{sys.executable} --version",
                    f"{sys.executable} -c raise SystemExit(1)",
                ),
                cwd=tmp,
            )

        self.assertFalse(result.passed)
        self.assertTrue(result.results[0].passed)
        self.assertFalse(result.results[1].passed)

    def test_real_validation_runner_rejects_shell_mode(self) -> None:
        with self.assertRaises(ValueError):
            run_validation_suite(("echo ok",), allow_shell=True)

    def test_python_validation_script_helper_writes_reproducible_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            command = write_python_validation_script(tmp, "validate_math.py", "assert 2 + 3 == 5\n", sys.executable)
            result = run_validation_suite((command,), cwd=tmp)

            self.assertTrue(result.passed)
            self.assertTrue((Path(tmp) / "validate_math.py").exists())
            self.assertIn("validate_math.py", command)

    def test_validation_report_includes_command_returncode_and_output(self) -> None:
        result = simulate_validation(("python validate.py",))
        report = format_validation_report(result)

        self.assertIn("python validate.py", report)
        self.assertIn("returncode=None", report)
        self.assertIn("simulated pass", report)


if __name__ == "__main__":
    unittest.main()