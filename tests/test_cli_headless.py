import contextlib
import io
import json
import tempfile
import unittest
import sys
from pathlib import Path

from nemo_coding_platform.cli import DEFAULT_MEMORY_DB, main
from nemo_coding_platform.core.memory_persistence import PersistentMemoryStore


def _load_cli_payload(output: io.StringIO) -> dict[str, object]:
    """Parse JSON payload from CLI stdout, skipping any NEMO_EVENT: prefix lines."""
    payload_text = output.getvalue().strip()
    if payload_text:
        try:
            parsed = json.loads(payload_text)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            for line in reversed(payload_text.splitlines()):
                candidate = line.strip()
                if not candidate:
                    continue
                try:
                    parsed = json.loads(candidate)
                    if isinstance(parsed, dict):
                        return parsed
                except json.JSONDecodeError:
                    continue
    return {}


class CliHeadlessTests(unittest.TestCase):
    def test_handoff_plan_json(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["handoff-plan", "Build feature", "--json"])

        payload = _load_cli_payload(output)
        self.assertEqual(code, 0)
        self.assertTrue(payload["unattended"])
        self.assertTrue(payload["review_gate"])
        self.assertIn("write_nemo_memory", payload["steps"])

    def test_headless_run_json(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["headless-run", "Build feature", "--provider", "fake", "--json"])

        payload = _load_cli_payload(output)
        self.assertEqual(code, 0)
        self.assertEqual(payload["score"], 1.0)
        self.assertEqual(payload["provider"], "fake-space-code")
        self.assertIn("model", payload)  # model is auto-resolved, not hardcoded
        self.assertIn("generated-implementation.md", payload["changed_files"])
        self.assertGreaterEqual(payload["events"], 8)
        self.assertGreaterEqual(payload["artifacts"], 6)

    def test_headless_run_saves_json(self) -> None:
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/result.json"
            with contextlib.redirect_stdout(output):
                code = main(["headless-run", "Build feature", "--provider", "fake", "--json", "--save-json", path])
            payload = _load_cli_payload(output)
            with open(path, encoding="utf-8") as saved:
                saved_payload = json.load(saved)

        self.assertEqual(code, 0)
        self.assertEqual(payload["saved_json"], path)
        self.assertEqual(saved_payload["schema_version"], 1)

    def test_show_and_eval_run_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/result.json"
            with contextlib.redirect_stdout(io.StringIO()):
                main(["headless-run", "Build feature", "--provider", "fake", "--json", "--save-json", path])

            show_output = io.StringIO()
            with contextlib.redirect_stdout(show_output):
                show_code = main(["show-run-json", path, "--json"])
            show_payload = _load_cli_payload(show_output)

            eval_output = io.StringIO()
            with contextlib.redirect_stdout(eval_output):
                eval_code = main(["eval-run-json", path, "--json"])
            eval_payload = _load_cli_payload(eval_output)

        self.assertEqual(show_code, 0)
        self.assertEqual(eval_code, 0)
        self.assertEqual(show_payload["grade"], "ready")
        self.assertEqual(eval_payload["score"], 1.0)
        self.assertEqual(eval_payload["reasons"], [])

    def test_replay_run_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/result.json"
            with contextlib.redirect_stdout(io.StringIO()):
                main(["headless-run", "Build feature", "--provider", "fake", "--json", "--save-json", path])

            replay_output = io.StringIO()
            with contextlib.redirect_stdout(replay_output):
                code = main(["replay-run-json", path, "--json"])
            payload = _load_cli_payload(replay_output)

        self.assertEqual(code, 0)
        self.assertTrue(payload["can_replay"])
        self.assertIn("checkpoint.md", payload["payload_refs"])
        self.assertIn("generated-implementation.md", payload["changed_files"])

    def test_headless_run_subprocess_provider_detects_runtime_change(self) -> None:
        executable = sys.executable.replace("\\", "/")
        command = f"{executable} -c \"from pathlib import Path; Path('cli-subprocess.txt').write_text('ok', encoding='utf-8')\""
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main([
                "headless-run",
                "Build feature",
                "--provider",
                "subprocess",
                "--allow-non-mcp",
                "--engine-command",
                command,
                "--json",
            ])

        payload = _load_cli_payload(output)

        self.assertEqual(code, 0)
        self.assertEqual(payload["provider"], "subprocess-space-code")
        self.assertIn("cli-subprocess.txt", payload["changed_files"])

    def test_headless_run_accepts_target_file_flag(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["headless-run", "Build feature", "--provider", "fake", "--target-file", "src/demo.py", "--json"])

        payload = _load_cli_payload(output)

        self.assertEqual(code, 0)
        self.assertEqual(payload["provider"], "fake-space-code")

    def test_headless_run_accepts_validation_python_flag(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main([
                "headless-run",
                "Build feature",
                "--provider",
                "fake",
                "--real-validation",
                "--validation-python",
                "assert 2 + 2 == 4",
                "--json",
            ])

        payload = _load_cli_payload(output)

        self.assertEqual(code, 0)
        self.assertTrue(payload["validation_passed"])

    def test_headless_run_accepts_bounded_simulation_flag(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["headless-run", "Build feature", "--provider", "fake", "--bounded-simulation", "--json"])

        payload = _load_cli_payload(output)

        self.assertEqual(code, 0)
        self.assertGreaterEqual(payload["artifacts"], 9)
        self.assertEqual(payload["score"], 1.0)

    def test_headless_run_memory_db_persists_nemo_writebacks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory_db = Path(tmp) / "nemo-memory.sqlite"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["headless-run", "Build feature", "--provider", "fake", "--bounded-simulation", "--memory-db", str(memory_db), "--json"])
            payload = _load_cli_payload(output)
            store = PersistentMemoryStore(memory_db)
            atoms = store.search_atoms(limit=20)
            memory_db_exists = memory_db.exists()

        self.assertEqual(code, 0)
        self.assertEqual(payload["score"], 1.0)
        self.assertTrue(memory_db_exists)
        self.assertTrue(any("Checkpoint writeback prepared" in atom.atom.content for atom in atoms))
        self.assertTrue(any("Headless run review package created" in atom.atom.content for atom in atoms))

    def test_headless_run_uses_default_nemo_memory_db_from_start(self) -> None:
        memory_db = Path(DEFAULT_MEMORY_DB)
        if memory_db.exists():
            memory_db.unlink()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["headless-run", "Build feature", "--provider", "fake", "--json"])
        payload = _load_cli_payload(output)
        atoms = PersistentMemoryStore(memory_db).search_atoms(limit=20)

        self.assertEqual(code, 0)
        self.assertEqual(payload["score"], 1.0)
        self.assertTrue(memory_db.exists())
        self.assertTrue(any("Headless run review package created" in atom.atom.content for atom in atoms))

    def test_headless_run_can_opt_out_of_persistent_nemo_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory_db = Path(tmp) / "nemo-memory.sqlite"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["headless-run", "Build feature", "--provider", "fake", "--memory-db", str(memory_db), "--no-memory-db", "--json"])
            payload = _load_cli_payload(output)

        self.assertEqual(code, 0)
        self.assertEqual(payload["score"], 1.0)
        self.assertFalse(memory_db.exists())

    def test_llm_benchmark_json_with_fake_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = io.StringIO()
            save_path = Path(tmp) / "llm-benchmark.json"
            with contextlib.redirect_stdout(output):
                code = main([
                    "llm-benchmark",
                    "--repo",
                    tmp,
                    "--provider",
                    "fake",
                    "--repeats",
                    "1",
                    "--no-warmup",
                    "--no-memory-db",
                    "--save-json",
                    str(save_path),
                    "--json",
                ])
            payload = _load_cli_payload(output)

            self.assertEqual(code, 0)
            self.assertEqual(payload["provider"], "fake")
            self.assertEqual(payload["repeats"], 1)
            self.assertIn("summary", payload)
            self.assertEqual(payload["saved_json"], str(save_path))
            self.assertTrue(save_path.exists())

    def test_llm_benchmark_subprocess_requires_mcp_by_default(self) -> None:
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(output):
                code = main([
                    "llm-benchmark",
                    "--repo",
                    tmp,
                    "--provider",
                    "subprocess",
                    "--repeats",
                    "1",
                    "--no-warmup",
                    "--json",
                ])

        self.assertEqual(code, 1)
        self.assertIn("requires --mcp-url", output.getvalue())

    def test_llm_benchmark_fail_on_regression_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            baseline_path = Path(tmp) / "baseline.json"
            baseline_path.write_text(
                json.dumps(
                    {
                        "summary": {
                            "success_rate": 1.0,
                            "validation_pass_rate": 1.0,
                            "repair_success_rate": 1.0,
                            "noop_rate": 0.0,
                        }
                    }
                ),
                encoding="utf-8",
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main([
                    "llm-benchmark",
                    "--repo",
                    tmp,
                    "--provider",
                    "fake",
                    "--repeats",
                    "1",
                    "--no-warmup",
                    "--no-memory-db",
                    "--baseline-json",
                    str(baseline_path),
                    "--fail-on-regression",
                    "--json",
                ])
            payload = _load_cli_payload(output)

        self.assertEqual(code, 1)
        self.assertIn("regression_gate", payload)
        self.assertFalse(payload["regression_gate"]["passed"])
        self.assertGreaterEqual(len(payload["regression_gate"]["violations"]), 1)

    def test_llm_benchmark_fail_on_regression_passes_with_relaxed_thresholds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            baseline_path = Path(tmp) / "baseline.json"
            baseline_path.write_text(
                json.dumps(
                    {
                        "summary": {
                            "success_rate": 0.0,
                            "validation_pass_rate": 0.0,
                            "repair_success_rate": 1.0,
                            "noop_rate": 1.0,
                        }
                    }
                ),
                encoding="utf-8",
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main([
                    "llm-benchmark",
                    "--repo",
                    tmp,
                    "--provider",
                    "fake",
                    "--repeats",
                    "1",
                    "--no-warmup",
                    "--no-memory-db",
                    "--baseline-json",
                    str(baseline_path),
                    "--fail-on-regression",
                    "--min-repair-success-rate-delta",
                    "-1.0",
                    "--max-noop-rate-delta",
                    "1.0",
                    "--json",
                ])
            payload = _load_cli_payload(output)

        self.assertEqual(code, 0)
        self.assertIn("regression_gate", payload)
        self.assertTrue(payload["regression_gate"]["passed"])


if __name__ == "__main__":
    unittest.main()