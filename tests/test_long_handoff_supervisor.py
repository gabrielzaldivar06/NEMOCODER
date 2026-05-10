import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from nemo_coding_platform.cli import DEFAULT_MEMORY_DB, main
from nemo_coding_platform.core.evals import score_headless_result
from nemo_coding_platform.core.headless_handoff import HandoffRequest
from nemo_coding_platform.core.long_handoff_supervisor import LongHandoffBudget, build_long_handoff_resume_plan, execute_long_handoff_continuation, execute_long_handoff_supervisor
from nemo_coding_platform.core.memory_persistence import PersistentMemoryStore
from nemo_coding_platform.core.persistence import build_long_handoff_lineage, build_replay_summary, evaluate_long_handoff_continuation_policy, evaluate_long_handoff_memory_policy, headless_result_to_dict, load_headless_result_json, save_headless_result_json
from nemo_coding_platform.core.task_run import ArtifactType, EventKind


class LongHandoffSupervisorTests(unittest.TestCase):
    def test_supervisor_adds_budget_heartbeats_and_resume_artifacts(self) -> None:
        result = execute_long_handoff_supervisor(
            HandoffRequest("Build feature", ".", ("passes tests",), ("python -m unittest",)),
            budget=LongHandoffBudget(max_runtime_minutes=30, heartbeat_minutes=10, max_heartbeats=2, token_budget=1200, pause_after_minutes=20),
        )

        heartbeat_refs = tuple(event.payload_ref for event in result.timeline.events if event.kind == EventKind.HEARTBEAT)
        artifact_paths = tuple(artifact.path for artifact in result.artifacts if artifact.artifact_type == ArtifactType.SUPERVISOR)

        self.assertEqual(heartbeat_refs, ("checkpoint-execute.md", "checkpoint-execute.md"))
        self.assertIn("supervisor-report.md", artifact_paths)
        self.assertIn("continuation-state.json", artifact_paths)
        self.assertIn("resume-token.txt", artifact_paths)
        self.assertIn("supervisor-report.md", result.runtime_files)
        self.assertIn("continuation-state.json", result.runtime_files)
        self.assertIn("resume-token.txt", result.runtime_files)
        self.assertTrue(result.timeline.has_event_kind(EventKind.PAUSED))
        self.assertEqual(score_headless_result(result).grade, "ready")

        state = json.loads((Path(result.run.sandbox_path) / "continuation-state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["task_id"], result.task.id)
        self.assertEqual(state["run_id"], result.run.id)
        self.assertEqual(state["provider_mode"], "fake")
        self.assertEqual(state["validation_policy"], "smoke")
        self.assertIn("checkpoint-execute.md", state["checkpoint_refs"])
        self.assertTrue(state["memory_writeback_handles"])
        self.assertTrue(state["execution_snapshot_ids"])
        self.assertIn("checkpoint-execute", state["execution_snapshot_ids"])
        self.assertIsNone(state["latest_atomic_checkpoint"])

    def test_long_handoff_cli_saves_replayable_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/long-run.json"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main([
                    "long-handoff-run",
                    "Build feature",
                    "--max-runtime-minutes",
                    "30",
                    "--heartbeat-minutes",
                    "10",
                    "--max-heartbeats",
                    "2",
                    "--pause-after-minutes",
                    "20",
                    "--provider",
                    "fake",
                    "--json",
                    "--save-json",
                    path,
                ])
            payload_text = output.getvalue().strip()
            payload = json.loads(payload_text) if payload_text else load_headless_result_json(path)
            replay = build_replay_summary(load_headless_result_json(path))

        self.assertEqual(code, 0)
        self.assertEqual(payload["heartbeats"], 2)
        self.assertTrue(payload["paused"])
        self.assertTrue(replay["can_replay"])
        self.assertIn("supervisor-report.md", replay["artifact_paths"])
        self.assertIn("resume-token.txt", replay["payload_refs"])

    def test_resume_plan_reads_persisted_resume_token(self) -> None:
        result = execute_long_handoff_supervisor(
            HandoffRequest("Build feature", ".", ("passes tests",), ("python -m unittest",)),
            budget=LongHandoffBudget(max_runtime_minutes=30, heartbeat_minutes=10, max_heartbeats=2, token_budget=1200, pause_after_minutes=20),
            task_id="resume-task",
            run_id="resume-run",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/long-run.json"
            save_headless_result_json(result, path)
            plan = build_long_handoff_resume_plan(load_headless_result_json(path))

        self.assertTrue(plan.can_resume)
        self.assertEqual(plan.resume_token, "resume-task:resume-run:minute-20")
        self.assertEqual(plan.resume_minute, 20)
        self.assertEqual(plan.objective, "Build feature")
        self.assertEqual(plan.provider_mode, "fake")
        self.assertEqual(plan.validation_policy, "smoke")
        self.assertTrue(plan.validation_commands)
        self.assertTrue(plan.memory_writeback_handles)
        self.assertIn("checkpoint-execute.md", plan.checkpoint_refs)
        self.assertIsNone(plan.latest_atomic_checkpoint)
        self.assertEqual(plan.repair_cursor, 0)
        self.assertEqual(plan.resume_validation_state, ())

    def test_resume_plan_extracts_atomic_checkpoint_cursor(self) -> None:
        result = execute_long_handoff_supervisor(
            HandoffRequest("Build atomic", ".", ("passes tests",), ("python -m unittest",)),
            budget=LongHandoffBudget(max_runtime_minutes=30, heartbeat_minutes=10, max_heartbeats=2, token_budget=1200, pause_after_minutes=20),
            task_id="atomic-task",
            run_id="atomic-run",
        )
        payload = headless_result_to_dict(result)
        snapshots = payload.get("execution_snapshots")
        self.assertIsInstance(snapshots, dict)
        snapshots["checkpoint-repair-00002"] = {
            "snapshot": {
                "resume_mode": "atomic",
                "repair_cursor": 2,
                "validation_state": ["smoke:FAILED"],
            }
        }
        payload["execution_snapshots"] = snapshots

        plan = build_long_handoff_resume_plan(payload)

        self.assertEqual(plan.latest_atomic_checkpoint, "checkpoint-repair-00002")
        self.assertEqual(plan.repair_cursor, 2)
        self.assertEqual(plan.resume_validation_state, ("smoke:FAILED",))

    def test_resume_plan_falls_back_to_persisted_json_when_state_file_is_missing(self) -> None:
        result = execute_long_handoff_supervisor(
            HandoffRequest("Build fallback", ".", ("passes tests",), ("python -m unittest",)),
            budget=LongHandoffBudget(max_runtime_minutes=30, heartbeat_minutes=10, max_heartbeats=2, token_budget=1200, pause_after_minutes=20),
            task_id="fallback-task",
            run_id="fallback-run",
        )
        (Path(result.run.sandbox_path) / "continuation-state.json").unlink()
        payload = headless_result_to_dict(result)
        plan = build_long_handoff_resume_plan(payload)

        self.assertTrue(plan.can_resume)
        self.assertEqual(plan.objective, "Build fallback")
        self.assertEqual(plan.provider_mode, "fake")
        self.assertEqual(plan.validation_policy, "smoke")
        self.assertEqual(plan.validation_commands, ("python -m unittest",))

    def test_headless_json_persists_execution_snapshots(self) -> None:
        result = execute_long_handoff_supervisor(
            HandoffRequest("Persist snapshots", ".", ("passes tests",), ("python -m unittest",)),
            budget=LongHandoffBudget(max_runtime_minutes=30, heartbeat_minutes=10, max_heartbeats=2, token_budget=1200, pause_after_minutes=20),
        )

        payload = headless_result_to_dict(result)

        snapshots = payload.get("execution_snapshots")
        self.assertIsInstance(snapshots, dict)
        self.assertIn("checkpoint-execute", snapshots)

    def test_long_handoff_resume_cli_outputs_resume_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_path = f"{tmp}/long-run.json"
            resume_path = f"{tmp}/resume-plan.json"
            with contextlib.redirect_stdout(io.StringIO()):
                main([
                    "long-handoff-run",
                    "Build feature",
                    "--max-runtime-minutes",
                    "30",
                    "--heartbeat-minutes",
                    "10",
                    "--max-heartbeats",
                    "2",
                    "--pause-after-minutes",
                    "20",
                    "--provider",
                    "fake",
                    "--json",
                    "--save-json",
                    run_path,
                ])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["long-handoff-resume", run_path, "--json", "--save-json", resume_path])
            payload = json.loads(output.getvalue())
            saved_payload = load_headless_result_json(resume_path)

        self.assertEqual(code, 0)
        self.assertTrue(payload["can_resume"])
        self.assertEqual(payload["resume_minute"], 20)
        self.assertEqual(saved_payload["resume_token"], payload["resume_token"])

    def test_continuation_creates_new_run_linked_to_resume_token(self) -> None:
        paused = execute_long_handoff_supervisor(
            HandoffRequest("Build feature", ".", ("passes tests",), ("python -m unittest",)),
            budget=LongHandoffBudget(max_runtime_minutes=30, heartbeat_minutes=10, max_heartbeats=2, token_budget=1200, pause_after_minutes=20),
            task_id="continue-task",
            run_id="continue-run",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/paused.json"
            save_headless_result_json(paused, path)
            continuation = execute_long_handoff_continuation(load_headless_result_json(path))

        self.assertEqual(continuation.task.id, "continue-task-resume")
        self.assertEqual(continuation.run.id, "continue-run-resume-20")
        self.assertTrue(continuation.timeline.has_event_kind(EventKind.RESUMED))
        self.assertIn("continuation-link.md", continuation.runtime_files)
        self.assertIn("continuation-memory.md", continuation.runtime_files)
        self.assertTrue(any(artifact.path == "continuation-link.md" for artifact in continuation.artifacts))
        self.assertTrue(any(artifact.path == "continuation-memory.md" for artifact in continuation.artifacts))
        self.assertTrue(any(item.call.tool_name == "store_conversation" and "continue-run-resume-20" in item.call.arguments.get("summary", "") for item in continuation.nemo_results))
        self.assertTrue(any(trace.id == "mem-continuation-link" and "continue-run-resume-20" in trace.summary for trace in continuation.memory_traces))
        self.assertEqual(score_headless_result(continuation).grade, "ready")

    def test_continuation_emits_restore_artifact_when_atomic_checkpoint_exists(self) -> None:
        paused = execute_long_handoff_supervisor(
            HandoffRequest("Build feature", ".", ("passes tests",), ("python -m unittest",)),
            budget=LongHandoffBudget(max_runtime_minutes=30, heartbeat_minutes=10, max_heartbeats=2, token_budget=1200, pause_after_minutes=20),
            task_id="continue-atomic-task",
            run_id="continue-atomic-run",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/paused.json"
            save_headless_result_json(paused, path)
            payload = load_headless_result_json(path)
            snapshots = payload.get("execution_snapshots")
            self.assertIsInstance(snapshots, dict)
            snapshots["checkpoint-repair-00001"] = {
                "snapshot": {
                    "resume_mode": "atomic",
                    "repair_cursor": 1,
                    "validation_state": ["smoke:FAILED"],
                }
            }
            payload["execution_snapshots"] = snapshots
            continuation = execute_long_handoff_continuation(payload)

        self.assertIn("resume-restore.md", continuation.runtime_files)
        self.assertTrue(any(event.kind == EventKind.RESUMED and event.payload_ref == "resume-restore.md" for event in continuation.timeline.events))

    def test_long_handoff_continue_cli_saves_replayable_continuation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paused_path = f"{tmp}/paused.json"
            continuation_path = f"{tmp}/continuation.json"
            with contextlib.redirect_stdout(io.StringIO()):
                main([
                    "long-handoff-run",
                    "Build feature",
                    "--max-runtime-minutes",
                    "30",
                    "--heartbeat-minutes",
                    "10",
                    "--max-heartbeats",
                    "2",
                    "--pause-after-minutes",
                    "20",
                    "--provider",
                    "fake",
                    "--no-memory-db",
                    "--json",
                    "--save-json",
                    paused_path,
                ])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["long-handoff-continue", paused_path, "--no-memory-db", "--json", "--save-json", continuation_path])
            payload = json.loads(output.getvalue())
            replay = build_replay_summary(load_headless_result_json(continuation_path))

        self.assertEqual(code, 0)
        self.assertTrue(payload["resumed"])
        self.assertTrue(replay["can_replay"])
        self.assertIn("continuation-link.md", replay["artifact_paths"])
        self.assertIn("continuation-memory.md", replay["artifact_paths"])
        self.assertIn("continuation-link.md", replay["payload_refs"])

    def test_long_handoff_continue_cli_memory_db_persists_continuation_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paused_path = f"{tmp}/paused.json"
            memory_db = Path(tmp) / "nemo-memory.sqlite"
            with contextlib.redirect_stdout(io.StringIO()):
                main([
                    "long-handoff-run",
                    "Build feature",
                    "--max-runtime-minutes",
                    "30",
                    "--heartbeat-minutes",
                    "10",
                    "--max-heartbeats",
                    "2",
                    "--pause-after-minutes",
                    "20",
                    "--provider",
                    "fake",
                    "--memory-db",
                    str(memory_db),
                    "--json",
                    "--save-json",
                    paused_path,
                ])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["long-handoff-continue", paused_path, "--memory-db", str(memory_db), "--json"])
            payload = json.loads(output.getvalue())
            atoms = PersistentMemoryStore(memory_db).search_atoms(limit=50)

        self.assertEqual(code, 0)
        self.assertTrue(payload["resumed"])
        self.assertTrue(any("Long handoff continuation linked" in atom.atom.content for atom in atoms))

    def test_long_handoff_run_uses_default_nemo_memory_db(self) -> None:
        memory_db = Path(DEFAULT_MEMORY_DB)
        if memory_db.exists():
            memory_db.unlink()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main([
                "long-handoff-run",
                "Build feature",
                "--max-runtime-minutes",
                "30",
                "--heartbeat-minutes",
                "10",
                "--max-heartbeats",
                "2",
                "--pause-after-minutes",
                "20",
                "--provider",
                "fake",
                "--json",
            ])
        payload = json.loads(output.getvalue())
        atoms = PersistentMemoryStore(memory_db).search_atoms(limit=50)

        self.assertEqual(code, 0)
        self.assertTrue(payload["paused"])
        self.assertTrue(memory_db.exists())
        self.assertTrue(any("Checkpoint writeback prepared" in atom.atom.content for atom in atoms))

    def test_long_handoff_lineage_links_source_and_continuation(self) -> None:
        paused = execute_long_handoff_supervisor(
            HandoffRequest("Build feature", ".", ("passes tests",), ("python -m unittest",)),
            budget=LongHandoffBudget(max_runtime_minutes=30, heartbeat_minutes=10, max_heartbeats=2, token_budget=1200, pause_after_minutes=20),
            task_id="lineage-task",
            run_id="lineage-run",
        )
        with tempfile.TemporaryDirectory() as tmp:
            source_path = f"{tmp}/source.json"
            continuation_path = f"{tmp}/continuation.json"
            save_headless_result_json(paused, source_path)
            continuation = execute_long_handoff_continuation(load_headless_result_json(source_path))
            save_headless_result_json(continuation, continuation_path)
            lineage = build_long_handoff_lineage([load_headless_result_json(source_path), load_headless_result_json(continuation_path)])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["long-handoff-lineage", source_path, continuation_path, "--json"])
            cli_payload = json.loads(output.getvalue())

        self.assertEqual(code, 0)
        self.assertTrue(lineage["complete"])
        self.assertTrue(lineage["ready"])
        self.assertEqual(lineage["link_count"], 1)
        self.assertEqual(lineage["links"][0]["source_run"], "lineage-run")
        self.assertEqual(lineage["links"][0]["continuation_run"], "lineage-run-resume-20")
        self.assertTrue(cli_payload["complete"])
        self.assertEqual(cli_payload["node_count"], 2)

    def test_long_handoff_lineage_reports_multi_hop_depth(self) -> None:
        root = execute_long_handoff_supervisor(
            HandoffRequest("Build feature", ".", ("passes tests",), ("python -m unittest",)),
            budget=LongHandoffBudget(max_runtime_minutes=30, heartbeat_minutes=10, max_heartbeats=2, token_budget=1200, pause_after_minutes=20),
            task_id="chain-task",
            run_id="chain-root",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root_path = f"{tmp}/root.json"
            first_path = f"{tmp}/first.json"
            save_headless_result_json(root, root_path)
            first = execute_long_handoff_continuation(
                load_headless_result_json(root_path),
                budget=LongHandoffBudget(max_runtime_minutes=30, heartbeat_minutes=10, max_heartbeats=1, token_budget=1200, pause_after_minutes=20),
            )
            save_headless_result_json(first, first_path)
            second = execute_long_handoff_continuation(load_headless_result_json(first_path))
            lineage = build_long_handoff_lineage([
                load_headless_result_json(root_path),
                load_headless_result_json(first_path),
                json.loads(json.dumps(headless_result_to_dict(second))),
            ])

        self.assertTrue(lineage["complete"])
        self.assertFalse(lineage["forked"])
        self.assertEqual(lineage["node_count"], 3)
        self.assertEqual(lineage["link_count"], 2)
        self.assertEqual(lineage["roots"], ["chain-root"])
        self.assertEqual(lineage["leaves"], ["chain-root-resume-20-resume-20"])
        self.assertEqual(lineage["max_depth"], 3)

    def test_long_handoff_lineage_detects_forks(self) -> None:
        root = execute_long_handoff_supervisor(
            HandoffRequest("Build feature", ".", ("passes tests",), ("python -m unittest",)),
            budget=LongHandoffBudget(max_runtime_minutes=30, heartbeat_minutes=10, max_heartbeats=2, token_budget=1200, pause_after_minutes=20),
            task_id="fork-task",
            run_id="fork-root",
        )
        with tempfile.TemporaryDirectory() as tmp:
            source_path = f"{tmp}/source.json"
            save_headless_result_json(root, source_path)
            first = execute_long_handoff_continuation(load_headless_result_json(source_path), objective="Continue branch one")
            second = execute_long_handoff_continuation(load_headless_result_json(source_path), objective="Continue branch two")
            first_payload = json.loads(json.dumps(headless_result_to_dict(first)))
            second_payload = json.loads(json.dumps(headless_result_to_dict(second)))
            second_payload["run"]["id"] = "fork-root-resume-20-alt"
            second_payload["task"]["id"] = "fork-task-resume-alt"
            for trace in second_payload["memory_traces"]:
                if trace["id"] == "mem-continuation-link":
                    trace["summary"] = trace["summary"].replace("continuation_task=fork-task-resume", "continuation_task=fork-task-resume-alt").replace("continuation_run=fork-root-resume-20", "continuation_run=fork-root-resume-20-alt")
            lineage = build_long_handoff_lineage([load_headless_result_json(source_path), first_payload, second_payload])

        self.assertTrue(lineage["complete"])
        self.assertTrue(lineage["forked"])
        self.assertFalse(lineage["autonomy_ready"])
        self.assertIn("lineage_forked", lineage["policy_reasons"])
        self.assertEqual(lineage["branch_count"], 1)
        self.assertEqual(lineage["branches"][0]["source_run"], "fork-root")
        self.assertEqual(lineage["branches"][0]["continuation_runs"], ["fork-root-resume-20", "fork-root-resume-20-alt"])

    def test_continuation_policy_blocks_existing_continuation_without_fork_permission(self) -> None:
        root = execute_long_handoff_supervisor(
            HandoffRequest("Build feature", ".", ("passes tests",), ("python -m unittest",)),
            budget=LongHandoffBudget(max_runtime_minutes=30, heartbeat_minutes=10, max_heartbeats=2, token_budget=1200, pause_after_minutes=20),
            task_id="policy-task",
            run_id="policy-root",
        )
        with tempfile.TemporaryDirectory() as tmp:
            source_path = f"{tmp}/source.json"
            first_path = f"{tmp}/first.json"
            save_headless_result_json(root, source_path)
            first = execute_long_handoff_continuation(load_headless_result_json(source_path))
            save_headless_result_json(first, first_path)
            policy = evaluate_long_handoff_continuation_policy(load_headless_result_json(source_path), [load_headless_result_json(source_path), load_headless_result_json(first_path)])

        self.assertFalse(policy["allowed"])
        self.assertEqual(policy["existing_continuations"], ["policy-root-resume-20"])
        self.assertIn("source_already_continued", policy["reasons"])

    def test_memory_policy_blocks_existing_continuation_from_nemo_summary(self) -> None:
        root = execute_long_handoff_supervisor(
            HandoffRequest("Build feature", ".", ("passes tests",), ("python -m unittest",)),
            budget=LongHandoffBudget(max_runtime_minutes=30, heartbeat_minutes=10, max_heartbeats=2, token_budget=1200, pause_after_minutes=20),
            task_id="memory-policy-task",
            run_id="memory-policy-root",
        )
        summary = "Long handoff continuation linked source_task=memory-policy-task source_run=memory-policy-root continuation_task=memory-policy-task-resume continuation_run=memory-policy-root-resume-20 resume_token=memory-policy-task:memory-policy-root:minute-20 resume_minute=20"

        policy = evaluate_long_handoff_memory_policy(headless_result_to_dict(root), [summary])

        self.assertFalse(policy["allowed"])
        self.assertEqual(policy["existing_continuations"], ["memory-policy-root-resume-20"])
        self.assertIn("source_already_continued", policy["reasons"])

    def test_long_handoff_continue_cli_blocks_fork_without_allow_fork(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_path = f"{tmp}/source.json"
            first_path = f"{tmp}/first.json"
            with contextlib.redirect_stdout(io.StringIO()):
                main([
                    "long-handoff-run",
                    "Build feature",
                    "--max-runtime-minutes",
                    "30",
                    "--heartbeat-minutes",
                    "10",
                    "--max-heartbeats",
                    "2",
                    "--pause-after-minutes",
                    "20",
                    "--provider",
                    "fake",
                    "--no-memory-db",
                    "--json",
                    "--save-json",
                    source_path,
                ])
                main(["long-handoff-continue", source_path, "--no-memory-db", "--json", "--save-json", first_path])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["long-handoff-continue", source_path, "--lineage-context", source_path, "--lineage-context", first_path, "--json"])
            payload = json.loads(output.getvalue())

        self.assertEqual(code, 1)
        self.assertEqual(payload["error"], "continuation_blocked_by_lineage_policy")
        self.assertIn("source_already_continued", payload["reasons"])

    def test_long_handoff_continue_cli_auto_blocks_from_nemo_memory_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_path = f"{tmp}/source.json"
            first_path = f"{tmp}/first.json"
            memory_db = Path(tmp) / "nemo-memory.sqlite"
            with contextlib.redirect_stdout(io.StringIO()):
                main([
                    "long-handoff-run",
                    "Build feature",
                    "--max-runtime-minutes",
                    "30",
                    "--heartbeat-minutes",
                    "10",
                    "--max-heartbeats",
                    "2",
                    "--pause-after-minutes",
                    "20",
                    "--provider",
                    "fake",
                    "--memory-db",
                    str(memory_db),
                    "--json",
                    "--save-json",
                    source_path,
                ])
                main(["long-handoff-continue", source_path, "--memory-db", str(memory_db), "--json", "--save-json", first_path])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["long-handoff-continue", source_path, "--memory-db", str(memory_db), "--json"])
            payload = json.loads(output.getvalue())

        self.assertEqual(code, 1)
        self.assertEqual(payload["error"], "continuation_blocked_by_lineage_policy")
        self.assertEqual(payload["existing_continuations"], ["run-1-resume-20"])

    def test_long_handoff_continue_cli_auto_gate_allows_explicit_fork_from_nemo_memory_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_path = f"{tmp}/source.json"
            first_path = f"{tmp}/first.json"
            memory_db = Path(tmp) / "nemo-memory.sqlite"
            with contextlib.redirect_stdout(io.StringIO()):
                main([
                    "long-handoff-run",
                    "Build feature",
                    "--max-runtime-minutes",
                    "30",
                    "--heartbeat-minutes",
                    "10",
                    "--max-heartbeats",
                    "2",
                    "--pause-after-minutes",
                    "20",
                    "--provider",
                    "fake",
                    "--memory-db",
                    str(memory_db),
                    "--json",
                    "--save-json",
                    source_path,
                ])
                main(["long-handoff-continue", source_path, "--memory-db", str(memory_db), "--json", "--save-json", first_path])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["long-handoff-continue", source_path, "--memory-db", str(memory_db), "--allow-fork", "--json"])
            payload = json.loads(output.getvalue())

        self.assertEqual(code, 0)
        self.assertTrue(payload["resumed"])

    def test_long_handoff_continue_cli_allows_fork_with_explicit_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_path = f"{tmp}/source.json"
            first_path = f"{tmp}/first.json"
            fork_path = f"{tmp}/fork.json"
            with contextlib.redirect_stdout(io.StringIO()):
                main([
                    "long-handoff-run",
                    "Build feature",
                    "--max-runtime-minutes",
                    "30",
                    "--heartbeat-minutes",
                    "10",
                    "--max-heartbeats",
                    "2",
                    "--pause-after-minutes",
                    "20",
                    "--provider",
                    "fake",
                    "--no-memory-db",
                    "--json",
                    "--save-json",
                    source_path,
                ])
                main(["long-handoff-continue", source_path, "--no-memory-db", "--json", "--save-json", first_path])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main([
                    "long-handoff-continue",
                    source_path,
                    "--lineage-context",
                    source_path,
                    "--lineage-context",
                    first_path,
                    "--allow-fork",
                    "--json",
                    "--save-json",
                    fork_path,
                ])
            payload_text = output.getvalue().strip()
            payload = json.loads(payload_text) if payload_text else load_headless_result_json(fork_path)

        self.assertEqual(code, 0)
        self.assertTrue(payload["resumed"])

    def test_snapshot_captures_changed_files_into_runtime_store(self) -> None:
        """snapshot_changed_files copies repo files to runtime/file-snapshots/{id}/."""
        from nemo_coding_platform.core.checkpoint import snapshot_changed_files
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            runtime = Path(tmp) / "runtime"
            (repo / "src").mkdir()
            (repo / "src" / "feature.py").write_text("def feature(): return 1\n", encoding="utf-8")
            (repo / "tests" / "test_feature.py").parent.mkdir(parents=True, exist_ok=True)
            (repo / "tests" / "test_feature.py").write_text("import feature\n", encoding="utf-8")

            stored = snapshot_changed_files(
                repo_path=repo,
                changed_files=["src/feature.py", "tests/test_feature.py", "nonexistent.py"],
                runtime_path=runtime,
                checkpoint_id="checkpoint-repair-00001",
            )

            snap_root = runtime / "file-snapshots" / "checkpoint-repair-00001"
            self.assertCountEqual(stored, ["src/feature.py", "tests/test_feature.py"])
            self.assertTrue((snap_root / "src" / "feature.py").exists())
            self.assertTrue((snap_root / "tests" / "test_feature.py").exists())
            # nonexistent.py must not be created
            self.assertFalse((snap_root / "nonexistent.py").exists())

    def test_restore_file_snapshot_writes_files_back_to_repo(self) -> None:
        """restore_file_snapshot rewrites snapshotted files to the repo."""
        from nemo_coding_platform.core.checkpoint import restore_file_snapshot, snapshot_changed_files
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            runtime = Path(tmp) / "runtime"
            (repo / "src").mkdir()
            (repo / "src" / "module.py").write_text("v1\n", encoding="utf-8")
            snapshot_changed_files(str(repo), ["src/module.py"], str(runtime), "cp-1")

            # Simulate file being changed after checkpoint
            (repo / "src" / "module.py").write_text("v2-broken\n", encoding="utf-8")

            restored = restore_file_snapshot(str(runtime), "cp-1", str(repo))

            self.assertEqual(restored, ["src/module.py"])
            self.assertEqual((repo / "src" / "module.py").read_text(encoding="utf-8"), "v1\n")

    def test_restore_returns_empty_when_no_snapshot_exists(self) -> None:
        from nemo_coding_platform.core.checkpoint import restore_file_snapshot
        with tempfile.TemporaryDirectory() as tmp:
            restored = restore_file_snapshot(tmp, "nonexistent-checkpoint", tmp)
        self.assertEqual(restored, [])

    def test_atomic_resume_restores_files_and_records_in_restore_md(self) -> None:
        """Full integration: snapshot captured during run, restored in continuation."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (repo / "src").mkdir()
            (repo / "src" / "impl.py").write_text("# original\n", encoding="utf-8")

            paused = execute_long_handoff_supervisor(
                HandoffRequest("Build feature", str(repo), ("passes tests",), ("python -m unittest",)),
                budget=LongHandoffBudget(max_runtime_minutes=30, heartbeat_minutes=10, max_heartbeats=2, token_budget=1200, pause_after_minutes=20),
                task_id="restore-task",
                run_id="restore-run",
            )
            payload = headless_result_to_dict(paused)

            # Inject a synthetic atomic snapshot that points to the paused run's sandbox.
            sandbox_path = paused.run.sandbox_path
            snapshots = payload.get("execution_snapshots") or {}
            snapshots["checkpoint-repair-00001"] = {
                "snapshot": {
                    "resume_mode": "atomic",
                    "repair_cursor": 1,
                    "validation_state": ["smoke:FAILED"],
                    "changed_files": ["src/impl.py"],
                },
                "snapshot_runtime_path": sandbox_path,
                "changed_files": ["src/impl.py"],
            }
            payload["execution_snapshots"] = snapshots

            # Also create the actual file-snapshot in the sandbox so restore has something to read.
            from nemo_coding_platform.core.checkpoint import snapshot_changed_files as _snap
            _snap(str(repo), ["src/impl.py"], sandbox_path, "checkpoint-repair-00001")

            continuation = execute_long_handoff_continuation(payload)

        self.assertIn("resume-restore.md", continuation.runtime_files)
        restore_md_content = (Path(continuation.run.sandbox_path) / "resume-restore.md").read_text(encoding="utf-8")
        self.assertIn("snapshot_runtime_path=", restore_md_content)
        self.assertIn("src/impl.py", restore_md_content)

    def test_resume_plan_exposes_snapshot_runtime_path(self) -> None:
        """build_long_handoff_resume_plan extracts snapshot_runtime_path from atomic checkpoint."""
        result = execute_long_handoff_supervisor(
            HandoffRequest("Build snapshot path", ".", ("passes tests",), ("python -m unittest",)),
            budget=LongHandoffBudget(max_runtime_minutes=30, heartbeat_minutes=10, max_heartbeats=2, token_budget=1200, pause_after_minutes=20),
            task_id="srp-task",
            run_id="srp-run",
        )
        sandbox_path = result.run.sandbox_path
        payload = headless_result_to_dict(result)
        snapshots = payload.get("execution_snapshots") or {}
        snapshots["checkpoint-repair-00001"] = {
            "snapshot": {
                "resume_mode": "atomic",
                "repair_cursor": 1,
                "validation_state": [],
            },
            "snapshot_runtime_path": sandbox_path,
        }
        payload["execution_snapshots"] = snapshots

        plan = build_long_handoff_resume_plan(payload)

        self.assertEqual(plan.latest_atomic_checkpoint, "checkpoint-repair-00001")
        self.assertEqual(plan.snapshot_runtime_path, sandbox_path)


if __name__ == "__main__":
    unittest.main()
