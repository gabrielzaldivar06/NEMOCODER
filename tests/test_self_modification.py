import tempfile
import unittest
import json
from pathlib import Path

from nemo_coding_platform.core.memory_persistence import PersistentMemoryStore
from nemo_coding_platform.core.nemo_adapter import PersistentNemoAdapter
from nemo_coding_platform.core.self_discovery import ensure_self_mod_permissions_file
from nemo_coding_platform.core.self_modification import SelfModRequest, SelfModTaskType, build_self_mod_context, build_self_mod_handoff_request, execute_self_modification, get_self_mod_continuity, learn_from_self_mod_failure, query_self_mod_risk_patterns, record_self_mod_decision, record_self_mod_feedback, self_mod_apply, self_mod_review, self_mod_risk_flags, self_mod_rollback, self_mod_status
from nemo_coding_platform.mcp_server import mcp_tool_definitions
from nemo_coding_platform.nemocode_mcp_tools import mcp_get_self_mod_continuity, mcp_learn_from_self_mod_failure, mcp_record_self_mod_decision, mcp_self_mod_apply, mcp_self_mod_review, mcp_self_mod_status, mcp_self_modify


def _make_repo(root: Path) -> None:
    (root / "src" / "nemo_coding_platform" / "core").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "skills" / "self-modification").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname = 'nemo_coding_platform'\n", encoding="utf-8")
    (root / "skills" / "self-modification" / "SKILL.md").write_text(
        "---\nname: self-modification\ndescription: test\n---\n\nUse tests.",
        encoding="utf-8",
    )


def _ready_payload(repo: Path, sandbox: Path, changed_files: list[str], status: str = "passed") -> dict[str, object]:
    return {
        "task": {"id": "self-task", "repo_path": str(repo)},
        "run": {"id": "self-run", "sandbox_path": str(sandbox)},
        "timeline": [{"kind": "checkpoint"}, {"kind": "memory_written"}],
        "artifacts": [{"artifact_type": "review_package"}],
        "validation": {"results": [{"status": status}]},
        "mutation_result": {"changed_files": changed_files},
    }


