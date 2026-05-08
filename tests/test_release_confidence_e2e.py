import json
import tempfile
import unittest
from pathlib import Path

from nemo_coding_platform.mission_control_server import (
    MissionControlServerConfig,
    api_apply,
    api_applies,
    api_file,
    api_review,
    api_rollback,
    api_state,
)
from tests.test_review_gate_cli import write_ready_run


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
            config = MissionControlServerConfig.from_paths(repo, runtimes, apply_results, None)

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


if __name__ == "__main__":
    unittest.main()
