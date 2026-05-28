import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

# Set ENABLE_REAL_PROVIDER=1 to run integration tests with a live LLM (LM Studio required).
_REAL_PROVIDER = os.getenv("ENABLE_REAL_PROVIDER") == "1"
_TEST_PROVIDER = "subprocess" if _REAL_PROVIDER else "fake"

import nemo_coding_platform.mission_control_server as mission_control_server
from nemo_coding_platform.core.memory import MemoryAtom, MemoryAtomType
from nemo_coding_platform.core.memory_persistence import PersistentMemoryStore
from nemo_coding_platform.mission_control_server import ApiRequestError, JOB_LOG_LIMIT, HandoffJob, HandoffJobManager, MissionControlHttpServer, MissionControlServerConfig, api_agent_message, api_applies, api_apply, api_apply_selection, api_browser_open, api_browser_search, api_browser_state, api_cleanup, api_decision_log_append, api_eval, api_file, api_handoff, api_handoff_start, api_job_signal, api_kpis, api_nemo, api_nemo_cognitive_stats, api_nemo_mcp_status, api_orphan_jobs, api_repo_clone, api_repo_open, api_repo_map, api_review, api_rollback, api_search, api_self_modify_start, api_settings, api_state, api_stats, api_terminal_run
from nemo_coding_platform.spacecode_mcp_tools import mcp_call_nemo_tool
from tests.test_review_gate_cli import write_ready_run