def _write_run_json(path: Path, payload: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


class SelfModificationTests(unittest.TestCase):
    def test_build_self_mod_handoff_request_sets_contract(self) -> None:
        request = SelfModRequest(
            description="Improve memory scoring",
            task_type=SelfModTaskType.REFACTOR,
            target_files=("src/nemo_coding_platform/core/context_portfolio.py",),
            validation_policy="targeted",
        )

        handoff = build_self_mod_handoff_request(request, "c:/dev/dev4")

        self.assertIn("[SELF-MOD:refactor]", handoff.prd)
        self.assertIn("Outcome is written back to NEMO memory.", handoff.acceptance_criteria)
        self.assertTrue(handoff.validation_commands)

    def test_build_self_mod_context_uses_operational_nemo_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentMemoryStore(Path(tmp) / "memory.sqlite")
            adapter = PersistentNemoAdapter(store)
            request = SelfModRequest(description="Improve self-mod context", repo_root=tmp)

            context = build_self_mod_context(adapter, request)

        self.assertIn("prime", context)
        self.assertIn("portfolio", context)
        self.assertIn("anticipate", context)

    def test_execute_self_modification_runs_in_controlled_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_repo(root)
            memory_db = str(root / ".nemo-runtimes" / "memory.sqlite")
            request = SelfModRequest(
                description="Document the self-mod pipeline",
                task_type=SelfModTaskType.DOCUMENTATION,
                target_files=("README.md",),
                validation_policy="none",
                memory_db=memory_db,
                repo_root=str(root),
            )

            result = execute_self_modification(request, task_id="self-task", run_id="self-run")
            summary = result.to_summary_dict()
            permissions_exists = Path(summary["permissions_file"]).exists()
            run_json_exists = Path(summary["run_json"]).exists()

        self.assertEqual(summary["task_id"], "self-task")
        self.assertEqual(summary["run_id"], "self-run")
        self.assertTrue(permissions_exists)
        self.assertTrue(run_json_exists)
        self.assertTrue(summary["memory_writeback"]["stored"])

    def test_self_mod_review_flags_policy_denied_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            _make_repo(repo)
            sandbox.mkdir()
            (sandbox / "generated-implementation.md").write_text("generated", encoding="utf-8")
            permissions = ensure_self_mod_permissions_file(repo)
            run_json = _write_run_json(root / "run.json", _ready_payload(repo, sandbox, ["generated-implementation.md"]))

            review = self_mod_review(run_json, permissions)

        self.assertFalse(review["mergeable"])
        self.assertIn("permission_denied_path:generated-implementation.md", review["risk_flags"])

    def test_self_mod_status_returns_review_risks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            _make_repo(repo)
            sandbox.mkdir()
            (sandbox / "generated-implementation.md").write_text("generated", encoding="utf-8")
            ensure_self_mod_permissions_file(repo)
            run_json = _write_run_json(root / "run.json", _ready_payload(repo, sandbox, ["generated-implementation.md"]))

            status = self_mod_status(run_json)

        self.assertFalse(status["mergeable"])
        self.assertIn("permission_denied_path:generated-implementation.md", status["risk_flags"])

    def test_self_mod_apply_requires_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            _make_repo(repo)
            sandbox.mkdir()
            (sandbox / "README.md").write_text("updated", encoding="utf-8")
            ensure_self_mod_permissions_file(repo)
            run_json = _write_run_json(root / "run.json", _ready_payload(repo, sandbox, ["README.md"]))

            with self.assertRaises(PermissionError):
                self_mod_apply(run_json, approve_review=False)

    def test_self_mod_apply_copies_allowed_files_with_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            _make_repo(repo)
            sandbox.mkdir()
            (repo / "README.md").write_text("old", encoding="utf-8")
            (sandbox / "README.md").write_text("updated", encoding="utf-8")
            ensure_self_mod_permissions_file(repo)
            run_json = _write_run_json(root / "run.json", _ready_payload(repo, sandbox, ["README.md"]))

            applied = self_mod_apply(run_json, approve_review=True)
            content = (repo / "README.md").read_text(encoding="utf-8")

        self.assertEqual(applied["applied_files"], ["README.md"])
        self.assertEqual(content, "updated")

    def test_self_mod_apply_trusted_profile_skips_manual_review_for_ready_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            _make_repo(repo)
            sandbox.mkdir()
            (repo / "README.md").write_text("old", encoding="utf-8")
            (sandbox / "README.md").write_text("updated", encoding="utf-8")
            ensure_self_mod_permissions_file(repo)
            run_json = _write_run_json(root / "run.json", _ready_payload(repo, sandbox, ["README.md"]))

            applied = self_mod_apply(run_json, autonomy_profile="trusted")

        self.assertEqual(applied["applied_files"], ["README.md"])
        self.assertTrue(applied["auto_applied"])
        self.assertFalse(applied["review_approved"])

    def test_self_mod_apply_records_memory_feedback_when_memory_db_is_supplied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            memory_db = root / "memory.sqlite"
            _make_repo(repo)
            sandbox.mkdir()
            (repo / "README.md").write_text("old", encoding="utf-8")
            (sandbox / "README.md").write_text("updated", encoding="utf-8")
            ensure_self_mod_permissions_file(repo)
            run_json = _write_run_json(root / "run.json", _ready_payload(repo, sandbox, ["README.md"]))

            applied = self_mod_apply(run_json, autonomy_profile="trusted", memory_db=memory_db, portfolio_id="portfolio-1")
            store = PersistentMemoryStore(memory_db)
            feedback = store.list_feedback(limit=10)

        self.assertTrue(applied["learning_feedback"]["stored"])
        self.assertTrue(applied["portfolio_effectiveness"]["stored"])
        self.assertGreaterEqual(len(feedback), 2)

    def test_record_self_mod_decision_is_retrieved_as_continuity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory_db = Path(tmp) / "memory.sqlite"

            decision = record_self_mod_decision(
                memory_db,
                objective="Improve self-mod context retrieval",
                chosen_path="wire continuity before mutation",
                rationale="Prior runs should shape planning context.",
                alternatives=("manual notes",),
                task_type="tool_expansion",
                task_id="task-a",
                run_id="run-a",
            )
            continuity = get_self_mod_continuity(memory_db, task_objective="context retrieval", task_type="tool_expansion")

        self.assertTrue(decision["stored"])
        self.assertEqual(continuity["count"], 1)
        self.assertIn("wire continuity", continuity["items"][0]["content"])

    def test_learn_from_failure_records_correction_and_risk_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            memory_db = root / "memory.sqlite"
            _make_repo(repo)
            sandbox.mkdir()
            (sandbox / "src" / "nemo_coding_platform" / "core").mkdir(parents=True)
            (sandbox / "src" / "nemo_coding_platform" / "core" / "example.py").write_text("x = 1", encoding="utf-8")
            ensure_self_mod_permissions_file(repo)
            run_json = _write_run_json(root / "run.json", _ready_payload(repo, sandbox, ["src/nemo_coding_platform/core/example.py"], status="failed"))

            learning = learn_from_self_mod_failure(memory_db, run_json, failure_pattern="validation_failed", suggested_correction="Run targeted core tests before apply.")
            risks = query_self_mod_risk_patterns(memory_db, file_or_module="example.py", risk_category="validation_failed")

        self.assertTrue(learning["stored"])
        self.assertTrue(learning["correction_id"])
        self.assertEqual(risks["count"], 1)
        self.assertIn("targeted core tests", risks["patterns"][0]["content"])

    def test_mcp_learning_wrappers_round_trip_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory_db = str(Path(tmp) / "memory.sqlite")
            decision = mcp_record_self_mod_decision(
                memory_db=memory_db,
                objective="Improve learning loop",
                chosen_path="record decisions",
                rationale="MCP clients need durable self-mod choices.",
                task_type="tool_expansion",
            )
            continuity = mcp_get_self_mod_continuity(memory_db=memory_db, task_objective="learning loop", task_type="tool_expansion")

        self.assertTrue(decision["ok"])
        self.assertTrue(continuity["ok"])
        self.assertEqual(continuity["count"], 1)

    def test_self_mod_rollback_requires_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            apply_json = Path(tmp) / "apply.json"
            apply_json.write_text(json.dumps({"task_id": "self-task", "run_id": "self-run", "repo_path": tmp, "applied_files": [], "created_files": [], "updated_files": [], "review_approved": True, "backup_dir": None, "backup_files": []}), encoding="utf-8")

            with self.assertRaises(PermissionError):
                self_mod_rollback(apply_json, approve_review=False)

    def test_self_mod_risk_flags_detect_validation_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            _make_repo(repo)
            sandbox.mkdir()
            permissions = ensure_self_mod_permissions_file(repo)
            payload = _ready_payload(repo, sandbox, ["README.md"], status="skipped")

            flags = self_mod_risk_flags(payload, permissions)

        self.assertIn("validation_skipped", flags)

    def test_mcp_tool_definitions_include_self_modify(self) -> None:
        names = {tool["name"] for tool in mcp_tool_definitions()}

        self.assertIn("nemocode.self_modify", names)
        self.assertIn("nemocode.self_mod_status", names)
        self.assertIn("nemocode.self_mod_review", names)
        self.assertIn("nemocode.self_mod_apply", names)
        self.assertIn("nemocode.self_mod_rollback", names)

    def test_mcp_self_modify_returns_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_repo(root)
            result = mcp_self_modify(
                description="Document self-mod MCP",
                task_type="documentation",
                target_files=["README.md"],
                validation_policy="none",
                memory_db=str(root / ".nemo-runtimes" / "memory.sqlite"),
                repo_root=str(root),
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["task_type"], "documentation")
        self.assertIn("changed_files", result)

    def test_mcp_self_mod_review_loop_returns_errors_for_unapproved_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            _make_repo(repo)
            sandbox.mkdir()
            (sandbox / "README.md").write_text("updated", encoding="utf-8")
            ensure_self_mod_permissions_file(repo)
            run_json = str(_write_run_json(root / "run.json", _ready_payload(repo, sandbox, ["README.md"])))

            status = mcp_self_mod_status(run_json)
            review = mcp_self_mod_review(run_json)
            apply_result = mcp_self_mod_apply(run_json, approve_review=False)

        self.assertTrue(status["ok"])
        self.assertTrue(review["ok"])
        self.assertFalse(apply_result["ok"])
        self.assertIn("approval required", apply_result["error"])


if __name__ == "__main__":
    unittest.main()