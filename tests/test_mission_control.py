import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from nemo_coding_platform.cli import main
from nemo_coding_platform.core.mission_control import build_mission_control_state
from tests.test_review_gate import ready_payload


class MissionControlTests(unittest.TestCase):
    def test_state_discovers_ready_runs_and_review_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            runtimes = root / "runs"
            repo.mkdir()
            sandbox.mkdir()
            runtimes.mkdir()
            (sandbox / "created.txt").write_text("created", encoding="utf-8")
            (runtimes / "run.json").write_text(json.dumps(ready_payload(repo, sandbox, ["created.txt"])), encoding="utf-8")

            state = build_mission_control_state(repo, runtimes)

        self.assertEqual(state["product"], "NEMO Desktop Mission Control")
        self.assertEqual(len(state["runs"]), 1)
        self.assertEqual(len(state["approval_queue"]), 1)
        self.assertEqual(state["runs"][0]["review_status"], "awaiting_review")
        self.assertTrue(state["runs"][0]["mergeable"])
        self.assertEqual(state["settings"]["quality_core"], "product/aider")

    def test_cli_exports_state_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            runtimes = root / "runs"
            output_path = root / "mission-control.json"
            repo.mkdir()
            sandbox.mkdir()
            runtimes.mkdir()
            (sandbox / "created.txt").write_text("created", encoding="utf-8")
            (runtimes / "run.json").write_text(json.dumps(ready_payload(repo, sandbox, ["created.txt"])), encoding="utf-8")

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["mission-control-state", "--repo", str(repo), "--runtimes", str(runtimes), "--save-json", str(output_path), "--json"])
            payload = json.loads(output.getvalue())
            saved = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        self.assertEqual(payload["runs"][0]["changed_files"], ["created.txt"])
        self.assertEqual(saved["approval_queue"][0]["review_status"], "awaiting_review")

    def test_state_orders_newest_run_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            runtimes = root / "runs"
            repo.mkdir()
            sandbox.mkdir()
            runtimes.mkdir()
            (sandbox / "created.txt").write_text("created", encoding="utf-8")
            older = runtimes / "older.json"
            newer = runtimes / "newer.json"
            older.write_text(json.dumps(ready_payload(repo, sandbox, ["created.txt"])), encoding="utf-8")
            newer.write_text(json.dumps(ready_payload(repo, sandbox, ["created.txt"])), encoding="utf-8")
            os.utime(older, (100, 100))
            os.utime(newer, (200, 200))

            state = build_mission_control_state(repo, runtimes)

        self.assertEqual(Path(state["runs"][0]["source_json"]).name, "newer.json")

    def test_state_exposes_runtime_profiles_and_continuation_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            runtimes = root / "runs"
            repo.mkdir()
            sandbox.mkdir()
            runtimes.mkdir()
            (sandbox / "created.txt").write_text("created", encoding="utf-8")
            (sandbox / "continuation-state.json").write_text(
                json.dumps({"resume_token": "task:run:minute-20", "validation_policy": "smoke", "checkpoint_refs": ["checkpoint-execute.md"]}),
                encoding="utf-8",
            )
            payload = ready_payload(repo, sandbox, ["created.txt"])
            payload["run"]["runtime_id"] = "runtime-1"
            payload["run"]["state"] = "reviewing"
            payload["run"]["phase"] = "review"
            payload["run"]["validation_profile"] = "smoke"
            payload["run"]["permission_profile"] = "full-handoff"
            payload["run"]["model_profile"] = "local-model"
            (runtimes / "run.json").write_text(json.dumps(payload), encoding="utf-8")

            state = build_mission_control_state(repo, runtimes)
            run = state["runs"][0]

        self.assertEqual(run["runtime_id"], "runtime-1")
        self.assertEqual(run["runtime_state"], "reviewing")
        self.assertEqual(run["execution_phase"], "review")
        self.assertEqual(run["validation_profile"], "smoke")
        self.assertEqual(run["continuation_state"]["resume_token"], "task:run:minute-20")

    def test_state_normalizes_missing_validation_policy_setting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = build_mission_control_state(tmp, Path(tmp) / "missing-runtimes", settings={"validation_policy": None})

        self.assertEqual(state["settings"]["validation_policy"], "smoke")


if __name__ == "__main__":
    unittest.main()