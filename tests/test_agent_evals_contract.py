import json
import tempfile
import unittest
from pathlib import Path

from nemo_coding_platform.core.evals import score_headless_result
from nemo_coding_platform.core.persistence import build_replay_summary, export_run_metrics, load_headless_result_json
from tests.test_review_gate import ready_payload


class AgentEvalsContractTests(unittest.TestCase):
    def test_replay_fixture_loads_from_json_and_builds_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            repo.mkdir()
            sandbox.mkdir()
            (sandbox / "created.txt").write_text("created", encoding="utf-8")
            payload = ready_payload(repo, sandbox, ["created.txt"])
            payload["timeline"].append({"sequence": 1, "phase": "review", "kind": "review_package_created", "summary": "Created review package"})
            payload["artifacts"].append({"artifact_type": "review_package", "path": "review-package.md", "summary": "review package"})
            fixture = root / "replay-fixture.json"
            fixture.write_text(json.dumps(payload), encoding="utf-8")

            loaded = load_headless_result_json(fixture)
            summary = build_replay_summary(loaded)

        self.assertEqual(summary["task_id"], "task-1")
        self.assertEqual(summary["run_id"], "run-1")
        self.assertIn("events", summary)
        self.assertIn("artifact_paths", summary)

    def test_run_log_can_be_scored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            repo.mkdir()
            sandbox.mkdir()
            payload = ready_payload(repo, sandbox, ["created.txt"])

            score = score_headless_result(payload)

        self.assertIn(score.grade, {"ready", "needs_review", "blocked"})
        self.assertGreaterEqual(score.score, 0.0)
        self.assertLessEqual(score.score, 1.0)

    def test_metrics_export_includes_fr10_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            repo.mkdir()
            sandbox.mkdir()
            payload = ready_payload(repo, sandbox, ["created.txt"])
            payload["timeline"] = [
                {"kind": "permission_decided", "summary": "approved"},
                {"kind": "checkpoint", "summary": "checkpoint"},
                {"kind": "memory_written", "summary": "memory"},
            ]
            payload["validation"] = {"results": [{"status": "passed", "summary": "ok", "command": {"command": "python -m unittest"}}]}
            payload["mutation_result"] = {"returncode": 0, "changed_files": ["created.txt"]}

            report = export_run_metrics(payload)

        self.assertIn("success_rate", report)
        self.assertIn("validation_pass_rate", report)
        self.assertIn("override_rate", report)
        self.assertIn("stale_memory_incidents", report)
        self.assertIn("tool_failure_rate", report)
        self.assertGreaterEqual(report["success_rate"], 0.0)
        self.assertLessEqual(report["success_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()