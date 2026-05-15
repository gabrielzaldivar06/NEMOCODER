import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from nemo_coding_platform.cli import main
from nemo_coding_platform.core.product import BackendProtocol, ProductSurface, DESKTOP_PRODUCT
from nemo_coding_platform.mission_control_server import MissionControlHttpServer, MissionControlServerConfig, api_repo_open, api_settings, api_state
from tests.generate_release_confidence_evidence import build_release_confidence_evidence


class GlobalMvpGateTests(unittest.TestCase):
    def test_global_mvp_acceptance_gate_12_of_12(self) -> None:
        # Criteria 1-3: desktop shell contract, model configuration, repo open flow.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            runtimes = root / "runtimes"
            apply_results = root / "apply-results"
            repo.mkdir()
            runtimes.mkdir()
            apply_results.mkdir()
            (repo / ".git").mkdir()

            config = MissionControlServerConfig.from_paths(repo, runtimes, apply_results, runtimes, memory_db=None)
            server = MissionControlHttpServer(("127.0.0.1", 0), config)
            try:
                state_payload = api_state(config)
                settings_payload = api_settings(
                    server,
                    {
                        "provider": "subprocess",
                        "model_base_url": "http://localhost:1234/v1",
                        "default_model": "",
                        "model_roles": {
                            "planner": "",
                            "editor": "",
                            "reviewer": "",
                            "summarizer": "",
                        },
                    },
                )
                repo_open_payload = api_repo_open(server, {"repo_path": str(repo)})
            finally:
                server.server_close()

        # Criteria 4-12: executable evidence from release confidence flow.
        evidence_payload = build_release_confidence_evidence()["release_confidence"]

        # Additional check for explicit task creation from headless-run output.
        task_create_out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            run_json = Path(tmp) / "result.json"
            with contextlib.redirect_stdout(task_create_out):
                task_create_exit = main([
                    "headless-run",
                    "Global MVP gate task",
                    "--provider",
                    "fake",
                    "--json",
                    "--save-json",
                    str(run_json),
                ])
            task_create_payload = json.loads(task_create_out.getvalue())
            persisted_payload = json.loads(run_json.read_text(encoding="utf-8")) if run_json.exists() else {}

        checklist = {
            # 1. Desktop app launches locally.
            "desktop_app_launches_locally": (
                DESKTOP_PRODUCT.target_surface == ProductSurface.DESKTOP
                and DESKTOP_PRODUCT.backend_protocol == BackendProtocol.HTTP_LOCAL
                and bool(state_payload.get("product"))
            ),
            # 2. User can configure LM Studio.
            "configure_lm_studio": (
                bool(settings_payload.get("ok"))
                and str(settings_payload.get("settings", {}).get("model_base_url")) == "http://localhost:1234/v1"
                and bool(settings_payload.get("settings", {}).get("model_roles"))
            ),
            # 3. User can open a repo.
            "open_repo": bool(repo_open_payload.get("ok")) and bool(repo_open_payload.get("repo", {}).get("is_git_repo")),
            # 4. User can create a coding task.
            "create_coding_task": (
                task_create_exit == 0
                and bool(persisted_payload)
                and bool((persisted_payload.get("task") or {}).get("id"))
                and bool((persisted_payload.get("run") or {}).get("id"))
                and bool(task_create_payload.get("saved_json"))
            ),
            # 5. Agent uses NEMO context at task start.
            "nemo_context_at_task_start": (
                evidence_payload["replay"]["headless_exit_code"] == 0
                and "memory.md" in evidence_payload["replay"]["artifact_paths"]
            ),
            # 6. Agent creates a plan.
            "agent_creates_plan": "generated-spec.md" in evidence_payload["replay"]["artifact_paths"],
            # 7. Agent executes in isolated workspace.
            "isolated_workspace_execution": evidence_payload["state"]["run_count"] >= 1,
            # 8. Agent applies changes only through Quality Core.
            "apply_through_quality_core": (
                bool(evidence_payload["apply"]["review_approved"])
                and len(evidence_payload["apply"]["applied_files"]) >= 1
            ),
            # 9. Agent runs validation commands.
            "validation_commands_run": "validation.txt" in evidence_payload["replay"]["artifact_paths"],
            # 10. User can review diff and timeline.
            "review_diff_and_timeline": (
                bool(evidence_payload["review"]["mergeable"])
                and len(evidence_payload["review"]["changed_files"]) >= 1
                and evidence_payload["replay"]["event_count"] >= 1
            ),
            # 11. NEMO records session outcome.
            "nemo_records_session_outcome": "memory.md" in evidence_payload["replay"]["artifact_paths"],
            # 12. Task can be replayed or inspected.
            "task_replay_or_inspection": (
                bool(evidence_payload["replay"]["can_replay"])
                and evidence_payload["replay"]["exit_code"] == 0
            ),
        }

        failing = [name for name, passed in checklist.items() if not passed]
        self.assertFalse(failing, f"Global MVP gate failed criteria: {', '.join(failing)}")


if __name__ == "__main__":
    unittest.main()
