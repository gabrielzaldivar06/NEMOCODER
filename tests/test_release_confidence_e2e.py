import json
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from nemo_coding_platform.cli import main
from nemo_coding_platform.mission_control_server import (
    MissionControlServerConfig,
    api_apply,
    api_apply_selection,
    api_applies,
    api_file,
    api_review,
    api_rollback,
    api_state,
)
from tests.test_review_gate_cli import write_ready_run
from tests.generate_release_confidence_evidence import build_release_confidence_evidence


class ReleaseConfidenceE2ETests(unittest.TestCase):
    def test_canonical_review_apply_rollback_flow_is_traceable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            runtimes = root / "runtimes"
            apply_results = root / "apply-results"
            repo.mkdir()
            sandbox.mkdir()
            runtimes.mkdir()

            (repo / "existing.txt").write_text("old\n", encoding="utf-8")
            (sandbox / "existing.txt").write_text("new\n", encoding="utf-8")
            (sandbox / "created.txt").write_text("created\n", encoding="utf-8")

            run_json = runtimes / "run.json"
            write_ready_run(run_json, repo, sandbox, ["existing.txt", "created.txt"])
            config = MissionControlServerConfig.from_paths(repo, runtimes, apply_results, runtimes)

            review_payload = api_review(config, {"source_json": str(run_json)})
            self.assertTrue(review_payload["ok"])
            self.assertTrue(review_payload["plan"]["mergeable"])
            self.assertEqual(
                set(review_payload["plan"]["changed_files"]),
                {"existing.txt", "created.txt"},
            )

            file_payload = api_file(
                config,
                {"source_json": str(run_json), "file_path": "existing.txt"},
            )
            self.assertEqual(file_payload["operation"], "update")
            self.assertTrue(file_payload["hunks"])

            apply_payload = api_apply(
                config,
                {"source_json": str(run_json), "approve_review": True},
            )
            self.assertTrue(apply_payload["ok"])

            apply_json = Path(apply_payload["apply_json"])
            self.assertTrue(apply_json.exists())
            apply_json_payload = json.loads(apply_json.read_text(encoding="utf-8"))
            self.assertEqual(set(apply_json_payload["applied_files"]), {"existing.txt", "created.txt"})

            self.assertEqual((repo / "existing.txt").read_text(encoding="utf-8"), "new\n")
            self.assertTrue((repo / "created.txt").exists())

            applies_payload = api_applies(config)
            self.assertTrue(applies_payload["ok"])
            self.assertTrue(any(item.get("path") == str(apply_json) for item in applies_payload["applies"]))

            rollback_payload = api_rollback(
                config,
                {"apply_json": str(apply_json), "approve_review": True},
            )
            self.assertTrue(rollback_payload["ok"])
            self.assertEqual(rollback_payload["result"]["restored_files"], ["existing.txt"])
            self.assertEqual(rollback_payload["result"]["deleted_files"], ["created.txt"])

            self.assertEqual((repo / "existing.txt").read_text(encoding="utf-8"), "old\n")
            self.assertFalse((repo / "created.txt").exists())

            state_payload = api_state(config)
            self.assertIn("runs", state_payload)
            self.assertGreaterEqual(len(state_payload["runs"]), 1)

    def test_partial_selection_flow_is_replayable_and_rollback_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            runtimes = root / "runtimes"
            apply_results = root / "apply-results"
            repo.mkdir()
            sandbox.mkdir()
            runtimes.mkdir()

            original = [f"line {index}" for index in range(1, 26)]
            changed = list(original)
            changed[1] = "line 2 changed"
            changed[19] = "line 20 changed"

            (repo / "existing.txt").write_text("\n".join(original) + "\n", encoding="utf-8")
            (sandbox / "existing.txt").write_text("\n".join(changed) + "\n", encoding="utf-8")

            run_json = runtimes / "run.json"
            write_ready_run(run_json, repo, sandbox, ["existing.txt"])
            config = MissionControlServerConfig.from_paths(repo, runtimes, apply_results, None)

            preview = api_file(config, {"source_json": str(run_json), "file_path": "existing.txt"})
            self.assertEqual(len(preview["hunks"]), 2)

            apply_payload = api_apply_selection(
                config,
                {
                    "source_json": str(run_json),
                    "approve_review": True,
                    "accepted_hunks": {"existing.txt": [preview["hunks"][0]["id"]]},
                },
            )
            apply_json = Path(apply_payload["apply_json"])
            self.assertTrue(apply_json.exists())

            content_after_apply = (repo / "existing.txt").read_text(encoding="utf-8")
            self.assertIn("line 2 changed", content_after_apply)
            self.assertNotIn("line 20 changed", content_after_apply)

            rollback_payload = api_rollback(
                config,
                {"apply_json": str(apply_json), "approve_review": True},
            )

            self.assertEqual(rollback_payload["result"]["restored_files"], ["existing.txt"])
            self.assertEqual(rollback_payload["result"]["deleted_files"], [])
            self.assertEqual(
                (repo / "existing.txt").read_text(encoding="utf-8"),
                "\n".join(original) + "\n",
            )

    def test_release_confidence_replay_gate_uses_persisted_headless_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_json = Path(tmp) / "result.json"
            with contextlib.redirect_stdout(io.StringIO()):
                headless_code = main([
                    "headless-run",
                    "Release confidence replay gate",
                    "--provider",
                    "fake",
                    "--json",
                    "--save-json",
                    str(run_json),
                ])

            replay_output = io.StringIO()
            with contextlib.redirect_stdout(replay_output):
                replay_code = main(["replay-run-json", str(run_json), "--json"])
            replay_payload = json.loads(replay_output.getvalue())

        self.assertEqual(headless_code, 0)
        self.assertEqual(replay_code, 0)
        self.assertTrue(replay_payload["can_replay"])
        self.assertEqual(replay_payload["grade"], "ready")
        self.assertGreaterEqual(replay_payload["event_count"], 1)

    def test_release_confidence_evidence_includes_benchmark_regression_gate(self) -> None:
        payload = build_release_confidence_evidence()
        evidence = payload["release_confidence"]
        benchmark_gate = evidence["benchmark_gate"]

        self.assertEqual(benchmark_gate["baseline_exit_code"], 0)
        self.assertEqual(benchmark_gate["pass_case_exit_code"], 0)
        self.assertTrue(benchmark_gate["pass_case_passed"])
        self.assertEqual(benchmark_gate["fail_case_exit_code"], 1)
        self.assertFalse(benchmark_gate["fail_case_passed"])
        self.assertGreaterEqual(benchmark_gate["fail_case_violation_count"], 1)


if __name__ == "__main__":
    unittest.main()
