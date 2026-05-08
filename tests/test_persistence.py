import tempfile
import unittest

from nemo_coding_platform.core.headless_handoff import HandoffRequest
from nemo_coding_platform.core.headless_runner import execute_headless_handoff
from nemo_coding_platform.core.persistence import (
    build_replay_summary,
    headless_result_to_dict,
    load_headless_result_json,
    save_headless_result_json,
    summarize_persisted_result,
)


def _execute_fake_handoff():
    return execute_headless_handoff(HandoffRequest("Build", ".", ("ok",), ("python -m unittest",)), provider_mode="fake")


class PersistenceTests(unittest.TestCase):
    def test_headless_result_serializes_stable_keys(self) -> None:
        result = _execute_fake_handoff()

        payload = headless_result_to_dict(result)

        self.assertEqual(payload["schema_version"], 1)
        self.assertIn("task", payload)
        self.assertIn("readiness", payload)
        self.assertEqual(payload["mutation_result"]["provider"], "fake-nemo-code")
        self.assertEqual(payload["mutation_result"]["model_profile"]["model"], "nvidia.agentic.coder-4b")
        self.assertIn("generated-implementation.md", payload["mutation_result"]["changed_files"])
        self.assertIn("portfolio", payload)
        self.assertEqual(payload["readiness"]["score"], 1.0)

    def test_headless_result_saves_json_file(self) -> None:
        result = _execute_fake_handoff()
        with tempfile.TemporaryDirectory() as tmp:
            path = save_headless_result_json(result, f"{tmp}/nested/result.json")
            loaded = load_headless_result_json(path)

        self.assertEqual(loaded["schema_version"], 1)
        self.assertEqual(loaded["task"]["autonomy_level"], "full_handoff")

    def test_persisted_result_summary(self) -> None:
        result = _execute_fake_handoff()

        summary = summarize_persisted_result(headless_result_to_dict(result))

        self.assertEqual(summary["grade"], "ready")
        self.assertEqual(summary["score"], 1.0)
        self.assertEqual(summary["model"], "nvidia.agentic.coder-4b")
        self.assertIn("generated-implementation.md", summary["changed_files"])

    def test_build_replay_summary_links_timeline_artifacts_and_runtime_files(self) -> None:
        result = _execute_fake_handoff()

        replay = build_replay_summary(headless_result_to_dict(result))

        self.assertEqual(replay["task_id"], "task-1")
        self.assertEqual(replay["run_id"], "run-1")
        self.assertEqual(replay["grade"], "ready")
        self.assertTrue(replay["can_replay"])
        self.assertIn("checkpoint.md", replay["payload_refs"])
        self.assertIn("checkpoint.md", replay["artifact_paths"])
        self.assertIn("checkpoint.md", replay["runtime_files"])
        self.assertIn("generated-implementation.md", replay["changed_files"])
        self.assertEqual(replay["events"][0]["sequence"], 1)
        self.assertEqual(replay["events"][0]["kind"], "context_bootstrapped")