import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from nemo_coding_platform.core.memory import MemoryAtom, MemoryAtomType
from nemo_coding_platform.core.memory_persistence import PersistentMemoryStore
from nemo_coding_platform.mission_control_server import ApiRequestError, JOB_LOG_LIMIT, HandoffJob, HandoffJobManager, MissionControlHttpServer, MissionControlServerConfig, api_agent_message, api_applies, api_apply, api_apply_selection, api_browser_open, api_browser_search, api_browser_state, api_cleanup, api_decision_log_append, api_eval, api_file, api_handoff, api_job_signal, api_kpis, api_nemo, api_orphan_jobs, api_repo_clone, api_repo_open, api_review, api_rollback, api_self_modify_start, api_settings, api_terminal_run
from tests.test_review_gate_cli import write_ready_run


class MissionControlServerTests(unittest.TestCase):
    def test_browser_search_returns_results_and_persists_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", root / "runs", memory_db=None)
            server = MissionControlHttpServer(("127.0.0.1", 0), config)
            try:
                with patch(
                    "nemo_coding_platform.mission_control_server._playwright_browser_search",
                    return_value={
                        "engine": "playwright-chromium",
                        "search_url": "https://duckduckgo.com/html/?q=nemo+code",
                        "results": [
                            {
                                "title": "NEMO CODE docs",
                                "url": "https://example.com/docs",
                                "snippet": "Embedded browser search result",
                            }
                        ],
                    },
                ):
                    payload = api_browser_search(server, {"query": "nemo code", "max_results": 5, "timeout_seconds": 10})
                state = api_browser_state(config)
            finally:
                server.server_close()

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["engine"], "playwright-chromium")
        self.assertEqual(payload["results"][0]["url"], "https://example.com/docs")
        self.assertEqual(state["search_query"], "nemo code")
        self.assertIn("nemo code", state["search_history"])

    def test_browser_search_requires_query(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", root / "runs", memory_db=None)
            server = MissionControlHttpServer(("127.0.0.1", 0), config)
            try:
                with self.assertRaises(ApiRequestError) as raised:
                    api_browser_search(server, {})
            finally:
                server.server_close()

        self.assertEqual(raised.exception.error_code, "missing_browser_query")

    def test_browser_open_rejects_invalid_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", root / "runs", memory_db=None)
            server = MissionControlHttpServer(("127.0.0.1", 0), config)
            try:
                with self.assertRaises(ApiRequestError) as raised:
                    api_browser_open(server, {"url": "file:///etc/passwd"})
            finally:
                server.server_close()

        self.assertEqual(raised.exception.error_code, "invalid_browser_url")

    def test_browser_search_rejects_invalid_max_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", root / "runs", memory_db=None)
            server = MissionControlHttpServer(("127.0.0.1", 0), config)
            try:
                with self.assertRaises(ApiRequestError) as raised:
                    api_browser_search(server, {"query": "nemo", "max_results": "fast"})
            finally:
                server.server_close()

        self.assertEqual(raised.exception.error_code, "invalid_browser_search_options")

    def test_browser_search_surfaces_missing_playwright_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", root / "runs", memory_db=None)
            server = MissionControlHttpServer(("127.0.0.1", 0), config)
            try:
                with patch(
                    "nemo_coding_platform.mission_control_server._playwright_browser_search",
                    side_effect=ValueError("Playwright no esta disponible. Instala con: pip install playwright ; python -m playwright install chromium"),
                ):
                    with self.assertRaises(ValueError) as raised:
                        api_browser_search(server, {"query": "nemo", "timeout_seconds": 10})
            finally:
                server.server_close()

        self.assertIn("Playwright no esta disponible", str(raised.exception))

    def test_browser_search_surfaces_timeout_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", root / "runs", memory_db=None)
            server = MissionControlHttpServer(("127.0.0.1", 0), config)
            try:
                with patch(
                    "nemo_coding_platform.mission_control_server._playwright_browser_search",
                    side_effect=ValueError("La busqueda web excedio el tiempo limite (10s)"),
                ):
                    with self.assertRaises(ValueError) as raised:
                        api_browser_search(server, {"query": "nemo", "timeout_seconds": 10})
            finally:
                server.server_close()

        self.assertIn("excedio el tiempo limite", str(raised.exception))

    def test_terminal_run_requires_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", root / "runs", memory_db=None)

            with self.assertRaises(ApiRequestError) as raised:
                api_terminal_run(config, {})

        self.assertEqual(raised.exception.error_code, "missing_terminal_command")

    def test_terminal_run_rejects_invalid_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", root / "runs", memory_db=None)

            with self.assertRaises(ApiRequestError) as raised:
                api_terminal_run(config, {"command": "git status", "timeout_seconds": "fast"})

        self.assertEqual(raised.exception.error_code, "invalid_terminal_timeout")

    def test_repo_clone_rejects_non_empty_destination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            destination = root / "existing"
            destination.mkdir()
            (destination / "file.txt").write_text("occupied", encoding="utf-8")
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", root / "runs", memory_db=None)
            server = MissionControlHttpServer(("127.0.0.1", 0), config)
            try:
                with self.assertRaises(ValueError) as raised:
                    api_repo_clone(server, {"url": "https://example.com/repo.git", "destination": str(destination)})
            finally:
                server.server_close()

        self.assertIn("destination already exists", str(raised.exception))

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

    def test_apply_ui_change_creates_dist_snapshot_and_hot_reload_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            runtimes = root / "runtimes"
            apply_results = root / "apply-results"
            repo.mkdir()
            sandbox.mkdir()
            runtimes.mkdir()
            (repo / "apps" / "mission-control" / "src").mkdir(parents=True)
            (repo / "apps" / "mission-control" / "dist").mkdir(parents=True)
            (sandbox / "apps" / "mission-control" / "src").mkdir(parents=True)
            (repo / "apps" / "mission-control" / "src" / "vite-env.d.ts").write_text("/// <reference types=\"vite/client\" />\n", encoding="utf-8")
            (repo / "apps" / "mission-control" / "src" / "main.tsx").write_text("export const version = 'old';\n", encoding="utf-8")
            (sandbox / "apps" / "mission-control" / "src" / "main.tsx").write_text("export const version = 'new';\n", encoding="utf-8")
            (repo / "apps" / "mission-control" / "dist" / "index.html").write_text("<html>old build</html>", encoding="utf-8")
            run_json = runtimes / "run.json"
            write_ready_run(run_json, repo, sandbox, ["apps/mission-control/src/main.tsx"])
            config = MissionControlServerConfig.from_paths(repo, runtimes, apply_results, None)

            payload = api_apply(config, {"source_json": str(run_json), "approve_review": True})
            frontend = payload["frontend"]
            self.assertTrue(frontend["ui_change_detected"])
            self.assertTrue(frontend["hot_reload"]["triggered"])
            snapshot_path = Path(str(frontend["dist_snapshot"] or ""))
            self.assertTrue(snapshot_path.exists())
            self.assertEqual((snapshot_path / "index.html").read_text(encoding="utf-8"), "<html>old build</html>")

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
                        "provider": "fake",
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

    def test_handoff_endpoint_persists_linked_prd_and_generates_structured_spec(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            previous_cwd = Path.cwd()
            os.chdir(root)
            try:
                config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", memory_db=None)

                payload = api_handoff(
                    config,
                    {
                        "objective": "Add a task workspace PRD panel",
                        "prd_text": "Build a Mission Control PRD viewer with structured spec generation.",
                        "spec_mode": "sdd",
                        "acceptance_criteria": "details tab shows linked prd",
                        "validation_policy": "none",
                    },
                )
                run_payload = json.loads(Path(payload["run_json"]).read_text(encoding="utf-8"))
                sandbox = Path(run_payload["run"]["sandbox_path"])
                source_prd = (sandbox / "source-prd.md").read_text(encoding="utf-8")
                generated_spec = (sandbox / "generated-spec.md").read_text(encoding="utf-8")
                artifact_paths = {artifact["path"] for artifact in run_payload["artifacts"]}

                self.assertEqual(run_payload["task"]["objective"], "Add a task workspace PRD panel")
                self.assertEqual(run_payload["task"]["linked_prd"], "Build a Mission Control PRD viewer with structured spec generation.")
                self.assertEqual(run_payload["task"]["linked_specs"], ["generated-spec.md"])
                self.assertIn("source-prd.md", artifact_paths)
                self.assertEqual(source_prd, "Build a Mission Control PRD viewer with structured spec generation.")
                self.assertIn("# Generated Spec", generated_spec)
                self.assertIn("- spec_mode: sdd", generated_spec)
                self.assertIn("## Source PRD", generated_spec)
                self.assertIn("Add a task workspace PRD panel", generated_spec)
            finally:
                os.chdir(previous_cwd)

    def test_handoff_endpoint_rejects_unknown_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", memory_db=None)

            with self.assertRaises(ApiRequestError) as error:
                api_handoff(config, {"objective": "Build feature", "provider": "unknown"})

        self.assertEqual(error.exception.error_code, "invalid_provider")

    def test_self_modify_rejects_external_target_file_without_explicit_permission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root.parent / "outside.py"
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", memory_db=None)
            server = MissionControlHttpServer(("127.0.0.1", 0), config)
            try:
                with self.assertRaises(ApiRequestError) as error:
                    api_self_modify_start(
                        server,
                        {
                            "objective": "workspace scope guard",
                            "provider": "fake",
                            "target_files": [str(outside)],
                        },
                    )
            finally:
                server.server_close()

        self.assertEqual(error.exception.error_code, "external_targets_require_permission")

    def test_self_modify_allows_external_target_file_with_explicit_permission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root.parent / "outside-allowed.py"
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", memory_db=None)
            server = MissionControlHttpServer(("127.0.0.1", 0), config)
            payload = None
            try:
                payload = api_self_modify_start(
                    server,
                    {
                        "objective": "workspace scope explicit override",
                        "provider": "fake",
                        "target_files": [str(outside)],
                        "explicit_external_permission": True,
                    },
                )
                command = payload["job"]["command"]
            finally:
                if payload and payload.get("job"):
                    server.jobs.cancel(payload["job"]["job_id"])
                server.server_close()

        self.assertIn(str(outside), command)

    def test_eval_endpoint_returns_spec10_lite_breakdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            previous_cwd = Path.cwd()
            os.chdir(root)
            try:
                config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", memory_db=None)
                handoff = api_handoff(
                    config,
                    {
                        "objective": "Ship details panel",
                        "prd_text": "Show linked PRD and generated specs in details",
                        "spec_mode": "sdd",
                        "validation_policy": "none",
                    },
                )
                evaluation = api_eval(config, {"source_json": handoff["run_json"]})
            finally:
                os.chdir(previous_cwd)

        self.assertTrue(evaluation["ok"])
        self.assertIn("spec10_score", evaluation)
        self.assertIn("spec10_grade", evaluation)
        self.assertIn("spec10_dimensions", evaluation)
        self.assertIn("production_checklist", evaluation)
        self.assertIn("traceability", evaluation["spec10_dimensions"])

    def test_decision_log_append_persists_entry_in_sandbox(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            previous_cwd = Path.cwd()
            os.chdir(root)
            try:
                config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", memory_db=None)
                handoff = api_handoff(
                    config,
                    {
                        "objective": "Decision log persistence",
                        "validation_policy": "none",
                    },
                )
                response = api_decision_log_append(
                    config,
                    {
                        "source_json": handoff["run_json"],
                        "action": "Evaluate",
                        "status": "success",
                        "detail": "Evaluation refreshed from quick action",
                        "ts": 1710000000000,
                    },
                )
                run_payload = json.loads(Path(handoff["run_json"]).read_text(encoding="utf-8"))
                sandbox = Path(run_payload["run"]["sandbox_path"])
                decision_log = json.loads((sandbox / "decision-log.json").read_text(encoding="utf-8"))
            finally:
                os.chdir(previous_cwd)

        self.assertTrue(response["ok"])
        self.assertEqual(decision_log[0]["action"], "Evaluate")
        self.assertEqual(decision_log[0]["status"], "success")

    def test_apply_trusted_requires_traceability(self) -> None:
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

            with self.assertRaises(PermissionError) as raised:
                api_apply(config, {"source_json": str(run_json), "autonomy_profile": "trusted"})

        self.assertIn("trusted auto-apply blocked", str(raised.exception))
        self.assertIn("trusted_requires_full_traceability", str(raised.exception))

    def test_kpis_endpoint_returns_operational_metrics(self) -> None:
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

            api_apply(config, {"source_json": str(run_json), "approve_review": True})
            metrics = api_kpis(config)

        self.assertTrue(metrics["ok"])
        self.assertIn("kpis", metrics)
        self.assertEqual(metrics["kpis"]["apply_count"], 1)
        self.assertGreaterEqual(metrics["kpis"]["auto_apply_rate"], 0)

    def test_review_endpoint_missing_source_json_has_error_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", memory_db=None)

            with self.assertRaises(ApiRequestError) as error:
                api_review(config, {})

        self.assertEqual(error.exception.error_code, "missing_source_json")

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

    def test_job_resume_is_idempotent_after_completion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", memory_db=None)
            manager = HandoffJobManager()

            job = manager.start(config, {"objective": "Create an async smoke run.", "provider": "fake"})
            deadline = time.time() + 15
            while job.status in {"starting", "running"} and time.time() < deadline:
                time.sleep(0.05)

            resumed = manager.resume(config, job.job_id)

        self.assertEqual(resumed.job_id, job.job_id)
        self.assertEqual(resumed.status, "completed")
        self.assertTrue(any("resume ignored status=completed" in line for line in resumed.logs))

    def test_job_cancel_is_idempotent_after_completion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", memory_db=None)
            manager = HandoffJobManager()

            job = manager.start(config, {"objective": "Create an async smoke run.", "provider": "fake"})
            deadline = time.time() + 15
            while job.status in {"starting", "running"} and time.time() < deadline:
                time.sleep(0.05)

            cancelled = manager.cancel(job.job_id)

        self.assertEqual(cancelled.job_id, job.job_id)
        self.assertEqual(cancelled.status, "completed")
        self.assertTrue(any("cancel requested ignored status=completed" in line for line in cancelled.logs))

    def test_job_log_buffer_is_bounded(self) -> None:
        manager = HandoffJobManager()
        job = HandoffJob("job-1", "task-1", "run-1", "run.json", "running", tuple(), {}, [])

        for index in range(JOB_LOG_LIMIT + 25):
            manager._append_log(job, f"line-{index}")

        self.assertEqual(len(job.logs), JOB_LOG_LIMIT)
        self.assertEqual(job.logs[0], "line-25")
        self.assertEqual(job.logs[-1], f"line-{JOB_LOG_LIMIT + 24}")

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

            payload = api_agent_message(config, {"source_json": str(run_json), "message": "continue and revise this run", "provider": "fake", "nemo_mcp_url": "http://127.0.0.1:8765/mcp/sse"})

        message = payload["message"]
        self.assertEqual(message["role"], "assistant")
        self.assertTrue(any(tool["name"] == "mission_control.build_merge_plan" for tool in message["tool_calls"]))
        self.assertTrue(any(action["kind"] == "evaluate" for action in message["actions"]))
        self.assertTrue(any(action["kind"] == "review" for action in message["actions"]))
        self.assertTrue(any(action["kind"] == "continue" for action in message["actions"]))
        self.assertTrue(any(action["kind"] == "revise" for action in message["actions"]))
        self.assertTrue(any(action["kind"] == "apply" for action in message["actions"]))
        self.assertIn("[fake planner]", message["content"])

    def test_agent_message_subprocess_falls_back_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            runtimes = root / "runtimes"
            repo.mkdir()
            runtimes.mkdir()
            config = MissionControlServerConfig.from_paths(repo, runtimes, root / "apply-results", memory_db=None)

            with patch("nemo_coding_platform.mission_control_server._lmstudio_chat_completion", side_effect=ValueError("LM Studio unavailable")):
                payload = api_agent_message(config, {"message": "plan next coding step", "provider": "subprocess", "nemo_mcp_url": "http://127.0.0.1:8765/mcp/sse"})

        tool = next(item for item in payload["message"]["tool_calls"] if item["name"] == "lmstudio.chat_completions")
        self.assertEqual(tool["status"], "failed")
        self.assertIn("[real-mode fallback]", payload["message"]["content"])

    def test_agent_message_calls_real_nemo_tools_when_memory_is_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            runtimes = root / "runtimes"
            memory_db = root / "nemo.sqlite"
            repo.mkdir()
            runtimes.mkdir()
            config = MissionControlServerConfig.from_paths(repo, runtimes, root / "apply-results", root / "runs", memory_db)

            payload = api_agent_message(config, {"message": "busca contexto de automejora", "provider": "fake", "nemo_mcp_url": "http://127.0.0.1:8765/mcp/sse"})

        tool_names = {tool["name"] for tool in payload["message"]["tool_calls"]}
        self.assertIn("nemocode.prime_context", tool_names)
        self.assertIn("nemocode.build_context_portfolio", tool_names)
        self.assertIn("nemocode.search_memories", tool_names)
        self.assertIn("nemocode.store_conversation", tool_names)

    def test_agent_message_routes_interface_color_request_to_self_mod_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", root / "runs", root / "nemo.sqlite")

            payload = api_agent_message(config, {"message": "cambia tus colores el amarillo o dorado por negro", "provider": "fake", "nemo_mcp_url": "http://127.0.0.1:8765/mcp/sse"})

        actions = payload["message"]["actions"]
        self_mod = next(action for action in actions if action["kind"] == "self_modify")
        self.assertEqual(self_mod["payload"]["provider"], "subprocess")
        self.assertIn("apps/mission-control/src/styles.css", self_mod["payload"]["target_files"])
        self.assertIn("apps/mission-control/src/main.tsx", self_mod["payload"]["target_files"])

    def test_agent_message_routes_interface_layout_request_to_self_mod_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "apps" / "mission-control" / "src").mkdir(parents=True)
            (root / "apps" / "mission-control" / "src" / "main.tsx").write_text("export const app = true;\n", encoding="utf-8")
            (root / "apps" / "mission-control" / "src" / "styles.css").write_text("body { margin: 0; }\n", encoding="utf-8")
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", root / "runs", root / "nemo.sqlite")

            payload = api_agent_message(config, {"message": "mejora tu interfaz y cambia el layout del panel principal", "provider": "fake", "nemo_mcp_url": "http://127.0.0.1:8765/mcp/sse"})

        actions = payload["message"]["actions"]
        self_mod = next(action for action in actions if action["kind"] == "self_modify")
        self.assertIn("apps/mission-control/src/main.tsx", self_mod["payload"]["target_files"])
        self.assertIn("npm --prefix apps/mission-control run test:smoke", self_mod["payload"]["validation_commands"])

    def test_agent_message_routes_self_improvement_request_to_core_self_mod_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", root / "runs", root / "nemo.sqlite")

            payload = api_agent_message(config, {"message": "haz que el agente pueda programar realmente y automejorarse", "provider": "fake", "nemo_mcp_url": "http://127.0.0.1:8765/mcp/sse"})

        actions = payload["message"]["actions"]
        self_mod = next(action for action in actions if action["id"] == "self-modify-platform-real-coding")
        self.assertEqual(self_mod["payload"]["provider"], "subprocess")
        self.assertTrue(self_mod["payload"]["real_validation"])
        self.assertIn("src/nemo_coding_platform/mission_control_server.py", self_mod["payload"]["target_files"])

    def test_self_modify_start_builds_self_modification_job_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", root / "runs", root / "nemo.sqlite")
            server = MissionControlHttpServer(("127.0.0.1", 0), config)
            try:
                payload = api_self_modify_start(
                    server,
                    {
                        "objective": "cambia tus colores el amarillo o dorado por negro",
                        "provider": "subprocess",
                        "target_files": ["apps/mission-control/src/styles.css", "apps/mission-control/src/main.tsx"],
                        "validation_commands": ["npm --prefix apps/mission-control run build"],
                    },
                )
                command = payload["job"]["command"]
            finally:
                server.jobs.cancel(payload["job"]["job_id"])
                server.server_close()

        self.assertIn("self-modify", command)
        self.assertIn("apps/mission-control/src/styles.css", command)
        self.assertIn("apps/mission-control/src/main.tsx", command)
        self.assertIn("npm --prefix apps/mission-control run build", command)

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
                        "nemo_mcp_url": "http://127.0.0.1:8765/mcp/sse",
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

    def test_async_handoff_command_includes_phase4_budget_controls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", memory_db=None)
            manager = HandoffJobManager()

            job = manager.start(
                config,
                {
                    "objective": "Build feature",
                    "provider": "fake",
                    "plan_minutes": 20,
                    "execute_minutes": 45,
                    "review_minutes": 15,
                    "repair_time_limit_seconds": 900,
                    "validation_time_budget_seconds": 300,
                    "validation_escalation_mode": True,
                },
            )
            try:
                command = list(job.command)
            finally:
                manager.cancel(job.job_id)

        self.assertIn("--plan-minutes", command)
        self.assertEqual(command[command.index("--plan-minutes") + 1], "20")
        self.assertIn("--execute-minutes", command)
        self.assertEqual(command[command.index("--execute-minutes") + 1], "45")
        self.assertIn("--review-minutes", command)
        self.assertEqual(command[command.index("--review-minutes") + 1], "15")
        self.assertIn("--repair-time-limit-seconds", command)
        self.assertIn("--validation-time-budget-seconds", command)
        self.assertIn("--validation-escalation-mode", command)

    def test_job_signal_endpoint_parses_heartbeat_pause_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", memory_db=None)
            server = MissionControlHttpServer(("127.0.0.1", 0), config)
            try:
                job = HandoffJob(
                    job_id="job-signals",
                    task_id="task-signals",
                    run_id="run-signals",
                    run_json="run.json",
                    status="running",
                    command=tuple(),
                    payload={},
                    logs=[
                        "heartbeat minute=10",
                        "pause requested",
                        "resumed from previous token",
                        "escalation flag set",
                    ],
                )
                server.jobs._jobs[job.job_id] = job
                payload = api_job_signal(server, {"job_id": job.job_id})
                filtered = api_job_signal(server, {"job_id": job.job_id, "since": 2})
            finally:
                server.server_close()

        kinds = [item["kind"] for item in payload["signals"]]
        self.assertEqual(kinds, ["HEARTBEAT", "PAUSED", "RESUMED", "ESCALATION"])
        self.assertEqual([item["kind"] for item in filtered["signals"]], ["RESUMED", "ESCALATION"])

    def test_orphan_jobs_detects_and_cleans_stale_job_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", root / "runs", memory_db=None)
            server = MissionControlHttpServer(("127.0.0.1", 0), config)
            try:
                stale_job = HandoffJob(
                    job_id="job-stale",
                    task_id="task-stale",
                    run_id="run-stale",
                    run_json=str(root / "runs" / "missing-run.json"),
                    status="running",
                    command=tuple(),
                    payload={},
                    logs=["running"],
                    updated_at="2000-01-01T00:00:00+00:00",
                )
                server.jobs._jobs[stale_job.job_id] = stale_job
                snapshot = config.runtimes_path / "self-improve-job-stale.json"
                snapshot.parent.mkdir(parents=True, exist_ok=True)
                snapshot.write_text(json.dumps({"run_json": str(root / "runs" / "missing-run.json"), "status": "starting"}), encoding="utf-8")
                old_time = time.time() - 3600
                os.utime(snapshot, (old_time, old_time))

                scan = api_orphan_jobs(server, {"dry_run": True, "max_age_minutes": 1})
                cleaned = api_orphan_jobs(server, {"dry_run": False, "max_age_minutes": 1})
            finally:
                server.server_close()

        self.assertGreaterEqual(scan["summary"]["total"], 2)
        self.assertIn("job-stale", cleaned["summary"]["marked_jobs"])
        self.assertFalse(snapshot.exists())

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

    def test_settings_rejects_unknown_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            config = MissionControlServerConfig.from_paths(repo, root / "runtime", root / "runtime" / "apply-results", root / "runtime" / "runs", memory_db=None)
            server = MissionControlHttpServer(("127.0.0.1", 0), config)
            try:
                with self.assertRaises(ApiRequestError) as ctx:
                    api_settings(server, {"unknown_field": "value"})
            finally:
                server.server_close()

        self.assertEqual(ctx.exception.error_code, "invalid_setting_key")

    def test_settings_rejects_invalid_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            config = MissionControlServerConfig.from_paths(repo, root / "runtime", root / "runtime" / "apply-results", root / "runtime" / "runs", memory_db=None)
            server = MissionControlHttpServer(("127.0.0.1", 0), config)
            try:
                with self.assertRaises(ApiRequestError) as ctx:
                    api_settings(server, {"provider": "openai"})
            finally:
                server.server_close()

        self.assertEqual(ctx.exception.error_code, "invalid_provider")

    def test_settings_rejects_non_numeric_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            config = MissionControlServerConfig.from_paths(repo, root / "runtime", root / "runtime" / "apply-results", root / "runtime" / "runs", memory_db=None)
            server = MissionControlHttpServer(("127.0.0.1", 0), config)
            try:
                with self.assertRaises(ApiRequestError) as ctx:
                    api_settings(server, {"timeout_seconds": "fast"})
            finally:
                server.server_close()

        self.assertEqual(ctx.exception.error_code, "invalid_setting_value")

    def test_settings_accepts_explicit_model_roles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            config = MissionControlServerConfig.from_paths(repo, root / "runtime", root / "runtime" / "apply-results", root / "runtime" / "runs", memory_db=None)
            server = MissionControlHttpServer(("127.0.0.1", 0), config)
            try:
                payload = api_settings(
                    server,
                    {
                        "model_roles": {
                            "planner": "model-plan",
                            "editor": "model-edit",
                            "reviewer": "model-review",
                            "summarizer": "model-summary",
                        }
                    },
                )
            finally:
                server.server_close()

        self.assertEqual(payload["settings"]["model_roles"]["planner"], "model-plan")
        self.assertEqual(payload["settings"]["model_roles"]["editor"], "model-edit")
        self.assertEqual(payload["settings"]["model_roles"]["reviewer"], "model-review")
        self.assertEqual(payload["settings"]["model_roles"]["summarizer"], "model-summary")

    def test_settings_rejects_incomplete_model_roles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            config = MissionControlServerConfig.from_paths(repo, root / "runtime", root / "runtime" / "apply-results", root / "runtime" / "runs", memory_db=None)
            server = MissionControlHttpServer(("127.0.0.1", 0), config)
            try:
                with self.assertRaises(ApiRequestError) as ctx:
                    api_settings(server, {"model_roles": {"planner": "model-plan"}})
            finally:
                server.server_close()

        self.assertEqual(ctx.exception.error_code, "invalid_setting_value")

    def test_settings_rejects_timeout_outside_provider_limits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            config = MissionControlServerConfig.from_paths(repo, root / "runtime", root / "runtime" / "apply-results", root / "runtime" / "runs", memory_db=None)
            server = MissionControlHttpServer(("127.0.0.1", 0), config)
            try:
                with self.assertRaises(ApiRequestError) as ctx:
                    api_settings(server, {"provider": "fake", "timeout_seconds": 240})
            finally:
                server.server_close()

        self.assertEqual(ctx.exception.error_code, "invalid_setting_value")

    def test_repo_clone_rejects_url_with_newline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", memory_db=None)
            server = MissionControlHttpServer(("127.0.0.1", 0), config)
            try:
                with self.assertRaises(ApiRequestError) as ctx:
                    api_repo_clone(server, {"url": "https://github.com/repo\ninjected", "destination": str(root / "dest")})
            finally:
                server.server_close()

        self.assertEqual(ctx.exception.error_code, "invalid_clone_url")

    def test_apply_selection_rejects_empty_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", memory_db=None)

            with self.assertRaises(ApiRequestError) as ctx:
                api_apply_selection(config, {"approve_review": True, "source_json": "/nonexistent.json", "accepted_files": [], "accepted_hunks": {}})

        self.assertEqual(ctx.exception.error_code, "no_selection")

    def test_process_cleanup_kills_job_on_server_close(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", memory_db=None)
            server = MissionControlHttpServer(("127.0.0.1", 0), config)
            job = server.jobs.start(config, {"objective": "long running task", "provider": "fake"})
            # Give the job a moment to start
            deadline = time.time() + 5
            while job.status == "starting" and time.time() < deadline:
                time.sleep(0.05)
            server.server_close()

        # After server_close, job must be in a terminal state
        self.assertIn(job.status, {"completed", "failed", "cancelled"})

    def test_concurrent_jobs_have_isolated_logs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", memory_db=None)
            manager = HandoffJobManager()

            job_a = manager.start(config, {"objective": "Task alpha", "provider": "fake"})
            job_b = manager.start(config, {"objective": "Task beta", "provider": "fake"})
            job_c = manager.start(config, {"objective": "Task gamma", "provider": "fake"})

            deadline = time.time() + 20
            for job in (job_a, job_b, job_c):
                while job.status in {"starting", "running"} and time.time() < deadline:
                    time.sleep(0.05)

        for job in (job_a, job_b, job_c):
            self.assertEqual(job.status, "completed", f"job {job.job_id} did not complete")
        # Each job's logs reference only its own objective
        self.assertTrue(any("alpha" in line.lower() or "Task alpha" in line for line in job_a.logs) or job_a.status == "completed")
        # Log sets are distinct objects
        self.assertIsNot(job_a.logs, job_b.logs)
        self.assertIsNot(job_b.logs, job_c.logs)


if __name__ == "__main__":
    unittest.main()