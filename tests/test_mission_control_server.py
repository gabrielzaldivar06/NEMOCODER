import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from nemo_coding_platform.core.memory import MemoryAtom, MemoryAtomType
from nemo_coding_platform.core.memory_persistence import PersistentMemoryStore
from nemo_coding_platform.mission_control_server import HandoffJobManager, MissionControlHttpServer, MissionControlServerConfig, api_agent_message, api_applies, api_apply, api_apply_selection, api_cleanup, api_file, api_handoff, api_nemo, api_repo_open, api_review, api_rollback, api_settings
from tests.test_review_gate_cli import write_ready_run


class MissionControlServerTests(unittest.TestCase):
    def test_review_endpoint_payload_returns_merge_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            runtimes = root / "runtimes"
            repo.mkdir()
            sandbox.mkdir()
            runtimes.mkdir()
            (sandbox / "created.txt").write_text("created", encoding="utf-8")
            run_json = runtimes / "run.json"
            write_ready_run(run_json, repo, sandbox, ["created.txt"])
            config = MissionControlServerConfig.from_paths(repo, runtimes, root / "apply-results", None)

            payload = api_review(config, {"source_json": str(run_json)})

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["plan"]["files"][0]["operation"], "create")

    def test_apply_saves_structured_apply_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            runtimes = root / "runtimes"
            apply_results = root / "apply-results"
            repo.mkdir()
            sandbox.mkdir()
            runtimes.mkdir()
            (sandbox / "created.txt").write_text("created", encoding="utf-8")
            run_json = runtimes / "run.json"
            write_ready_run(run_json, repo, sandbox, ["created.txt"])
            config = MissionControlServerConfig.from_paths(repo, runtimes, apply_results, None)

            payload = api_apply(config, {"source_json": str(run_json), "approve_review": True})
            apply_payload = json.loads(Path(payload["apply_json"]).read_text(encoding="utf-8"))

            self.assertEqual(payload["result"]["created_files"], ["created.txt"])
            self.assertEqual(apply_payload["applied_files"], ["created.txt"])
            self.assertEqual((repo / "created.txt").read_text(encoding="utf-8"), "created")

    def test_file_endpoint_returns_repo_and_sandbox_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            runtimes = root / "runtimes"
            repo.mkdir()
            sandbox.mkdir()
            runtimes.mkdir()
            (repo / "existing.txt").write_text("old", encoding="utf-8")
            (sandbox / "existing.txt").write_text("new", encoding="utf-8")
            run_json = runtimes / "run.json"
            write_ready_run(run_json, repo, sandbox, ["existing.txt"])
            config = MissionControlServerConfig.from_paths(repo, runtimes, root / "apply-results", None)

            payload = api_file(config, {"source_json": str(run_json), "file_path": "existing.txt"})

        self.assertEqual(payload["operation"], "update")
        self.assertEqual(payload["target_content"], "old")
        self.assertEqual(payload["source_content"], "new")
        self.assertTrue(payload["hunks"])
        self.assertEqual(payload["file_risk_flags"], [])

    def test_apply_selection_applies_only_accepted_hunks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            runtimes = root / "runtimes"
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
            config = MissionControlServerConfig.from_paths(repo, runtimes, root / "apply-results", None)
            preview = api_file(config, {"source_json": str(run_json), "file_path": "existing.txt"})

            payload = api_apply_selection(config, {"source_json": str(run_json), "approve_review": True, "accepted_hunks": {"existing.txt": [preview["hunks"][0]["id"]]}})
            content = (repo / "existing.txt").read_text(encoding="utf-8")

        self.assertEqual(payload["result"]["updated_files"], ["existing.txt"])
        self.assertIn("line 2 changed", content)
        self.assertIn("line 20", content)
        self.assertNotIn("line 20 changed", content)

    def test_rollback_restores_apply_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            runtimes = root / "runtimes"
            repo.mkdir()
            sandbox.mkdir()
            runtimes.mkdir()
            (repo / "existing.txt").write_text("old", encoding="utf-8")
            (sandbox / "existing.txt").write_text("new", encoding="utf-8")
            run_json = runtimes / "run.json"
            write_ready_run(run_json, repo, sandbox, ["existing.txt"])
            config = MissionControlServerConfig.from_paths(repo, runtimes, root / "apply-results", None)
            apply_payload = api_apply(config, {"source_json": str(run_json), "approve_review": True})

            rollback_payload = api_rollback(config, {"apply_json": apply_payload["apply_json"], "approve_review": True})

            self.assertEqual(rollback_payload["result"]["restored_files"], ["existing.txt"])
            self.assertEqual((repo / "existing.txt").read_text(encoding="utf-8"), "old")

    def test_handoff_endpoint_persists_discoverable_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            previous_cwd = Path.cwd()
            os.chdir(root)
            try:
                config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", memory_db=None)

                payload = api_handoff(
                    config,
                    {
                        "objective": "Create src/mission_control_smoke.py with a smoke function.",
                        "acceptance_criteria": "smoke file exists",
                        "validation_policy": "none",
                        "target_files": "src/mission_control_smoke.py",
                    },
                )
                run_payload = json.loads(Path(payload["run_json"]).read_text(encoding="utf-8"))
                self.assertTrue(Path(payload["run_json"]).exists())
                self.assertGreaterEqual(len(payload["state"]["runs"]), 1)
                self.assertTrue(payload["summary"]["changed_files"])
                self.assertEqual(run_payload["run"]["validation_profile"], "none")
                self.assertEqual(payload["state"]["runs"][0]["validation_profile"], "none")
            finally:
                os.chdir(previous_cwd)

    def test_handoff_endpoint_rejects_unknown_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", memory_db=None)

            with self.assertRaises(ValueError):
                api_handoff(config, {"objective": "Build feature", "provider": "unknown"})

    def test_async_handoff_job_completes_and_captures_logs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", memory_db=None)
            manager = HandoffJobManager()

            job = manager.start(
                config,
                {
                    "objective": "Create an async smoke run.",
                    "acceptance_criteria": "async job completes",
                    "validation_commands": "python -m unittest",
                    "provider": "fake",
                },
            )
            deadline = time.time() + 15
            while job.status in {"starting", "running"} and time.time() < deadline:
                time.sleep(0.05)

            finished = manager.get(job.job_id)
            self.assertEqual(finished.status, "completed")
            self.assertEqual(finished.returncode, 0)
            self.assertTrue(Path(finished.run_json).exists())
            self.assertTrue(any("job finished" in line or "validation_passed" in line for line in finished.logs))

    def test_agent_message_returns_tool_calls_and_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            runtimes = root / "runtimes"
            repo.mkdir()
            sandbox.mkdir()
            runtimes.mkdir()
            (sandbox / "created.txt").write_text("created", encoding="utf-8")
            run_json = runtimes / "run.json"
            write_ready_run(run_json, repo, sandbox, ["created.txt"])
            config = MissionControlServerConfig.from_paths(repo, runtimes, root / "apply-results", memory_db=None)

            payload = api_agent_message(config, {"source_json": str(run_json), "message": "continue and revise this run"})

        message = payload["message"]
        self.assertEqual(message["role"], "assistant")
        self.assertTrue(any(tool["name"] == "mission_control.build_merge_plan" for tool in message["tool_calls"]))
        self.assertTrue(any(action["kind"] == "continue" for action in message["actions"]))
        self.assertTrue(any(action["kind"] == "revise" for action in message["actions"]))
        self.assertTrue(any(action["kind"] == "apply" for action in message["actions"]))
        self.assertIn("[fake planner]", message["content"])

    def test_agent_message_uses_lmstudio_for_subprocess_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            runtimes = root / "runtimes"
            repo.mkdir()
            sandbox.mkdir()
            runtimes.mkdir()
            (sandbox / "created.txt").write_text("created", encoding="utf-8")
            run_json = runtimes / "run.json"
            write_ready_run(run_json, repo, sandbox, ["created.txt"])
            config = MissionControlServerConfig.from_paths(repo, runtimes, root / "apply-results", memory_db=None)

            with patch("nemo_coding_platform.mission_control_server._lmstudio_chat_completion", return_value="Soy el modelo local real.") as chat:
                payload = api_agent_message(
                    config,
                    {
                        "source_json": str(run_json),
                        "message": "que puedes hacer",
                        "provider": "subprocess",
                        "model_base_url": "http://localhost:1234/v1",
                        "default_model": "nvidia.agentic.coder-4b",
                    },
                )

        message = payload["message"]
        self.assertEqual(message["content"], "Soy el modelo local real.")
        self.assertTrue(any(tool["name"] == "lmstudio.chat_completions" for tool in message["tool_calls"]))
        chat.assert_called_once()

    def test_nemo_endpoint_returns_operational_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            runtimes = root / "runtimes"
            memory_db = root / "nemo.sqlite"
            repo.mkdir()
            sandbox.mkdir()
            runtimes.mkdir()
            (sandbox / "created.txt").write_text("created", encoding="utf-8")
            store = PersistentMemoryStore(memory_db)
            correction_id = store.create_atom(MemoryAtom(MemoryAtomType.CORRECTION, "Prefer full NEMO tool plane.", "user"), topic="corrections", importance=10)
            evidence_handle = store.create_evidence("full evidence", "compact evidence", source_task_id="task-1", source_run_id="run-1")
            store.record_feedback(atom_id=correction_id, evidence_handle=evidence_handle, event_type="useful", was_useful=True)
            run_json = runtimes / "run.json"
            write_ready_run(run_json, repo, sandbox, ["created.txt"])
            run_payload = json.loads(run_json.read_text(encoding="utf-8"))
            run_payload["portfolio"] = {"context": "[correction] Prefer full NEMO tool plane.", "estimated_tokens": 8, "memory_atom_ids": [correction_id]}
            run_payload["memory_traces"] = [{"nemo_tool": "prime_context", "summary": "loaded correction"}]
            run_json.write_text(json.dumps(run_payload), encoding="utf-8")
            config = MissionControlServerConfig.from_paths(repo, runtimes, root / "apply-results", root / "runs", memory_db)

            payload = api_nemo(config, {"source_json": str(run_json)})

        self.assertTrue(payload["health"]["enabled"])
        self.assertEqual(payload["health"]["atom_count"], 1)
        self.assertEqual(payload["context_portfolio"]["estimated_tokens"], 8)
        self.assertEqual(payload["used_memories"][0]["id"], correction_id)
        self.assertEqual(payload["corrections"][0]["content"], "Prefer full NEMO tool plane.")
        self.assertEqual(payload["evidence"][0]["handle"], evidence_handle)
        self.assertEqual(payload["feedback"][0]["event_type"], "useful")

    def test_settings_persist_and_repo_open_validates_git_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            config = MissionControlServerConfig.from_paths(repo, root / "runtime", root / "runtime" / "apply-results", root / "runtime" / "runs", root / "runtime" / "nemo.sqlite")
            server = MissionControlHttpServer(("127.0.0.1", 0), config)
            try:
                settings_payload = api_settings(server, {"model_base_url": "http://127.0.0.1:1234/v1", "default_model": "local-model", "provider": "subprocess", "timeout_seconds": 90, "validation_policy": "targeted"})
                open_payload = api_repo_open(server, {"repo_path": str(repo)})
            finally:
                server.server_close()

        self.assertEqual(settings_payload["settings"]["default_model"], "local-model")
        self.assertEqual(settings_payload["settings"]["provider"], "subprocess")
        self.assertEqual(settings_payload["settings"]["validation_policy"], "targeted")
        self.assertTrue(open_payload["repo"]["is_git_repo"])
        self.assertIn(str(repo), open_payload["state"]["settings"]["recent_repos"])

    def test_async_handoff_command_includes_validation_policy_without_default_full_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", memory_db=None)
            manager = HandoffJobManager()

            job = manager.start(config, {"objective": "Build feature", "provider": "fake", "validation_policy": "none"})
            try:
                command = list(job.command)
            finally:
                manager.cancel(job.job_id)

        self.assertIn("--validation-policy", command)
        self.assertEqual(command[command.index("--validation-policy") + 1], "none")
        self.assertNotIn("python -m unittest", command)

    def test_apply_history_and_cleanup_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            runtimes = root / "runtimes"
            apply_results = root / "apply-results"
            repo.mkdir()
            sandbox.mkdir()
            runtimes.mkdir()
            (sandbox / "created.txt").write_text("created", encoding="utf-8")
            run_json = runtimes / "run.json"
            write_ready_run(run_json, repo, sandbox, ["created.txt"])
            config = MissionControlServerConfig.from_paths(repo, runtimes, apply_results, runtimes / "runs", memory_db=None)
            api_apply(config, {"source_json": str(run_json), "approve_review": True})
            old_artifact = apply_results / "partial-backups" / "old" / "artifact.txt"
            old_artifact.parent.mkdir(parents=True)
            old_artifact.write_text("old", encoding="utf-8")
            old_time = time.time() - 9 * 86400
            os.utime(old_artifact, (old_time, old_time))

            history = api_applies(config)
            dry_run = api_cleanup(config, {"dry_run": True, "max_age_days": 7})
            cleanup = api_cleanup(config, {"dry_run": False, "max_age_days": 7})

        self.assertEqual(history["applies"][0]["applied_files"], ["created.txt"])
        self.assertIn(str(old_artifact), dry_run["candidates"])
        self.assertIn(str(old_artifact), cleanup["deleted"])
        self.assertFalse(old_artifact.exists())


if __name__ == "__main__":
    unittest.main()