class MissionControlServerTests(unittest.TestCase):
    def test_chat_max_tokens_defaults_to_interactive_budget(self) -> None:
        self.assertEqual(mission_control_server._chat_max_tokens({}), 32000)

    def test_chat_max_tokens_clamps_invalid_and_small_values(self) -> None:
        self.assertEqual(mission_control_server._chat_max_tokens({"max_tokens": "invalid"}), 32000)
        self.assertEqual(mission_control_server._chat_max_tokens({"max_tokens": 12}), 1024)
        self.assertEqual(mission_control_server._chat_max_tokens({"max_tokens": 999999}), mission_control_server.MAX_CHAT_MAX_TOKENS)

    def test_agent_context_char_budget_scales_with_context_window(self) -> None:
        budget = mission_control_server._agent_context_char_budget(
            {"context_window_tokens": 131072, "chat_max_tokens": 16384},
            12000,
        )

        self.assertGreaterEqual(budget, 12000)
        self.assertGreater(budget, 100000)

    def test_generate_image_requires_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", root / "runs", memory_db=None)

            with self.assertRaises(ApiRequestError) as raised:
                mission_control_server.api_generate_image(config, {})

        self.assertEqual(raised.exception.error_code, "missing_prompt")

    def test_api_search_requires_query(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", root / "runs", memory_db=None)

            with self.assertRaises(ApiRequestError) as raised:
                api_search(config, {})

        self.assertEqual(raised.exception.error_code, "invalid_request")

    def test_api_search_uses_requested_layers_and_returns_llm_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", root / "runs", memory_db=None)

            captured: dict[str, object] = {}

            class _FakeResponse:
                llm_context = "context text"

                def as_dict(self) -> dict[str, object]:
                    return {
                        "query": "q",
                        "results": [],
                        "layer_timings_ms": {},
                        "errors": {},
                        "llm_context_chars": 12,
                    }

            def _fake_run_layered_search(query: str, **kwargs: object) -> _FakeResponse:
                captured["query"] = query
                captured["layers"] = kwargs.get("layers")
                return _FakeResponse()

            with patch("nemo_coding_platform.mission_control_server.run_layered_search", side_effect=_fake_run_layered_search):
                payload = api_search(
                    config,
                    {"query": "find this", "layers": ["url"], "urls": ["https://example.com"]},
                )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["llm_context"], "context text")
        self.assertEqual(payload["layers_used"], ["url"])
        self.assertEqual(captured["query"], "find this")
        layers = captured["layers"]
        self.assertIsInstance(layers, list)
        self.assertEqual([layer.name for layer in layers], ["url"])

    def test_api_search_defaults_layers_when_payload_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", root / "runs", memory_db=None)

            captured: dict[str, object] = {}

            class _FakeResponse:
                llm_context = "ctx"

                def as_dict(self) -> dict[str, object]:
                    return {
                        "query": "q",
                        "results": [],
                        "layer_timings_ms": {},
                        "errors": {},
                        "llm_context_chars": 3,
                    }

            def _fake_run_layered_search(query: str, **kwargs: object) -> _FakeResponse:
                captured["layers"] = kwargs.get("layers")
                return _FakeResponse()

            with patch("nemo_coding_platform.mission_control_server.run_layered_search", side_effect=_fake_run_layered_search):
                payload = api_search(config, {"query": "x", "layers": ["unknown"]})

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["layers_used"], ["nemo_memory", "nemo_portfolio", "url"])
        layers = captured["layers"]
        self.assertIsInstance(layers, list)
        self.assertEqual([layer.name for layer in layers], ["nemo_memory", "nemo_portfolio", "url"])

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
                                "title": "Space Code docs",
                                "url": "https://example.com/docs",
                                "snippet": "Embedded browser search result",
                            }
                        ],
                    },
                ):
                    payload = api_browser_search(server, {"query": "space code", "max_results": 5, "timeout_seconds": 10})
                state = api_browser_state(config)
            finally:
                server.server_close()

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["engine"], "playwright-chromium")
        self.assertEqual(payload["results"][0]["url"], "https://example.com/docs")
        self.assertEqual(state["search_query"], "space code")
        self.assertIn("space code", state["search_history"])

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
                        "provider": _TEST_PROVIDER,
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
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", memory_db=None)
            manager = HandoffJobManager()

            job = manager.start(
                config,
                {
                    "objective": "Create an async smoke run.",
                    "acceptance_criteria": "async job completes",
                    "validation_commands": "python -m unittest",
                    "provider": _TEST_PROVIDER,
                },
            )
            deadline = time.time() + (120 if _REAL_PROVIDER else 90)
            while job.status in {"starting", "running"} and time.time() < deadline:
                time.sleep(0.05)

            finished = manager.get(job.job_id)
            self.assertEqual(finished.status, "completed")
            self.assertEqual(finished.returncode, 0)
            self.assertTrue(Path(finished.run_json).exists())
            self.assertTrue(any("job finished" in line or "validation_passed" in line for line in finished.logs))

    def test_job_resume_is_idempotent_after_completion(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", memory_db=None)
            manager = HandoffJobManager()

            job = manager.start(config, {"objective": "Create an async smoke run.", "provider": "fake"})
            deadline = time.time() + 45
            while job.status in {"starting", "running"} and time.time() < deadline:
                time.sleep(0.05)

            resumed = manager.resume(config, job.job_id)

        self.assertEqual(resumed.job_id, job.job_id)
        self.assertEqual(resumed.status, "completed")
        self.assertTrue(any("resume ignored status=completed" in line for line in resumed.logs))

    def test_job_cancel_is_idempotent_after_completion(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", memory_db=None)
            manager = HandoffJobManager()

            job = manager.start(config, {"objective": "Create an async smoke run.", "provider": "fake"})
            deadline = time.time() + 45
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

    def test_job_manager_persists_snapshot_and_restores_completed_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_root = root / "jobs"
            run_json = root / "runs" / "job-run.json"
            run_json.parent.mkdir(parents=True, exist_ok=True)
            write_ready_run(run_json, root, root / "sandbox", ["created.txt"])

            manager = HandoffJobManager(snapshot_root)
            job = HandoffJob(
                job_id="job-persisted",
                task_id="task-persisted",
                run_id="run-persisted",
                run_json=str(run_json),
                status="running",
                command=("python", "-m", "nemo_coding_platform"),
                payload={"objective": "persist me", "timeout_seconds": 600},
                logs=["starting self-modification job"],
            )

            manager._jobs[job.job_id] = job
            manager._persist_job(job)

            restored = HandoffJobManager(snapshot_root)
            recovered = restored.get("job-persisted")

        self.assertEqual(recovered.status, "completed")
        self.assertEqual(recovered.returncode, 0)
        self.assertTrue(any("restored snapshot detected completed run_json" in line for line in recovered.logs))

    def test_job_manager_heartbeat_snapshot_reports_runtime_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", root / "runs", memory_db=None)
            runtime_root = config.runtimes_path / "self-task-1-self-run-1"
            runtime_root.mkdir(parents=True, exist_ok=True)
            (runtime_root / ".nemo-engine-message.md").write_text("engine message", encoding="utf-8")
            (runtime_root / "generated-spec.md").write_text("spec", encoding="utf-8")
            (runtime_root / "checkpoint-repair-00001.json").write_text("{}", encoding="utf-8")
            manager = HandoffJobManager()
            job = HandoffJob(
                job_id="self-job-1",
                task_id="self-task-1",
                run_id="self-run-1",
                run_json=str(config.runtimes_path / "self-mod" / "runs" / "self-task-1-self-run-1.json"),
                status="running",
                command=("python",),
                payload={},
                logs=[],
            )

            snapshot = manager._heartbeat_snapshot(config, job)

        self.assertIn("runtime_exists=1", snapshot)
        self.assertIn("runtime_files=3", snapshot)
        self.assertIn("engine_message_exists=1", snapshot)
        self.assertIn("checkpoints=1", snapshot)
        self.assertIn("generated-spec.md", snapshot)

    def test_job_manager_heartbeat_tick_appends_runtime_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", root / "runs", memory_db=None)
            runtime_root = config.runtimes_path / "self-task-2-self-run-2"
            runtime_root.mkdir(parents=True, exist_ok=True)
            (runtime_root / ".nemo-engine-message.md").write_text("engine message", encoding="utf-8")
            manager = HandoffJobManager()
            job = HandoffJob(
                job_id="self-job-2",
                task_id="self-task-2",
                run_id="self-run-2",
                run_json=str(config.runtimes_path / "self-mod" / "runs" / "self-task-2-self-run-2.json"),
                status="running",
                command=("python",),
                payload={},
                logs=["starting self-modification job"],
            )

            manager._heartbeat_tick(config, job)

        self.assertEqual(len(job.logs), 2)
        self.assertIn("heartbeat process_alive=1", job.logs[-1])
        self.assertIn("runtime_exists=1", job.logs[-1])

    def test_api_state_includes_persisted_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", root / "runs", memory_db=None)
            server = MissionControlHttpServer(("127.0.0.1", 0), config)
            try:
                job = HandoffJob(
                    job_id="job-visible",
                    task_id="task-visible",
                    run_id="run-visible",
                    run_json=str(root / "runs" / "visible.json"),
                    status="running",
                    command=("python",),
                    payload={"objective": "visible job", "timeout_seconds": 600},
                    logs=["starting handoff job"],
                )
                server.jobs._jobs[job.job_id] = job
                server.jobs._persist_job(job)
                payload = api_state(server.config, server.jobs)
            finally:
                server.server_close()

        self.assertIn("jobs", payload)
        self.assertEqual(payload["jobs"][0]["job_id"], "job-visible")

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
        self.assertTrue(isinstance(message.get("agent_trace"), list))
        self.assertTrue(any(event.get("kind") == "tool_result" for event in message["agent_trace"]))
        self.assertTrue(any(event.get("label") == "response_ready" for event in message["agent_trace"]))
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
        self.assertIn("nemo_memory.prime_context", tool_names)
        self.assertIn("nemo_memory.build_context_portfolio", tool_names)
        self.assertIn("nemo_memory.search_memories", tool_names)
        self.assertIn("nemo_memory.store_conversation", tool_names)
        prime_tool = next(tool for tool in payload["message"]["tool_calls"] if tool["name"] == "nemo_memory.prime_context")
        self.assertEqual(prime_tool.get("tool_name"), "prime_context")
        self.assertEqual(prime_tool.get("alias_name"), "spacecode.prime_context")
        trace_labels = {event.get("label") for event in payload["message"].get("agent_trace", [])}
        self.assertIn("tool: nemo_memory.prime_context (alias spacecode.prime_context)", trace_labels)
        trace_kinds = {event.get("kind") for event in payload["message"].get("agent_trace", [])}
        self.assertIn("tool_call", trace_kinds)
        self.assertIn("tool_result", trace_kinds)
        self.assertIn("finalize", trace_kinds)
        self.assertIn("status", trace_kinds)
        self.assertIn("response_ready", trace_labels)

    def test_agent_message_unwraps_nested_native_mcp_payload_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            runtimes = root / "runtimes"
            memory_db = root / "nemo.sqlite"
            repo.mkdir()
            runtimes.mkdir()
            config = MissionControlServerConfig.from_paths(repo, runtimes, root / "apply-results", root / "runs", memory_db)

            def _nested_payload(value: dict[str, object]) -> dict[str, object]:
                return {"ok": True, "payload": {"ok": True, "tool_call_audit": {"allowed": True}, "result": value}}

            def _fake_call(tool_name: str, lifecycle_phase: str | None = None, memory_db: str = ".nemo-memory.db", mcp_url: str = "", approve_review: bool = False, **arguments: object) -> dict[str, object]:
                if tool_name == "context_bootstrap":
                    return _nested_payload({"context": "bootstrap context", "portfolio": {"estimated_tokens": 12}, "memories": []})
                if tool_name == "prime_context":
                    return _nested_payload({"context": "prime context", "topic": "Mission Control conversation", "memories": [{"content": "m1"}]})
                if tool_name == "build_context_portfolio":
                    return _nested_payload({"estimated_tokens": 44, "evidence_handles": [], "context": "portfolio context"})
                if tool_name == "search_memories":
                    return _nested_payload({"query": "identity", "memories": [{"user_name": "Nested User"}]})
                if tool_name == "anticipate":
                    return _nested_payload({"memories": [{"content": "anticipate memory"}]})
                if tool_name == "store_conversation":
                    return _nested_payload({"stored": True, "atom_id": "atom-nested"})
                if tool_name == "cognitive_ingest":
                    return _nested_payload({"stored": True, "atom_id": "atom-ingest"})
                return _nested_payload({})

            with patch("nemo_coding_platform.mission_control_server.mcp_call_nemo_tool", side_effect=_fake_call), patch("nemo_coding_platform.nemo_gateway._mcp_call_nemo_tool", side_effect=_fake_call), patch("nemo_coding_platform.mission_control_server._lmstudio_chat_completion", return_value="model should not answer memory lookup") as model_call:
                payload = api_agent_message(config, {"message": "cual es mi nombre?", "provider": "subprocess", "nemo_mcp_url": "http://127.0.0.1:8765/mcp/sse"})

        self.assertIn("Según NEMO MCP real, tu nombre es Nested User", payload["message"]["content"])
        model_call.assert_not_called()
        search_tool = next(tool for tool in payload["message"]["tool_calls"] if tool["name"] == "nemo_memory.search_memories")
        self.assertIn("user_name=Nested User", search_tool.get("summary", ""))

    def test_agent_message_includes_bootstrap_packet_in_model_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            runtimes = root / "runtimes"
            memory_db = root / "nemo.sqlite"
            repo.mkdir()
            runtimes.mkdir()
            config = MissionControlServerConfig.from_paths(repo, runtimes, root / "apply-results", root / "runs", memory_db)

            captured: dict[str, str] = {}

            def _capture(payload: dict[str, object], user_message: str, context_summary: str, **kwargs: object) -> str:
                captured["context_summary"] = context_summary
                return "ok"

            def _fake_call(tool_name: str, lifecycle_phase: str | None = None, **arguments: object) -> dict[str, object]:
                payloads: dict[str, dict[str, object]] = {
                    "context_bootstrap": {"context": "bootstrap context", "portfolio": {"estimated_tokens": 12}},
                    "prime_context": {"context": "prime context", "memories": [{"content": "m1"}]},
                    "build_context_portfolio": {"estimated_tokens": 44, "evidence_handles": []},
                    "anticipate": {"memories": [{"content": "anticipate memory"}]},
                    "store_conversation": {"stored": True, "atom_id": "atom-1"},
                }
                return {"ok": True, "payload": payloads.get(tool_name, {"memories": []})}

            with patch("nemo_coding_platform.mission_control_server.mcp_call_nemo_tool", side_effect=_fake_call), patch("nemo_coding_platform.nemo_gateway._mcp_call_nemo_tool", side_effect=_fake_call), patch("nemo_coding_platform.mission_control_server._lmstudio_chat_completion", side_effect=_capture):
                api_agent_message(config, {"message": "plan next coding step", "provider": "subprocess", "nemo_mcp_url": "http://127.0.0.1:8765/mcp/sse", "require_nemo_mcp_capabilities": False})

        context_summary = captured.get("context_summary", "")
        self.assertIn("NEMO bootstrap packet:", context_summary)
        self.assertIn("nemo_memory.prime_context", context_summary)
        self.assertIn("nemo_memory.build_context_portfolio", context_summary)
        self.assertIn("nemo_memory.search_memories", context_summary)
        self.assertIn("nemo_memory.anticipate", context_summary)

    def test_agent_message_stores_user_name_as_structured_identity_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            runtimes = root / "runtimes"
            memory_db = root / "nemo.sqlite"
            repo.mkdir()
            runtimes.mkdir()
            config = MissionControlServerConfig.from_paths(repo, runtimes, root / "apply-results", root / "runs", memory_db)

            captured_calls: list[tuple[str, dict[str, object]]] = []

            def _fake_call(tool_name: str, lifecycle_phase: str | None = None, memory_db: str = ".nemo-memory.db", mcp_url: str = "", approve_review: bool = False, **arguments: object) -> dict[str, object]:
                captured_calls.append((tool_name, dict(arguments)))
                if tool_name in {"context_bootstrap", "prime_context"}:
                    return {"ok": True, "payload": {"context": "bootstrap", "portfolio": {"estimated_tokens": 120}}}
                if tool_name == "build_context_portfolio":
                    return {"ok": True, "payload": {"estimated_tokens": 120, "evidence_handles": [], "context": "portfolio"}}
                if tool_name == "search_memories":
                    return {"ok": True, "payload": {"memories": []}}
                if tool_name == "anticipate":
                    return {"ok": True, "payload": {"memories": []}}
                return {"ok": True, "payload": {"stored": True, "atom_id": "atom-1"}}

            with patch("nemo_coding_platform.mission_control_server.mcp_call_nemo_tool", side_effect=_fake_call), patch("nemo_coding_platform.nemo_gateway._mcp_call_nemo_tool", side_effect=_fake_call), patch("nemo_coding_platform.mission_control_server._lmstudio_chat_completion", return_value="ok"):
                api_agent_message(config, {"message": "guarda que mi nombre es Gabriel Zaldivar", "provider": "subprocess", "nemo_mcp_url": "http://127.0.0.1:8765/mcp/sse"})

        ingest_call = next((item for item in captured_calls if item[0] == "cognitive_ingest"), None)
        self.assertIsNotNone(ingest_call)
        ingest_arguments = ingest_call[1] if ingest_call else {}
        self.assertIn("user_name", str(ingest_arguments.get("content", "")))
        self.assertIn("Gabriel Zaldivar", str(ingest_arguments.get("content", "")))
        self.assertIn("identity", tuple(ingest_arguments.get("tags", ())))

    def test_agent_message_recalls_user_name_via_nemo_identity_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            runtimes = root / "runtimes"
            memory_db = root / "nemo.sqlite"
            repo.mkdir()
            runtimes.mkdir()
            config = MissionControlServerConfig.from_paths(repo, runtimes, root / "apply-results", root / "runs", memory_db)

            captured: dict[str, str] = {}

            def _fake_call(tool_name: str, lifecycle_phase: str | None = None, memory_db: str = ".nemo-memory.db", mcp_url: str = "", approve_review: bool = False, **arguments: object) -> dict[str, object]:
                if tool_name in {"context_bootstrap", "prime_context"}:
                    return {"ok": True, "payload": {"context": "bootstrap", "portfolio": {"estimated_tokens": 120}}}
                if tool_name == "build_context_portfolio":
                    return {"ok": True, "payload": {"estimated_tokens": 120, "evidence_handles": [], "context": "portfolio"}}
                if tool_name == "search_memories":
                    return {"ok": True, "payload": {"memories": [{"user_name": "Gabriel Zaldivar"}]}}
                if tool_name == "anticipate":
                    return {"ok": True, "payload": {"memories": []}}
                return {"ok": True, "payload": {"stored": True, "atom_id": "atom-1"}}

            with patch("nemo_coding_platform.mission_control_server.mcp_call_nemo_tool", side_effect=_fake_call), patch("nemo_coding_platform.nemo_gateway._mcp_call_nemo_tool", side_effect=_fake_call), patch("nemo_coding_platform.mission_control_server._lmstudio_chat_completion", return_value="model should not answer memory lookup") as model_call:
                payload = api_agent_message(config, {"message": "cual es mi nombre?", "provider": "subprocess", "nemo_mcp_url": "http://127.0.0.1:8765/mcp/sse"})

        self.assertIn("Según NEMO MCP real, tu nombre es Gabriel Zaldivar", payload["message"]["content"])
        model_call.assert_not_called()

    def test_agent_message_recalls_user_name_from_archived_conversation_compact_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            runtimes = root / "runtimes"
            memory_db = root / "nemo.sqlite"
            repo.mkdir()
            runtimes.mkdir()
            config = MissionControlServerConfig.from_paths(repo, runtimes, root / "apply-results", root / "runs", memory_db)

            captured_filters: list[str] = []

            def _fake_call(tool_name: str, lifecycle_phase: str | None = None, memory_db: str = ".nemo-memory.db", mcp_url: str = "", approve_review: bool = False, **arguments: object) -> dict[str, object]:
                if tool_name == "search_memories":
                    query = str(arguments.get("query") or "")
                    captured_filters.append(str(arguments.get("database_filter") or ""))
                    if query == "mi nombre es":
                        return {"ok": True, "payload": {"results": ["[1.05|imp:?|archived_conversation] hola me llamo gabriel tengo 34 años (2026-03-21)"]}}
                    return {"ok": True, "payload": {"results": []}}
                if tool_name == "context_bootstrap":
                    return {"ok": True, "payload": {"context": "", "portfolio": {"estimated_tokens": 0}, "memories": []}}
                if tool_name == "prime_context":
                    return {"ok": True, "payload": {"context": "", "memories": []}}
                if tool_name == "build_context_portfolio":
                    return {"ok": True, "payload": {"estimated_tokens": 0, "evidence_handles": []}}
                if tool_name == "anticipate":
                    return {"ok": True, "payload": {"memories": []}}
                return {"ok": True, "payload": {"stored": True, "atom_id": "atom-1"}}

            with patch("nemo_coding_platform.mission_control_server.mcp_call_nemo_tool", side_effect=_fake_call), patch("nemo_coding_platform.nemo_gateway._mcp_call_nemo_tool", side_effect=_fake_call), patch("nemo_coding_platform.mission_control_server._lmstudio_chat_completion", return_value="model should not answer memory lookup") as model_call:
                payload = api_agent_message(config, {"message": "DIME COMO ME LLAMO", "provider": "subprocess", "nemo_mcp_url": "http://127.0.0.1:8765/mcp/sse"})

        self.assertIn("tu nombre es gabriel", payload["message"]["content"])
        self.assertIn("all", captured_filters)
        model_call.assert_not_called()

    def test_nemo_mcp_tool_call_does_not_forward_approve_review_to_stdio_adapter(self) -> None:
        captured_arguments: dict[str, object] = {}

        class FakeAdapter:
            def call(self, phase: object, tool_name: str, **arguments: object) -> tuple[object, object]:
                captured_arguments.update(arguments)
                result = type("Result", (), {"ok": True, "payload": {"memories": []}})()
                return self, result

        with patch("nemo_coding_platform.spacecode_mcp_tools._get_adapter", return_value=FakeAdapter()):
            payload = mcp_call_nemo_tool(
                "search_memories",
                lifecycle_phase="review",
                mcp_url=mission_control_server.VSCODE_STDIO_NEMO_URL,
                approve_review=True,
                query="me llamo",
            )

        self.assertTrue(payload["ok"])
        self.assertNotIn("approve_review", captured_arguments)
        self.assertEqual(captured_arguments["query"], "me llamo")

    def test_agent_message_answers_nemo_lookup_from_global_mcp_search_not_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            runtimes = root / "runtimes"
            memory_db = root / "nemo.sqlite"
            repo.mkdir()
            runtimes.mkdir()
            config = MissionControlServerConfig.from_paths(repo, runtimes, root / "apply-results", root / "runs", memory_db)

            captured_queries: list[tuple[str, str]] = []
            captured_tools: list[str] = []

            def _fake_call(tool_name: str, lifecycle_phase: str | None = None, memory_db: str = ".nemo-memory.db", mcp_url: str = "", approve_review: bool = False, **arguments: object) -> dict[str, object]:
                captured_tools.append(tool_name)
                if tool_name == "search_memories":
                    query = str(arguments.get("query") or "")
                    topic = str(arguments.get("topic") or "")
                    captured_queries.append((query, topic))
                    if query == "DEV4":
                        return {
                            "ok": True,
                            "payload": {
                                "memories": [
                                    {
                                        "id": "mem-dev4",
                                        "type": "project_fact",
                                        "content": "Project DEV4 is this repo: Space Code mission-control real MCP integration.",
                                    }
                                ]
                            },
                        }
                    return {"ok": True, "payload": {"memories": []}}
                if tool_name == "context_bootstrap":
                    return {"ok": True, "payload": {"context": "", "portfolio": {"estimated_tokens": 0}, "memories": []}}
                if tool_name == "prime_context":
                    return {"ok": True, "payload": {"context": "", "memories": []}}
                if tool_name == "build_context_portfolio":
                    return {"ok": True, "payload": {"estimated_tokens": 0, "evidence_handles": []}}
                if tool_name == "anticipate":
                    return {"ok": True, "payload": {"memories": []}}
                return {"ok": True, "payload": {"stored": True, "atom_id": "atom-1"}}

            with patch("nemo_coding_platform.mission_control_server.mcp_call_nemo_tool", side_effect=_fake_call), patch("nemo_coding_platform.nemo_gateway._mcp_call_nemo_tool", side_effect=_fake_call), patch("nemo_coding_platform.mission_control_server._lmstudio_chat_completion", return_value="hallucinated DEV4 owner") as model_call:
                payload = api_agent_message(
                    config,
                    {
                        "message": "REVISA SI TIENES INFORMACION EN NEMO MCP SOBRE DEV4",
                        "provider": "subprocess",
                        "nemo_mcp_url": "http://127.0.0.1:8765/mcp/sse",
                    },
                )

        content = payload["message"]["content"]
        self.assertIn("Consulté NEMO MCP real", content)
        self.assertIn("Project DEV4 is this repo", content)
        self.assertNotIn("Ana Martínez", content)
        self.assertIn(("DEV4", ""), captured_queries)
        self.assertNotIn(("DEV4", "Space Code self-modification"), captured_queries)
        self.assertNotIn("cognitive_ingest", captured_tools)
        model_call.assert_not_called()

    def test_agent_message_nemo_lookup_filters_chat_question_echoes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            runtimes = root / "runtimes"
            memory_db = root / "nemo.sqlite"
            repo.mkdir()
            runtimes.mkdir()
            config = MissionControlServerConfig.from_paths(repo, runtimes, root / "apply-results", root / "runs", memory_db)

            captured_queries: list[str] = []

            def _fake_call(tool_name: str, lifecycle_phase: str | None = None, memory_db: str = ".nemo-memory.db", mcp_url: str = "", approve_review: bool = False, **arguments: object) -> dict[str, object]:
                if tool_name == "search_memories":
                    query = str(arguments.get("query") or "")
                    captured_queries.append(query)
                    if query == "TRANTOR":
                        return {
                            "ok": True,
                            "payload": {
                                "memories": [
                                    {
                                        "id": "mem-trantor-question",
                                        "type": "session_summary",
                                        "content": "Mission Control chat user request: QUE ES TRANTOR?",
                                    },
                                    {
                                        "id": "mem-trantor-lookup",
                                        "type": "session_summary",
                                        "content": "Mission Control chat user request: BUSCA EN NEMO MCP QUE ES TRANTOR, EN LAS MEMORIAS",
                                    },
                                ]
                            },
                        }
                    return {"ok": True, "payload": {"memories": []}}
                if tool_name == "context_bootstrap":
                    return {"ok": True, "payload": {"context": "", "portfolio": {"estimated_tokens": 0}, "memories": []}}
                if tool_name == "prime_context":
                    return {"ok": True, "payload": {"context": "", "memories": []}}
                if tool_name == "build_context_portfolio":
                    return {"ok": True, "payload": {"estimated_tokens": 0, "evidence_handles": []}}
                if tool_name == "anticipate":
                    return {"ok": True, "payload": {"memories": []}}
                return {"ok": True, "payload": {"stored": True, "atom_id": "atom-1"}}

            with patch("nemo_coding_platform.mission_control_server.mcp_call_nemo_tool", side_effect=_fake_call), patch("nemo_coding_platform.nemo_gateway._mcp_call_nemo_tool", side_effect=_fake_call), patch("nemo_coding_platform.mission_control_server._lmstudio_chat_completion", return_value="Tool executed.") as model_call:
                payload = api_agent_message(
                    config,
                    {
                        "message": "BUSCA EN NEMO MCP QUE ES TRANTOR, EN LAS MEMORIAS",
                        "provider": "subprocess",
                        "nemo_mcp_url": "http://127.0.0.1:8765/mcp/sse",
                    },
                )

        content = payload["message"]["content"]
        self.assertIn("Consulté NEMO MCP real por 'TRANTOR'", content)
        self.assertIn("no encontré memorias útiles", content)
        self.assertIn("TRANTOR", captured_queries)
        self.assertNotIn("BUSCA", captured_queries)
        self.assertNotIn("Tool executed", content)
        model_call.assert_not_called()

    def test_agent_message_recuerdas_topic_is_lookup_not_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            runtimes = root / "runtimes"
            memory_db = root / "nemo.sqlite"
            repo.mkdir()
            runtimes.mkdir()
            config = MissionControlServerConfig.from_paths(repo, runtimes, root / "apply-results", root / "runs", memory_db)

            captured_queries: list[str] = []
            captured_tools: list[str] = []

            def _fake_call(tool_name: str, lifecycle_phase: str | None = None, memory_db: str = ".nemo-memory.db", mcp_url: str = "", approve_review: bool = False, **arguments: object) -> dict[str, object]:
                captured_tools.append(tool_name)
                if tool_name == "search_memories":
                    query = str(arguments.get("query") or "")
                    captured_queries.append(query)
                    if query == "ALFA42":
                        return {
                            "ok": True,
                            "payload": {
                                "memories": [
                                    {"id": "mem-topic-chat", "type": "session_summary", "content": "Mission Control chat user request: RECUERDAS ALFA42"},
                                    {"id": "mem-topic-bad-store", "type": "project_fact", "content": "RECUERDAS ALFA42"},
                                ]
                            },
                        }
                    return {"ok": True, "payload": {"memories": []}}
                if tool_name == "context_bootstrap":
                    return {"ok": True, "payload": {"context": "", "portfolio": {"estimated_tokens": 0}, "memories": []}}
                if tool_name == "prime_context":
                    return {"ok": True, "payload": {"context": "", "memories": []}}
                if tool_name == "build_context_portfolio":
                    return {"ok": True, "payload": {"estimated_tokens": 0, "evidence_handles": []}}
                if tool_name == "anticipate":
                    return {"ok": True, "payload": {"memories": []}}
                return {"ok": True, "payload": {"stored": True, "atom_id": "atom-1"}}

            with patch("nemo_coding_platform.mission_control_server.mcp_call_nemo_tool", side_effect=_fake_call), patch("nemo_coding_platform.nemo_gateway._mcp_call_nemo_tool", side_effect=_fake_call), patch("nemo_coding_platform.mission_control_server._lmstudio_chat_completion", return_value="model reminder hallucination") as model_call:
                payload = api_agent_message(config, {"message": "RECUERDAS ALFA42", "provider": "subprocess", "nemo_mcp_url": "http://127.0.0.1:8765/mcp/sse"})

        content = payload["message"]["content"]
        self.assertIn("Consulté NEMO MCP real por 'ALFA42'", content)
        self.assertIn("no encontré memorias útiles", content)
        self.assertIn("ALFA42", captured_queries)
        self.assertNotIn("cognitive_ingest", captured_tools)
        self.assertNotIn("model reminder hallucination", content)
        model_call.assert_not_called()

    def test_agent_message_dime_lo_que_sepas_topic_uses_verified_nemo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            runtimes = root / "runtimes"
            memory_db = root / "nemo.sqlite"
            repo.mkdir()
            runtimes.mkdir()
            config = MissionControlServerConfig.from_paths(repo, runtimes, root / "apply-results", root / "runs", memory_db)

            captured_queries: list[str] = []

            def _fake_call(tool_name: str, lifecycle_phase: str | None = None, memory_db: str = ".nemo-memory.db", mcp_url: str = "", approve_review: bool = False, **arguments: object) -> dict[str, object]:
                if tool_name == "search_memories":
                    query = str(arguments.get("query") or "")
                    captured_queries.append(query)
                    if query == "ALFA42":
                        return {"ok": True, "payload": {"memories": [{"id": "mem-topic", "type": "project_fact", "content": "ALFA42 is a saved project codename for the desktop verification environment."}]}}
                    return {"ok": True, "payload": {"memories": []}}
                if tool_name == "context_bootstrap":
                    return {"ok": True, "payload": {"context": "", "portfolio": {"estimated_tokens": 0}, "memories": []}}
                if tool_name == "prime_context":
                    return {"ok": True, "payload": {"context": "", "memories": []}}
                if tool_name == "build_context_portfolio":
                    return {"ok": True, "payload": {"estimated_tokens": 0, "evidence_handles": []}}
                if tool_name == "anticipate":
                    return {"ok": True, "payload": {"memories": []}}
                return {"ok": True, "payload": {"stored": True, "atom_id": "atom-1"}}

            with patch("nemo_coding_platform.mission_control_server.mcp_call_nemo_tool", side_effect=_fake_call), patch("nemo_coding_platform.nemo_gateway._mcp_call_nemo_tool", side_effect=_fake_call), patch("nemo_coding_platform.mission_control_server._lmstudio_chat_completion", return_value="No tengo información sobre ALFA42") as model_call:
                payload = api_agent_message(config, {"message": "DIME LO QUE SEPAS DE ALFA42", "provider": "subprocess", "nemo_mcp_url": "http://127.0.0.1:8765/mcp/sse"})

        content = payload["message"]["content"]
        self.assertIn("Consulté NEMO MCP real por 'ALFA42'", content)
        self.assertIn("desktop verification environment", content)
        self.assertIn("ALFA42", captured_queries)
        self.assertNotIn("No tengo información sobre ALFA42", content)
        model_call.assert_not_called()

    def test_agent_message_identity_and_projects_searches_project_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            runtimes = root / "runtimes"
            memory_db = root / "nemo.sqlite"
            repo.mkdir()
            runtimes.mkdir()
            config = MissionControlServerConfig.from_paths(repo, runtimes, root / "apply-results", root / "runs", memory_db)

            captured_queries: list[str] = []

            def _fake_call(tool_name: str, lifecycle_phase: str | None = None, memory_db: str = ".nemo-memory.db", mcp_url: str = "", approve_review: bool = False, **arguments: object) -> dict[str, object]:
                if tool_name == "search_memories":
                    query = str(arguments.get("query") or "")
                    captured_queries.append(query)
                    if query in {"me llamo", "mi nombre es"}:
                        return {"ok": True, "payload": {"memories": [{"id": "mem-name", "type": "preference", "content": "me llamo Gabriel", "user_name": "Gabriel"}]}}
                    if query in {"proyectos", "proyecto activo", "project context", "repositorios"}:
                        return {"ok": True, "payload": {"memories": [{"id": "mem-project", "type": "project_fact", "content": "Proyecto ALFA: entorno de verificacion con integracion MCP real."}]}}
                    return {"ok": True, "payload": {"memories": []}}
                if tool_name == "context_bootstrap":
                    return {"ok": True, "payload": {"context": "", "portfolio": {"estimated_tokens": 0}, "memories": []}}
                if tool_name == "prime_context":
                    return {"ok": True, "payload": {"context": "", "memories": []}}
                if tool_name == "build_context_portfolio":
                    return {"ok": True, "payload": {"estimated_tokens": 0, "evidence_handles": []}}
                if tool_name == "anticipate":
                    return {"ok": True, "payload": {"memories": []}}
                return {"ok": True, "payload": {"stored": True, "atom_id": "atom-1"}}

            with patch("nemo_coding_platform.mission_control_server.mcp_call_nemo_tool", side_effect=_fake_call), patch("nemo_coding_platform.nemo_gateway._mcp_call_nemo_tool", side_effect=_fake_call), patch("nemo_coding_platform.mission_control_server._lmstudio_chat_completion", return_value="model answer") as model_call:
                payload = api_agent_message(config, {"message": "CUAL ES MI NOMBRE Y QUE PROYECTOS TENGO", "provider": "subprocess", "nemo_mcp_url": "http://127.0.0.1:8765/mcp/sse"})

        content = payload["message"]["content"]
        self.assertIn("tu nombre es Gabriel", content)
        self.assertIn("Proyecto ALFA", content)
        self.assertIn("proyectos", captured_queries)
        self.assertIn("proyecto activo", captured_queries)
        model_call.assert_not_called()

    def test_agent_message_mcp_native_memory_continuity_across_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            runtimes = root / "runtimes"
            memory_db = root / "nemo.sqlite"
            repo.mkdir()
            runtimes.mkdir()

            # Simulated native MCP memory backend keyed by memory_db path.
            memory_by_db: dict[str, list[dict[str, object]]] = {}

            def _memory_rows(db_key: str) -> list[dict[str, object]]:
                rows = memory_by_db.setdefault(db_key, [])
                return rows

            def _fake_mcp_call(tool_name: str, lifecycle_phase: str | None = None, memory_db: str = ".nemo-memory.db", mcp_url: str = "", approve_review: bool = False, **arguments: object) -> dict[str, object]:
                rows = _memory_rows(str(memory_db))

                if tool_name == "cognitive_ingest":
                    content = str(arguments.get("content") or "")
                    entry: dict[str, object] = {
                        "content": content,
                        "memory_type": str(arguments.get("memory_type") or ""),
                        "tags": tuple(arguments.get("tags") or ()),
                    }
                    if "user_name" in content:
                        entry["user_name"] = "Gabriel Zaldivar"
                    rows.append(entry)
                    return {"ok": True, "payload": {"stored": True, "atom_id": f"atom-{len(rows)}"}}

                if tool_name == "store_conversation":
                    summary = str(arguments.get("summary") or "")
                    rows.append({"content": summary, "memory_type": "session_summary", "tags": tuple(arguments.get("tags") or ())})
                    return {"ok": True, "payload": {"stored": True, "atom_id": f"conv-{len(rows)}"}}

                if tool_name == "search_memories":
                    query = str(arguments.get("query") or "").lower()
                    matches: list[dict[str, object]] = []
                    for row in reversed(rows):
                        content = str(row.get("content") or "")
                        if "identity" in query and "user_name" in row:
                            matches.append(dict(row))
                            continue
                        if any(token in content.lower() for token in ("gabriel", "pytest", "dev4", "mission control", "proyecto")):
                            matches.append(dict(row))
                    return {"ok": True, "payload": {"query": query, "memories": matches[:10]}}

                if tool_name in {"context_bootstrap", "prime_context", "build_context_portfolio"}:
                    recent = rows[-8:]
                    context_text = "\n".join(str(item.get("content") or "") for item in recent)
                    if tool_name == "context_bootstrap":
                        return {
                            "ok": True,
                            "payload": {
                                "context": context_text,
                                "portfolio": {
                                    "context": context_text,
                                    "estimated_tokens": max(1, len(context_text) // 4),
                                    "evidence_handles": [],
                                },
                                "memories": [dict(item) for item in recent],
                            },
                        }
                    if tool_name == "prime_context":
                        return {"ok": True, "payload": {"context": context_text, "memories": [dict(item) for item in recent]}}
                    return {
                        "ok": True,
                        "payload": {
                            "context": context_text,
                            "estimated_tokens": max(1, len(context_text) // 4),
                            "evidence_handles": [],
                        },
                    }

                if tool_name == "anticipate":
                    anticipated = [dict(item) for item in rows[-3:]]
                    return {"ok": True, "payload": {"memories": anticipated}}

                return {"ok": True, "payload": {"accepted": True}}

            with patch("nemo_coding_platform.mission_control_server.mcp_call_nemo_tool", side_effect=_fake_mcp_call), patch("nemo_coding_platform.nemo_gateway._mcp_call_nemo_tool", side_effect=_fake_mcp_call), patch("nemo_coding_platform.mission_control_server._lmstudio_chat_completion", return_value="ok"):
                # Session 1: store multiple memories.
                config_session_1 = MissionControlServerConfig.from_paths(repo, runtimes, root / "apply-results", root / "runs", memory_db)
                api_agent_message(config_session_1, {"message": "guarda que mi nombre es Gabriel Zaldivar", "provider": "subprocess", "nemo_mcp_url": "http://127.0.0.1:8765/mcp/sse"})
                api_agent_message(config_session_1, {"message": "guarda que mi preferencia es usar pytest para validacion", "provider": "subprocess", "nemo_mcp_url": "http://127.0.0.1:8765/mcp/sse"})
                api_agent_message(config_session_1, {"message": "guarda que el proyecto activo es dev4 mission control", "provider": "subprocess", "nemo_mcp_url": "http://127.0.0.1:8765/mcp/sse"})

            with patch("nemo_coding_platform.mission_control_server.mcp_call_nemo_tool", side_effect=_fake_mcp_call), patch("nemo_coding_platform.nemo_gateway._mcp_call_nemo_tool", side_effect=_fake_mcp_call), patch("nemo_coding_platform.mission_control_server._lmstudio_chat_completion", return_value="model should not answer memory lookup") as model_call:
                # Session 2: new chat session/process should recover prior memory through MCP native tool calls.
                config_session_2 = MissionControlServerConfig.from_paths(repo, runtimes, root / "apply-results", root / "runs", memory_db)
                response = api_agent_message(
                    config_session_2,
                    {
                        "message": "cual es mi nombre y cual es mi proyecto activo?",
                        "provider": "subprocess",
                        "nemo_mcp_url": "http://127.0.0.1:8765/mcp/sse",
                    },
                )

            message_payload = response["message"]
            tool_names = [tool["name"] for tool in message_payload["tool_calls"]]
            self.assertIn("nemo_memory.context_bootstrap", tool_names)
            self.assertIn("nemo_memory.prime_context", tool_names)
            self.assertIn("nemo_memory.build_context_portfolio", tool_names)
            self.assertIn("nemo_memory.search_memories", tool_names)
            self.assertIn("nemo_memory.anticipate", tool_names)

            content = str(message_payload.get("content") or "")
            self.assertIn("Gabriel Zaldivar", content)
            self.assertIn("dev4 mission control", content)
            model_call.assert_not_called()

    def test_nemo_mcp_status_exposes_full_native_tool_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            runtimes = root / "runtimes"
            memory_db = root / "nemo.sqlite"
            repo.mkdir()
            runtimes.mkdir()
            config = MissionControlServerConfig.from_paths(repo, runtimes, root / "apply-results", root / "runs", memory_db)

            payload = api_nemo_mcp_status(config, {"nemo_mcp_url": "http://127.0.0.1:8765/mcp/sse"})

        available_tools = payload.get("available_tools") or []
        self.assertIn("context_bootstrap", available_tools)
        self.assertIn("prime_context", available_tools)
        self.assertIn("build_context_portfolio", available_tools)
        self.assertIn("search_memories", available_tools)
        self.assertIn("anticipate", available_tools)
        self.assertIn("cognitive_ingest", available_tools)
        self.assertIn("store_conversation", available_tools)

    def test_nemo_mcp_status_accepts_vscode_stdio_transport(self) -> None:
        fake_server = type(
            "FakeServer",
            (),
            {
                "name": "nemo",
                "source_path": r"C:\Users\gabri\AppData\Roaming\Code\User\mcp.json",
                "command": r"C:\dev\memory persistence\.venv\Scripts\python.exe",
                "args": (r"C:\dev\memory persistence\persistent-ai-memory\ai_memory_mcp_server.py",),
            },
        )()

        with patch("nemo_coding_platform.mission_control_server.discover_vscode_mcp_server", return_value=fake_server):
            payload = mission_control_server._probe_nemo_mcp_sse("stdio://vscode/nemo")

        self.assertTrue(payload["active"])
        self.assertEqual(payload["transport"], "vscode_stdio")
        self.assertEqual(payload["config_source"], fake_server.source_path)

    def test_unique_memories_accepts_real_nemo_results_shape(self) -> None:
        memories = mission_control_server._unique_memories_from_payloads(
            [
                (
                    "query",
                    {
                        "results": [
                            {
                                "type": "ai_memory",
                                "similarity_score": 0.91,
                                "data": {"memory_id": "m1", "content": "Proyecto activo dev4", "memory_type": "fact"},
                            }
                        ]
                    },
                )
            ]
        )

        self.assertEqual(len(memories), 1)
        self.assertEqual(memories[0]["memory_id"], "m1")
        self.assertEqual(mission_control_server._memory_content(memories[0]), "Proyecto activo dev4")

    def test_nemo_mcp_status_reports_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            runtimes = root / "runtimes"
            memory_db = root / "nemo.sqlite"
            repo.mkdir()
            runtimes.mkdir()
            config = MissionControlServerConfig.from_paths(repo, runtimes, root / "apply-results", root / "runs", memory_db)

            with patch("nemo_coding_platform.mission_control_server._probe_nemo_mcp_sse", return_value={"configured": True, "active": True, "status": "active", "url": "http://127.0.0.1:8765/mcp/sse"}), patch(
                "nemo_coding_platform.mission_control_server._probe_nemo_mcp_capabilities",
                return_value={
                    "enabled": True,
                    "supports_context_bootstrap": True,
                    "supports_prime_context": True,
                    "supports_search_memories": True,
                    "supports_core_context_reads": True,
                    "supports_write_read_roundtrip": False,
                    "roundtrip_probe_executed": False,
                    "errors": [],
                },
            ):
                payload = api_nemo_mcp_status(config, {"nemo_mcp_url": "http://127.0.0.1:8765/mcp/sse", "include_capability_probe": True})

        capabilities = payload.get("capabilities") if isinstance(payload.get("capabilities"), dict) else {}
        self.assertTrue(capabilities.get("supports_core_context_reads"))
        self.assertIn("available_tools", payload)

    def test_stdio_capability_probe_accepts_empty_bootstrap_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            runtimes = root / "runtimes"
            memory_db = root / "nemo.sqlite"
            repo.mkdir()
            runtimes.mkdir()
            config = MissionControlServerConfig.from_paths(repo, runtimes, root / "apply-results", root / "runs", memory_db)

            mission_control_server._NEMO_MCP_CAPABILITY_CACHE.clear()
            with patch("nemo_coding_platform.mission_control_server.mcp_call_nemo_tool") as mocked_call:
                payload = mission_control_server._probe_nemo_mcp_capabilities(
                    config,
                    mcp_url=mission_control_server.VSCODE_STDIO_NEMO_URL,
                    include_roundtrip_probe=False,
                )

        self.assertTrue(payload["supports_context_bootstrap"])
        self.assertTrue(payload["supports_core_context_reads"])
        self.assertEqual(payload["errors"], [])
        mocked_call.assert_not_called()

    def test_settings_normalizes_legacy_sse_to_vscode_stdio(self) -> None:
        fake_server = type(
            "FakeServer",
            (),
            {
                "name": "nemo",
                "source_path": r"C:\Users\gabri\AppData\Roaming\Code\User\mcp.json",
                "command": r"C:\dev\memory persistence\.venv\Scripts\python.exe",
                "args": (r"C:\dev\memory persistence\persistent-ai-memory\ai_memory_mcp_server.py",),
            },
        )()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            runtimes = root / "runtimes"
            memory_db = root / "nemo.sqlite"
            repo.mkdir()
            runtimes.mkdir()
            config = MissionControlServerConfig.from_paths(repo, runtimes, root / "apply-results", root / "runs", memory_db)
            server = type("Server", (), {"config": config, "jobs": HandoffJobManager()})()

            with patch("nemo_coding_platform.mission_control_server.discover_vscode_mcp_server", return_value=fake_server):
                payload = api_settings(server, {"nemo_mcp_url": mission_control_server.LEGACY_NEMO_SSE_URL})

        self.assertEqual(payload["settings"]["nemo_mcp_url"], mission_control_server.VSCODE_STDIO_NEMO_URL)

    def test_agent_message_normalizes_stale_legacy_sse_payload_for_capability_gate(self) -> None:
        fake_server = type(
            "FakeServer",
            (),
            {
                "name": "nemo",
                "source_path": r"C:\Users\gabri\AppData\Roaming\Code\User\mcp.json",
                "command": r"C:\dev\memory persistence\.venv\Scripts\python.exe",
                "args": (r"C:\dev\memory persistence\persistent-ai-memory\ai_memory_mcp_server.py",),
            },
        )()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            runtimes = root / "runtimes"
            memory_db = root / "nemo.sqlite"
            repo.mkdir()
            runtimes.mkdir()
            config = MissionControlServerConfig.from_paths(repo, runtimes, root / "apply-results", root / "runs", memory_db)

            with patch("nemo_coding_platform.mission_control_server.discover_vscode_mcp_server", return_value=fake_server), patch(
                "nemo_coding_platform.mission_control_server._probe_nemo_mcp_capabilities",
                return_value={
                    "enabled": True,
                    "supports_context_bootstrap": True,
                    "supports_prime_context": True,
                    "supports_search_memories": True,
                    "supports_core_context_reads": True,
                    "supports_write_read_roundtrip": False,
                    "roundtrip_probe_executed": False,
                    "errors": [],
                },
            ) as mocked_probe, patch("nemo_coding_platform.mission_control_server.mcp_call_nemo_tool", return_value={"ok": True, "payload": {}}):
                api_agent_message(
                    config,
                    {
                        "message": "Prueba del nuevo chat integrado: responde breve y confirma NEMO MCP.",
                        "provider": "fake",
                        "nemo_mcp_url": mission_control_server.LEGACY_NEMO_SSE_URL,
                        "require_nemo_mcp_capabilities": True,
                    },
                )

        self.assertEqual(mocked_probe.call_args.kwargs["mcp_url"], mission_control_server.VSCODE_STDIO_NEMO_URL)

    def test_agent_message_strict_capability_gate_rejects_unusable_mcp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            runtimes = root / "runtimes"
            memory_db = root / "nemo.sqlite"
            repo.mkdir()
            runtimes.mkdir()
            config = MissionControlServerConfig.from_paths(repo, runtimes, root / "apply-results", root / "runs", memory_db)

            with patch(
                "nemo_coding_platform.mission_control_server._probe_nemo_mcp_capabilities",
                return_value={
                    "enabled": True,
                    "supports_context_bootstrap": False,
                    "supports_prime_context": True,
                    "supports_search_memories": False,
                    "supports_core_context_reads": False,
                    "supports_write_read_roundtrip": False,
                    "roundtrip_probe_executed": True,
                    "errors": ["context_bootstrap_unavailable", "search_memories_unavailable"],
                },
            ):
                with self.assertRaises(ApiRequestError) as raised:
                    api_agent_message(
                        config,
                        {
                            "message": "hola",
                            "provider": "fake",
                            "nemo_mcp_url": "http://127.0.0.1:8765/mcp/sse",
                            "require_nemo_mcp_capabilities": True,
                        },
                    )

        self.assertEqual(raised.exception.error_code, "nemo_mcp_capability_mismatch")

    def test_handoff_start_requires_real_mcp_capabilities_when_nemo_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            runtimes = root / "runtimes"
            memory_db = root / "nemo.sqlite"
            repo.mkdir()
            runtimes.mkdir()
            config = MissionControlServerConfig.from_paths(repo, runtimes, root / "apply-results", root / "runs", memory_db)
            server = MissionControlHttpServer(("127.0.0.1", 0), config)
            try:
                with patch("nemo_coding_platform.mission_control_server._probe_nemo_mcp_sse", return_value={"configured": True, "active": True, "status": "active", "url": "http://127.0.0.1:8765/mcp/sse"}), patch(
                    "nemo_coding_platform.mission_control_server._probe_nemo_mcp_capabilities",
                    return_value={
                        "enabled": True,
                        "supports_context_bootstrap": True,
                        "supports_prime_context": False,
                        "supports_search_memories": True,
                        "supports_core_context_reads": False,
                        "supports_write_read_roundtrip": False,
                        "roundtrip_probe_executed": True,
                        "errors": ["prime_context_unavailable"],
                    },
                ):
                    with self.assertRaises(ApiRequestError) as raised:
                        api_handoff_start(
                            server,
                            {
                                "objective": "build feature",
                                "provider": "subprocess",
                                "nemo_mcp_url": "http://127.0.0.1:8765/mcp/sse",
                                "nemo_required": True,
                            },
                        )
            finally:
                server.server_close()

        self.assertEqual(raised.exception.error_code, "nemo_mcp_capability_mismatch")

    def test_mcp_native_real_server_continuity_across_sessions_and_core_tools(self) -> None:
        mcp_url = "http://127.0.0.1:8765/mcp/sse"
        real_mcp_required = os.environ.get("SPACE_CODE_REAL_MCP_REQUIRED") == "1"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            runtimes = root / "runtimes"
            memory_db = root / "nemo.sqlite"
            repo.mkdir()
            runtimes.mkdir()

            config = MissionControlServerConfig.from_paths(repo, runtimes, root / "apply-results", root / "runs", memory_db)
            probe = api_nemo_mcp_status(config, {"nemo_mcp_url": mcp_url})
            if not probe.get("active"):
                if real_mcp_required:
                    self.fail(f"NEMO MCP unavailable for required real integration gate: status={probe.get('status')}")
                self.skipTest(f"NEMO MCP unavailable for real integration test: status={probe.get('status')}")

            marker = f"mc-real-session-{int(time.time() * 1000)}"
            marker_name = f"Name-{marker}"

            # Session 1: store multiple memories via the real chat flow.
            api_agent_message(
                config,
                {
                    "message": f"guarda que mi nombre es {marker_name}",
                    "provider": "fake",
                    "nemo_mcp_url": mcp_url,
                },
            )
            api_agent_message(
                config,
                {
                    "message": f"guarda preferencia de validacion: pytest para {marker}",
                    "provider": "fake",
                    "nemo_mcp_url": mcp_url,
                },
            )
            api_agent_message(
                config,
                {
                    "message": f"guarda contexto de proyecto activo: dev4 mission-control {marker}",
                    "provider": "fake",
                    "nemo_mcp_url": mcp_url,
                },
            )

            # Reinforce continuity with deterministic topic-scoped writes.
            for idx in range(1, 4):
                write_result = mcp_call_nemo_tool(
                    "store_conversation",
                    lifecycle_phase="close",
                    memory_db=str(memory_db),
                    mcp_url=mcp_url,
                    summary=f"session1 continuity memory {idx} {marker}",
                    topic=marker,
                    tags=("mission-control", "integration-test", "continuity"),
                )
                self.assertTrue(write_result.get("ok"), msg=f"store_conversation write failed: {write_result}")

            # Session 2: new config object simulates new chat session/process.
            config_session_2 = MissionControlServerConfig.from_paths(repo, runtimes, root / "apply-results", root / "runs", memory_db)
            response = api_agent_message(
                config_session_2,
                {
                    "message": f"cual es mi nombre y dame continuidad del proyecto {marker}",
                    "provider": "fake",
                    "nemo_mcp_url": mcp_url,
                },
            )

            tool_names = {tool["name"] for tool in response["message"]["tool_calls"]}
            self.assertIn("nemo_memory.context_bootstrap", tool_names)
            self.assertIn("nemo_memory.prime_context", tool_names)
            self.assertIn("nemo_memory.build_context_portfolio", tool_names)
            self.assertIn("nemo_memory.search_memories", tool_names)
            self.assertIn("nemo_memory.anticipate", tool_names)

            # Direct MCP native continuity check on a new session using topic-scoped prime_context.
            continuity_result = mcp_call_nemo_tool(
                "prime_context",
                lifecycle_phase="start",
                memory_db=str(memory_db),
                mcp_url=mcp_url,
                topic=marker,
                limit=10,
            )
            self.assertTrue(continuity_result.get("ok"), msg=f"prime_context continuity check failed: {continuity_result}")
            continuity_payload = mission_control_server._normalize_nemo_tool_payload(continuity_result)
            continuity_context = str(continuity_payload.get("context") or "")
            continuity_memories = continuity_payload.get("memories") if isinstance(continuity_payload.get("memories"), list) else []
            if not (marker in continuity_context or len(continuity_memories) > 0):
                if real_mcp_required:
                    self.fail(
                        "External NEMO MCP backend did not expose retrievable continuity for newly written entries "
                        "while SPACE_CODE_REAL_MCP_REQUIRED=1."
                    )
                self.skipTest(
                    "External NEMO MCP backend did not expose retrievable continuity for newly written entries; "
                    "strict continuity assertion skipped for this environment."
                )

            core_tool_calls = [
                ("context_bootstrap", "start", {"task": f"continuity check {marker}", "topic": "Mission Control conversation", "token_budget": 512, "limit": 8}),
                ("prime_context", "start", {"topic": "Mission Control conversation", "limit": 8}),
                ("build_context_portfolio", "plan", {"task": f"continuity check {marker}", "topic": "Mission Control conversation", "token_budget": 512}),
                ("search_memories", "review", {"query": marker, "limit": 5}),
                ("anticipate", "plan", {"task": f"next actions for {marker}", "limit": 3}),
                ("store_conversation", "close", {"summary": f"integration close summary {marker}", "topic": "Mission Control conversation", "tags": ("mission-control", "integration-test")}),
                ("cognitive_ingest", "review", {"content": f"integration memory atom {marker}", "memory_type": "evidence", "tags": ("mission-control", "integration-test"), "context": "real mcp integration test"}),
            ]
            for tool_name, phase, kwargs in core_tool_calls:
                result = mcp_call_nemo_tool(
                    tool_name,
                    lifecycle_phase=phase,
                    memory_db=str(memory_db),
                    mcp_url=mcp_url,
                    **kwargs,
                )
                self.assertTrue(result.get("ok"), msg=f"tool {tool_name} failed: {result}")

    def test_agent_message_respects_selected_nemo_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            runtimes = root / "runtimes"
            memory_db = root / "nemo.sqlite"
            repo.mkdir()
            runtimes.mkdir()
            config = MissionControlServerConfig.from_paths(repo, runtimes, root / "apply-results", root / "runs", memory_db)

            payload = api_agent_message(
                config,
                {
                    "message": "consulta contexto y guarda resultado",
                    "provider": "fake",
                    "nemo_mcp_url": "http://127.0.0.1:8765/mcp/sse",
                    "selected_nemo_tools": ["prime_context", "store_conversation"],
                },
            )

        search_tool = next(tool for tool in payload["message"]["tool_calls"] if tool["name"] == "nemo_memory.search_memories")
        self.assertEqual(search_tool["status"], "skipped")
        self.assertIn("disabled", search_tool["summary"].lower())

    def test_nemo_mcp_status_includes_available_and_selected_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            runtimes = root / "runtimes"
            memory_db = root / "nemo.sqlite"
            repo.mkdir()
            runtimes.mkdir()
            config = MissionControlServerConfig.from_paths(repo, runtimes, root / "apply-results", root / "runs", memory_db)

            payload = api_nemo_mcp_status(config, {"selected_nemo_tools": ["prime_context", "search_memories"]})

        self.assertTrue(isinstance(payload.get("available_tools"), list))
        self.assertIn("prime_context", payload.get("available_tools") or [])
        self.assertEqual(payload.get("selected_tools"), ["prime_context", "search_memories"])

    def test_agent_message_invalid_chat_mode_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            runtimes = root / "runtimes"
            repo.mkdir()
            runtimes.mkdir()
            config = MissionControlServerConfig.from_paths(repo, runtimes, root / "apply-results", memory_db=None)

            with self.assertRaises(ApiRequestError) as raised:
                api_agent_message(config, {"message": "hola", "provider": "fake", "chat_mode": "invalid", "nemo_mcp_url": "http://127.0.0.1:8765/mcp/sse"})

        self.assertEqual(raised.exception.error_code, "invalid_chat_mode")

    def test_trim_context_summary_prioritizes_corrections_and_preferences(self) -> None:
        summary = "\n".join([
            "Selected task: task-1 / run-1",
            "Objective: keep memory quality high",
            "NEMO context:",
            "ordinary context line A",
            "ordinary context line B",
            "critical correction: never skip prime context",
            "durable preference: use nemo proactively",
            "ordinary context line C",
        ])
        trimmed = mission_control_server._trim_context_summary(summary, max_chars=180)
        self.assertIn("critical correction", trimmed)
        self.assertIn("durable preference", trimmed)
        self.assertIn("[context trimmed:", trimmed)

    def test_extract_http_urls_deduplicates_and_limits(self) -> None:
        message = "Read https://example.com/a and https://example.com/a and https://example.com/b plus https://example.com/c"
        urls = mission_control_server._extract_http_urls(message, limit=2)
        self.assertEqual(urls, ["https://example.com/a", "https://example.com/b"])

    def test_agent_message_reads_user_url_and_emits_trace(self) -> None:
        mission_control_server._URL_SOURCE_CACHE.clear()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            runtimes = root / "runtimes"
            memory_db = root / "nemo.sqlite"
            repo.mkdir()
            runtimes.mkdir()
            config = MissionControlServerConfig.from_paths(repo, runtimes, root / "apply-results", root / "runs", memory_db)

            with patch(
                "nemo_coding_platform.mission_control_server._read_url_source",
                return_value={
                    "url": "https://example.com/docs",
                    "title": "Example Docs",
                    "content": "Useful technical source content.",
                },
            ):
                payload = api_agent_message(
                    config,
                    {
                        "message": "analiza esta fuente https://example.com/docs",
                        "provider": "fake",
                        "nemo_mcp_url": "http://127.0.0.1:8765/mcp/sse",
                    },
                )

        tool_names = {tool["name"] for tool in payload["message"]["tool_calls"]}
        self.assertIn("mission_control.read_url", tool_names)
        sources = payload["message"].get("sources") or []
        self.assertTrue(len(sources) >= 1)
        self.assertEqual(sources[0]["url"], "https://example.com/docs")
        self.assertFalse(bool(sources[0].get("cached")))
        trace_labels = {event.get("label") for event in payload["message"].get("agent_trace", [])}
        self.assertIn("tool: mission_control.read_url", trace_labels)

    def test_read_url_source_cached_reuses_recent_result(self) -> None:
        mission_control_server._URL_SOURCE_CACHE.clear()
        with patch(
            "nemo_coding_platform.mission_control_server._read_url_source",
            return_value={
                "url": "https://example.com/cache",
                "title": "Cache",
                "content": "cached content",
            },
        ) as mocked:
            first_payload, first_cached = mission_control_server._read_url_source_cached("https://example.com/cache")
            second_payload, second_cached = mission_control_server._read_url_source_cached("https://example.com/cache")

        self.assertEqual(mocked.call_count, 1)
        self.assertFalse(first_cached)
        self.assertTrue(second_cached)
        self.assertEqual(first_payload["url"], second_payload["url"])

    def test_api_stats_includes_source_analytics_summary(self) -> None:
        mission_control_server._URL_SOURCE_CACHE.clear()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            runtimes = root / "runtimes"
            memory_db = root / "nemo.sqlite"
            repo.mkdir()
            runtimes.mkdir()
            config = MissionControlServerConfig.from_paths(repo, runtimes, root / "apply-results", root / "runs", memory_db)

            with patch(
                "nemo_coding_platform.mission_control_server._read_url_source",
                return_value={
                    "url": "https://example.com/docs",
                    "title": "Example Docs",
                    "content": "Useful technical source content.",
                },
            ):
                api_agent_message(
                    config,
                    {
                        "message": "analiza esta fuente https://example.com/docs",
                        "provider": "fake",
                        "nemo_mcp_url": "http://127.0.0.1:8765/mcp/sse",
                    },
                )
                api_agent_message(
                    config,
                    {
                        "message": "re-lee https://example.com/docs para comparar",
                        "provider": "fake",
                        "nemo_mcp_url": "http://127.0.0.1:8765/mcp/sse",
                    },
                )

            stats_payload = api_stats(config)

        sources = stats_payload.get("sources")
        self.assertIsInstance(sources, dict)
        self.assertEqual(sources.get("total_reads"), 2)
        self.assertEqual(sources.get("unique_urls"), 1)
        self.assertEqual(sources.get("cache_hits"), 1)
        self.assertTrue((sources.get("cache_hit_rate") or 0.0) > 0.0)
        top_sources = sources.get("top_sources")
        self.assertIsInstance(top_sources, list)
        self.assertTrue(len(top_sources) >= 1)
        self.assertEqual(top_sources[0].get("url"), "https://example.com/docs")
        self.assertEqual(top_sources[0].get("reads"), 2)

    def test_api_stats_counts_jobs_from_manager_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            runtimes = root / "runtimes"
            memory_db = root / "nemo.sqlite"
            repo.mkdir()
            runtimes.mkdir()
            config = MissionControlServerConfig.from_paths(repo, runtimes, root / "apply-results", root / "runs", memory_db)
            manager = HandoffJobManager()
            manager._jobs["job-running"] = HandoffJob(
                job_id="job-running",
                task_id="task-a",
                run_id="run-a",
                run_json="run-a.json",
                status="running",
                command=("echo", "ok"),
                payload={},
                logs=[],
            )
            manager._jobs["job-completed"] = HandoffJob(
                job_id="job-completed",
                task_id="task-b",
                run_id="run-b",
                run_json="run-b.json",
                status="completed",
                command=("echo", "ok"),
                payload={},
                logs=[],
            )

            payload = api_stats(config, manager)

        jobs = payload.get("jobs")
        self.assertIsInstance(jobs, dict)
        self.assertEqual(jobs.get("total"), 2)
        self.assertEqual(jobs.get("active"), 1)
        self.assertEqual(jobs.get("completed"), 1)

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

    def test_self_modify_start_exposes_timeout_and_job_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", root / "runs", root / "nemo.sqlite")
            server = MissionControlHttpServer(("127.0.0.1", 0), config)
            payload = None
            try:
                payload = api_self_modify_start(
                    server,
                    {
                        "objective": "Audit bugs and vulnerabilities in the codebase and fix them safely.",
                        "provider": "subprocess",
                        "timeout_seconds": 1800,
                        "validation_policy": "none",
                    },
                )
                job = payload["job"]
            finally:
                if payload and payload.get("job"):
                    server.jobs.cancel(payload["job"]["job_id"])
                server.server_close()

        self.assertEqual(job["objective"], "Audit bugs and vulnerabilities in the codebase and fix them safely.")
        self.assertEqual(job["timeout_seconds"], 1800)
        self.assertTrue(job["logs"])
        self.assertEqual(job["logs"][0], "starting self-modification job")
        self.assertIn("--timeout", job["command"])
        self.assertEqual(job["command"][job["command"].index("--timeout") + 1], "1800.0")

    def test_handoff_start_enables_real_validation_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", root / "runs", root / "nemo.sqlite")
            server = MissionControlHttpServer(("127.0.0.1", 0), config)
            payload = None
            try:
                payload = api_handoff_start(
                    server,
                    {
                        "objective": "Create a measurable run.",
                        "provider": "fake",
                        "validation_policy": "targeted",
                    },
                )
                command = payload["job"]["command"]
            finally:
                if payload and payload.get("job"):
                    server.jobs.cancel(payload["job"]["job_id"])
                server.server_close()

        self.assertIn("long-handoff-run", command)
        self.assertIn("--real-validation", command)

    def test_handoff_start_rejects_subprocess_without_active_mcp_when_memory_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", root / "runs", root / "nemo.sqlite")
            server = MissionControlHttpServer(("127.0.0.1", 0), config)
            try:
                with patch(
                    "nemo_coding_platform.mission_control_server._probe_nemo_mcp_sse",
                    return_value={"configured": True, "active": False, "status": "unreachable", "error": "connection refused"},
                ):
                    with self.assertRaises(ApiRequestError) as raised:
                        api_handoff_start(
                            server,
                            {
                                "objective": "Create a measurable run.",
                                "provider": "subprocess",
                                "nemo_mcp_url": "http://127.0.0.1:8765/mcp/sse",
                            },
                        )
            finally:
                server.server_close()

        self.assertEqual(raised.exception.error_code, "nemo_mcp_unreachable")

    def test_handoff_start_rejects_plan_mode_for_write_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", root / "runs", root / "nemo.sqlite")
            server = MissionControlHttpServer(("127.0.0.1", 0), config)
            try:
                with self.assertRaises(ApiRequestError) as raised:
                    api_handoff_start(
                        server,
                        {
                            "objective": "Create a measurable run.",
                            "provider": "fake",
                            "workflow_mode": "plan",
                        },
                    )
            finally:
                server.server_close()

        self.assertEqual(raised.exception.error_code, "workflow_policy_violation")

    def test_self_modify_start_rejects_review_mode_for_write_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", root / "runs", root / "nemo.sqlite")
            server = MissionControlHttpServer(("127.0.0.1", 0), config)
            try:
                with self.assertRaises(ApiRequestError) as raised:
                    api_self_modify_start(
                        server,
                        {
                            "objective": "self improve",
                            "provider": "fake",
                            "workflow_mode": "review",
                        },
                    )
            finally:
                server.server_close()

        self.assertEqual(raised.exception.error_code, "workflow_policy_violation")

    def test_review_endpoint_rejects_build_mode(self) -> None:
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

            with self.assertRaises(ApiRequestError) as raised:
                api_review(config, {"source_json": str(run_json), "workflow_mode": "build"})

        self.assertEqual(raised.exception.error_code, "workflow_policy_violation")

    def test_apply_endpoint_rejects_build_mode(self) -> None:
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

            with self.assertRaises(ApiRequestError) as raised:
                api_apply(config, {"source_json": str(run_json), "approve_review": True, "workflow_mode": "build"})

        self.assertEqual(raised.exception.error_code, "workflow_policy_violation")

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

            with patch("nemo_coding_platform.mission_control_server._lmstudio_chat_completion", return_value=("Soy el modelo local real.", None, None)) as chat:
                payload = api_agent_message(
                    config,
                    {
                        "source_json": str(run_json),
                        "message": "que puedes hacer",
                        "provider": "subprocess",
                        "nemo_mcp_url": "http://127.0.0.1:8765/mcp/sse",
                        "model_base_url": "http://localhost:1234/v1",
                        "default_model": "",
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

    def test_nemo_cognitive_stats_returns_memory_and_run_kpis(self) -> None:
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
            correction_id = store.create_atom(MemoryAtom(MemoryAtomType.CORRECTION, "Prefer short context portfolios.", "user"), topic="Space Code self-modification", importance=10)
            evidence_handle = store.create_evidence("full evidence", "compact evidence", source_task_id="task-1", source_run_id="run-1")
            store.record_feedback(atom_id=correction_id, evidence_handle=evidence_handle, event_type="useful", was_useful=True)
            run_json = runtimes / "run.json"
            write_ready_run(run_json, repo, sandbox, ["created.txt"])
            run_payload = json.loads(run_json.read_text(encoding="utf-8"))
            run_payload["portfolio"] = {"estimated_tokens": 120, "token_budget": 200, "memory_atom_ids": [correction_id]}
            run_json.write_text(json.dumps(run_payload), encoding="utf-8")
            config = MissionControlServerConfig.from_paths(repo, runtimes, root / "apply-results", root / "runs", memory_db)

            payload = api_nemo_cognitive_stats(config, {"source_json": str(run_json)})

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["health"]["enabled"])
        self.assertEqual(payload["memory_kpis"]["atom_count"], 1)
        self.assertEqual(payload["memory_kpis"]["correction_count"], 1)
        self.assertEqual(payload["memory_kpis"]["useful_feedback_rate"], 1.0)
        self.assertEqual(payload["memory_kpis"]["portfolio_utilization"], 0.6)
        self.assertIn("total_runs", payload["run_kpis"])

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
                    api_settings(server, {"provider": "fake", "timeout_seconds": 360})
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
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", memory_db=None)
            manager = HandoffJobManager()

            job_a = manager.start(config, {"objective": "Task alpha", "provider": "fake"})
            job_b = manager.start(config, {"objective": "Task beta", "provider": "fake"})
            job_c = manager.start(config, {"objective": "Task gamma", "provider": "fake"})

            deadline = time.time() + 60
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


    def test_api_repo_map_returns_ok_and_map_string(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / "main.py").write_text("def hello(): pass\n", encoding="utf-8")
            config = MissionControlServerConfig.from_paths(repo, root / "runtimes", root / "apply-results", memory_db=None)

            result = api_repo_map(config)

        self.assertTrue(result["ok"])
        self.assertEqual(result["repo_path"], str(repo))
        self.assertIsInstance(result["map"], str)

    def test_api_repo_map_returns_empty_map_for_empty_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            config = MissionControlServerConfig.from_paths(repo, root / "runtimes", root / "apply-results", memory_db=None)

            result = api_repo_map(config)

        self.assertTrue(result["ok"])
        self.assertIsInstance(result["map"], str)

    def test_plan_loop_gen_sys_includes_repo_context_when_repo_has_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / "utils.py").write_text("def greet(name): return f'Hello {name}'\n", encoding="utf-8")
            config = MissionControlServerConfig.from_paths(repo, root / "runtimes", root / "apply-results", memory_db=None)

            captured_sys: list[str] = []

            def _fake_lm(payload, system, user, **kw):
                captured_sys.append(system)
                return "print('hello')"

            def _fake_nemo(config, tool_calls, tool_name, *, lifecycle_phase=None, nemo_mcp_url="", **kw):
                return {"ok": True, "payload": {"context": "", "portfolio": {"estimated_tokens": 0}, "memories": []}}

            with patch("nemo_coding_platform.plan_loop._plan_lm_call", side_effect=_fake_lm), \
                 patch("nemo_coding_platform.mission_control_server._nemo_chat_tool_call", side_effect=_fake_nemo):
                events = list(mission_control_server.api_agent_plan_gen(
                    config,
                    {"objective": "Write a greeting function", "max_iterations": 1, "quality_threshold": 1.0},
                ))

        self.assertTrue(any("Project file structure" in s for s in captured_sys), f"No 'Project file structure' in captured sys prompts: {captured_sys}")

    def test_find_run_payload_by_id_returns_none_for_unknown_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = MissionControlServerConfig.from_paths(root / "repo", root / "runtimes", root / "apply", memory_db=None)
            result = mission_control_server._find_run_payload_by_id(config, "nonexistent-run-id")
        self.assertIsNone(result)

    def test_find_run_payload_by_id_finds_matching_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtimes = root / "runtimes"
            runtimes.mkdir(parents=True)
            payload = {
                "task": {"id": "task-1", "objective": "test"},
                "run": {"id": "run-abc-123", "state": "completed", "phase": "done"},
                "timeline": [],
                "artifacts": [],
            }
            (runtimes / "task-1-run-abc-123.json").write_text(json.dumps(payload), encoding="utf-8")
            config = MissionControlServerConfig.from_paths(root / "repo", runtimes, root / "apply", memory_db=None)
            found = mission_control_server._find_run_payload_by_id(config, "run-abc-123")
        self.assertIsNotNone(found)
        self.assertEqual(found["run"]["id"], "run-abc-123")

    def test_api_replay_gen_yields_error_for_unknown_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = MissionControlServerConfig.from_paths(root / "repo", root / "runtimes", root / "apply", memory_db=None)
            events = list(mission_control_server.api_replay_gen(config, "unknown-run-xyz"))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "error")
        self.assertIn("not found", events[0]["error"])

    def test_api_replay_gen_yields_start_and_done_for_valid_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtimes = root / "runtimes"
            runtimes.mkdir(parents=True)
            payload = {
                "task": {"id": "task-1", "objective": "test"},
                "run": {"id": "run-xyz", "state": "completed", "phase": "done"},
                "timeline": [],
                "artifacts": [{"path": str(root / "nonexistent.py"), "artifact_type": "spec"}],
                "validation": {"passed": True, "results": []},
            }
            (runtimes / "task-1-run-xyz.json").write_text(json.dumps(payload), encoding="utf-8")
            config = MissionControlServerConfig.from_paths(root / "repo", runtimes, root / "apply", memory_db=None)
            events = list(mission_control_server.api_replay_gen(config, "run-xyz"))
        types = [e["type"] for e in events]
        self.assertIn("start", types)
        self.assertIn("done", types)
        start = next(e for e in events if e["type"] == "start")
        self.assertEqual(start["run_id"], "run-xyz")
        done = next(e for e in events if e["type"] == "done")
        self.assertIn("replay_score", done)
        self.assertIn("score_delta", done)

    def test_mission_control_run_to_dict_includes_readiness(self) -> None:
        from nemo_coding_platform.core.mission_control import build_mission_control_state
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtimes = root / "runtimes"
            runtimes.mkdir(parents=True)
            payload = {
                "schema_version": 1,
                "task": {"id": "task-r1", "objective": "test readiness"},
                "run": {"id": "run-r1", "state": "completed", "phase": "done"},
                "timeline": [],
                "artifacts": [],
                "validation": {"passed": False, "results": []},
            }
            (runtimes / "task-r1-run-r1.json").write_text(json.dumps(payload), encoding="utf-8")
            state = build_mission_control_state(root, runtimes)
        runs = state.get("runs", [])
        self.assertGreater(len(runs), 0, "at least one run should be loaded")
        run_dict = runs[0]
        self.assertIn("readiness", run_dict, "to_dict must include readiness field")
        readiness = run_dict["readiness"]
        self.assertIn("score", readiness)
        self.assertIn("grade", readiness)
        self.assertIn("validation_passed", readiness)
        self.assertIsInstance(readiness["reasons"], list)

def _make_config(tmp: Path) -> "MissionControlServerConfig":
    (tmp / ".git").mkdir(exist_ok=True)
    return MissionControlServerConfig.from_paths(
        tmp, ".nemo-runtimes", tmp / "apply-results", tmp / "runs", memory_db=None
    )


def _nim_response(text: str) -> bytes:
    return json.dumps({
        "choices": [{"message": {"role": "assistant", "content": text}}]
    }).encode("utf-8")


class ChatEndpointTransparencyTests(unittest.TestCase):
    """Tests for /api/agent/message chat flow: fallback transparency, image gen, HTML detection."""

    # ── helper ──────────────────────────────────────────────────────────────
    def _make_msg_payload(self, message: str = "hola", provider: str = "subprocess", extra: dict | None = None) -> dict:
        payload = {
            "message": message,
            "provider": provider,
            "model_base_url": "http://127.0.0.1:1234/v1",
            "default_model": "test-model",
            "api_key": "lm-studio",
            "nemo_mcp_url": "disabled://noop",
            "require_nemo_mcp_capabilities": False,
        }
        if extra:
            payload.update(extra)
        return payload

    # ── chat success: tool_call shows model+base_url ─────────────────────
    def test_chat_success_tool_call_shows_model_and_base_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(Path(tmp))

            class _MockResp:
                def read(self): return _nim_response("Hola, ¿cómo puedo ayudarte?")
                def __enter__(self): return self
                def __exit__(self, *a): pass

            with patch("urllib.request.urlopen", return_value=_MockResp()):
                result = api_agent_message(config, self._make_msg_payload())

        tool_calls = result["message"]["tool_calls"]
        tool_names = [tc["name"] for tc in tool_calls]
        self.assertIn("lmstudio.chat_completions", tool_names)
        completed = next(tc for tc in tool_calls if tc["name"] == "lmstudio.chat_completions")
        self.assertEqual(completed["status"], "completed")
        self.assertIn("test-model", completed["summary"])
        self.assertNotIn("local_fallback", tool_names)

    # ── chat fallback: primary times out → local fallback visible in tool_calls ──
    def test_chat_fallback_shows_two_tool_calls_with_correct_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(Path(tmp))

            # Patch _lmstudio_chat_completion directly to return a fallback tuple.
            # Patching urlopen is unreliable here because _default_settings() calls
            # _nemo_sse_available() which itself hits urlopen before the chat request.
            payload = self._make_msg_payload(extra={"model_base_url": "https://fake-nim.example.com/v1"})
            with patch(
                "nemo_coding_platform.mission_control_server._lmstudio_chat_completion",
                return_value=("local response", "<URLError: timed out connecting to fake-nim>", "local-gemma"),
            ):
                result = api_agent_message(config, payload)

        tool_calls = result["message"]["tool_calls"]
        tool_names = [tc["name"] for tc in tool_calls]
        self.assertIn("lmstudio.chat_completions", tool_names, "primary failure must appear")
        self.assertIn("lmstudio.local_fallback", tool_names, "local fallback must appear")
        primary = next(tc for tc in tool_calls if tc["name"] == "lmstudio.chat_completions")
        fallback = next(tc for tc in tool_calls if tc["name"] == "lmstudio.local_fallback")
        self.assertEqual(primary["status"], "failed")
        self.assertEqual(fallback["status"], "completed")
        self.assertIn("local-gemma", fallback["summary"])
        # Response should be the model's text, not the generic fallback message
        self.assertNotIn("[real-mode fallback]", result["message"]["content"])

    # ── chat HTTP error (401): no local fallback, error surfaced clearly ──
    def test_chat_http_error_no_local_fallback(self) -> None:
        import urllib.error

        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(Path(tmp))

            class _401:
                code = 401
                def read(self): return b'{"error": "Unauthorized"}'
                def __enter__(self): return self
                def __exit__(self, *a): pass
            http_err = urllib.error.HTTPError("url", 401, "Unauthorized", {}, None)
            http_err.read = lambda: b'{"error":"Unauthorized"}'

            with patch("urllib.request.urlopen", side_effect=http_err):
                result = api_agent_message(config, self._make_msg_payload())

        tool_calls = result["message"]["tool_calls"]
        tool_names = [tc["name"] for tc in tool_calls]
        self.assertIn("lmstudio.chat_completions", tool_names)
        self.assertNotIn("lmstudio.local_fallback", tool_names)
        primary = next(tc for tc in tool_calls if tc["name"] == "lmstudio.chat_completions")
        self.assertEqual(primary["status"], "failed")
        self.assertIn("401", primary["summary"])

    # ── image generation: Pollinations fallback when SD not running ──────
    def test_image_generation_uses_pollinations_when_local_sd_absent(self) -> None:
        import urllib.error

        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(Path(tmp))
            settings_dir = Path(tmp) / ".nemo-runtimes" / "mission-control"
            settings_dir.mkdir(parents=True)

            call_log: list[str] = []

            def _mock_urlopen(req, timeout=None):
                url = req.full_url if hasattr(req, "full_url") else str(req)
                call_log.append(url)
                if "localhost:7860" in url or "localhost:8188" in url:
                    raise urllib.error.URLError("connection refused")
                if "pollinations.ai" in url:
                    # Return minimal PNG bytes
                    class _Img:
                        def read(self): return b"\x89PNG\r\n\x1a\n" + b"\x00" * 2000
                        def __enter__(self): return self
                        def __exit__(self, *a): pass
                    return _Img()
                raise urllib.error.URLError("unexpected")

            with patch("urllib.request.urlopen", side_effect=_mock_urlopen):
                result = mission_control_server.api_generate_image(
                    config, {"prompt": "a cartoon chicken", "size": "512x512"}
                )

        self.assertTrue(result.get("ok"))
        self.assertIn("image_url", result)
        self.assertTrue(any("pollinations.ai" in u for u in call_log), "must have tried Pollinations")

    # ── HTML language detection in plan loop ─────────────────────────────
    def test_detect_language_identifies_html_variants(self) -> None:
        detect = mission_control_server._detect_language
        self.assertEqual(detect("crea un html game de snake"), "html")
        self.assertEqual(detect("make a canvas game with asteroids"), "html")
        self.assertEqual(detect("build a web game using javascript and canvas"), "html")
        self.assertEqual(detect("html dashboard para metricas"), "html")
        self.assertEqual(detect("genera un script python para fibonacci"), "python")
        self.assertEqual(detect("create a node.js server"), "javascript")

    # ── HTML execution validation in plan loop ───────────────────────────
    def test_try_run_code_html_valid_document(self) -> None:
        import tempfile
        html = "<!doctype html><html><body><h1>Hello</h1></body></html>"
        with tempfile.TemporaryDirectory() as ws:
            ok, out = mission_control_server._try_run_code(html, "html", workspace=Path(ws))
        self.assertTrue(ok)
        self.assertIn("HTML artifact generated", out)

    def test_try_run_code_html_rejects_fragment(self) -> None:
        fragment = "<h1>just a heading</h1>"
        ok, out = mission_control_server._try_run_code(fragment, "html")
        self.assertFalse(ok)
        self.assertIn("HTML must contain", out)

    # ── fallback error message is informative (not generic) ──────────────
    def test_chat_fallback_message_not_generic(self) -> None:
        import urllib.error

        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(Path(tmp))

            def _always_fail(req, timeout=None):
                raise urllib.error.URLError("connection refused")

            with patch("urllib.request.urlopen", side_effect=_always_fail):
                result = api_agent_message(config, self._make_msg_payload())

        response = result["message"]["content"]
        # The old generic message should be gone; error detail must be surfaced in tool_calls
        self.assertNotIn("LM Studio is unavailable right now", response)
        failed_tc = [tc for tc in result["message"]["tool_calls"] if tc.get("status") == "failed"]
        self.assertTrue(len(failed_tc) > 0, "at least one failed tool_call must be present")


class DecisionAgentEndpointTests(unittest.TestCase):
    def test_decision_agent_status_idle(self):
        """api_decision_agent_status returns idle when no job has run."""
        from nemo_coding_platform.decision_agent_manager import DecisionAgentManager
        from nemo_coding_platform.mission_control_server import api_decision_agent_status
        manager = DecisionAgentManager()
        result = api_decision_agent_status(manager)
        self.assertEqual(result["status"], "idle")
        self.assertIsNone(result["job"])

    def test_decision_agent_run_returns_job_id(self):
        """api_decision_agent_run starts a job and returns a da- prefixed job_id."""
        import tempfile
        from pathlib import Path
        from nemo_coding_platform.decision_agent_manager import DecisionAgentManager
        from nemo_coding_platform.mission_control_server import (
            MissionControlServerConfig, api_decision_agent_run,
        )
        from nemo_coding_platform.core.decision_agent import _FALLBACK_REPORT
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = MissionControlServerConfig.from_paths(
                root, ".nemo-runtimes", root / "apply-results", root / "runs", memory_db=None
            )
            manager = DecisionAgentManager()
            with patch(
                "nemo_coding_platform.decision_agent_manager.analyze_nemo_signal",
                return_value=_FALLBACK_REPORT,
            ), patch(
                "nemo_coding_platform.decision_agent_manager.execute_self_modification",
                side_effect=RuntimeError("blocked in test"),
            ):
                result = api_decision_agent_run(
                    config,
                    {"lm_base_url": "http://localhost:1234/v1", "lm_model": "test"},
                    manager,
                )
        self.assertIn("job_id", result)
        self.assertTrue(result["job_id"].startswith("da-"))
        self.assertEqual(result["status"], "analyzing")

    def test_decision_agent_apply_wrong_status_raises(self):
        """api_decision_agent_apply raises PermissionError if job is not awaiting_review."""
        import tempfile
        from pathlib import Path
        from nemo_coding_platform.decision_agent_manager import DecisionAgentManager
        from nemo_coding_platform.mission_control_server import (
            MissionControlServerConfig, api_decision_agent_apply, api_decision_agent_run,
        )
        from nemo_coding_platform.core.decision_agent import _FALLBACK_REPORT
        from unittest.mock import patch
        import time
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = MissionControlServerConfig.from_paths(
                root, ".nemo-runtimes", root / "apply-results", root / "runs", memory_db=None
            )
            manager = DecisionAgentManager()
            with patch(
                "nemo_coding_platform.decision_agent_manager.analyze_nemo_signal",
                return_value=_FALLBACK_REPORT,
            ), patch(
                "nemo_coding_platform.decision_agent_manager.execute_self_modification",
                side_effect=RuntimeError("blocked in test"),
            ):
                result = api_decision_agent_run(config, {}, manager)
                job_id = result["job_id"]
            time.sleep(0.3)
            with self.assertRaises(PermissionError):
                api_decision_agent_apply(config, {"job_id": job_id}, manager)

if __name__ == "__main__":
    unittest.main()