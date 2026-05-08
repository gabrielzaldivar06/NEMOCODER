import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nemo_coding_platform.mission_control_server import HandoffJob, MissionControlHttpServer, MissionControlServerConfig, api_apply, api_handoff_start, api_nemo, api_repo_open, api_review, api_settings, api_state
from tests.test_review_gate_cli import write_ready_run


class DesktopUiFlowsContractTests(unittest.TestCase):
    def test_launch_flow_returns_state_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", root / "runs", memory_db=None)

            state = api_state(config)

        self.assertIn("product", state)
        self.assertIn("settings", state)
        self.assertIn("runs", state)

    def test_connect_model_flow_updates_runtime_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", root / "runs", memory_db=None)
            server = MissionControlHttpServer(("127.0.0.1", 0), config)
            try:
                payload = api_settings(
                    server,
                    {
                        "provider": "subprocess",
                        "default_model": "nemo-model",
                        "model_roles": {
                            "planner": "nemo-model-plan",
                            "editor": "nemo-model-edit",
                            "reviewer": "nemo-model-review",
                            "summarizer": "nemo-model-summary",
                        },
                        "timeout_seconds": 45,
                    },
                )
            finally:
                server.server_close()

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["settings"]["provider"], "subprocess")
        self.assertEqual(payload["settings"]["default_model"], "nemo-model")
        self.assertEqual(payload["settings"]["model_roles"]["planner"], "nemo-model-plan")
        self.assertEqual(payload["settings"]["model_roles"]["editor"], "nemo-model-edit")
        self.assertEqual(payload["settings"]["model_roles"]["reviewer"], "nemo-model-review")
        self.assertEqual(payload["settings"]["model_roles"]["summarizer"], "nemo-model-summary")
        self.assertEqual(payload["settings"]["timeout_seconds"], 45)

    def test_connect_nemo_flow_returns_health_and_memory_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", root / "runs", memory_db=None)

            payload = api_nemo(config, {})

        self.assertTrue(payload["ok"])
        self.assertIn("health", payload)
        self.assertIn("memory_traces", payload)
        self.assertFalse(payload["health"]["enabled"])

    def test_open_repo_flow_accepts_git_repo_and_returns_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current_repo = root / "current"
            target_repo = root / "target"
            current_repo.mkdir()
            target_repo.mkdir()
            (current_repo / ".git").mkdir()
            (target_repo / ".git").mkdir()
            config = MissionControlServerConfig.from_paths(current_repo, ".nemo-runtimes", root / "apply-results", root / "runs", memory_db=None)
            server = MissionControlHttpServer(("127.0.0.1", 0), config)
            try:
                payload = api_repo_open(server, {"repo_path": str(target_repo)})
            finally:
                server.server_close()

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["repo"]["is_git_repo"])
        self.assertEqual(payload["repo"]["path"], str(target_repo.resolve()))
        self.assertIn("state", payload)

    def test_run_agent_flow_starts_handoff_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", root / "runs", memory_db=None)
            server = MissionControlHttpServer(("127.0.0.1", 0), config)
            fake_job = HandoffJob(
                job_id="job-1",
                task_id="task-1",
                run_id="run-1",
                run_json=str(root / "runs" / "run-1.json"),
                status="starting",
                command=("python", "-m", "nemo_coding_platform.cli"),
                payload={"objective": "build feature"},
                logs=["starting handoff job"],
            )
            try:
                with patch.object(server.jobs, "start", return_value=fake_job):
                    payload = api_handoff_start(server, {"objective": "build feature"})
            finally:
                server.server_close()

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["job"]["job_id"], "job-1")
        self.assertEqual(payload["job"]["status"], "starting")

    def test_review_and_approve_flow_produces_merge_plan_and_apply_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            runtimes = root / "runs"
            repo.mkdir()
            sandbox.mkdir()
            runtimes.mkdir()
            (sandbox / "created.txt").write_text("created", encoding="utf-8")
            run_json = runtimes / "run.json"
            write_ready_run(run_json, repo, sandbox, ["created.txt"])
            config = MissionControlServerConfig.from_paths(repo, runtimes, root / "apply-results", None)

            review_payload = api_review(config, {"source_json": str(run_json)})
            apply_payload = api_apply(config, {"source_json": str(run_json), "approve_review": True})

        self.assertTrue(review_payload["ok"])
        self.assertTrue(review_payload["plan"]["mergeable"])
        self.assertTrue(apply_payload["ok"])
        self.assertEqual(apply_payload["result"]["created_files"], ["created.txt"])

    def test_memory_trace_flow_uses_selected_run_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            runtimes = root / "runs"
            runtimes.mkdir()
            run_json = runtimes / "run.json"
            run_json.write_text(
                json.dumps(
                    {
                        "task": {"id": "task-1", "objective": "objective"},
                        "run": {"id": "run-1"},
                        "portfolio": {"memory_atom_ids": []},
                        "memory_traces": [{"summary": "trace summary"}],
                    }
                ),
                encoding="utf-8",
            )
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", runtimes, memory_db=None)

            payload = api_nemo(config, {"source_json": str(run_json)})

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["selected_run"]["task_id"], "task-1")
        self.assertEqual(payload["memory_traces"][0]["summary"], "trace summary")


if __name__ == "__main__":
    unittest.main()