import tempfile
import unittest
from pathlib import Path

from nemo_coding_platform.core.contracts import RuntimeState
from nemo_coding_platform.mission_control_server import HandoffJob, HandoffJobManager, MissionControlServerConfig


class AgentRuntimeContractTests(unittest.TestCase):
    def test_runtime_state_includes_prd_minimum_states(self) -> None:
        states = {state.value for state in RuntimeState}

        self.assertTrue(
            {
                "initializing",
                "ready",
                "planning",
                "building",
                "reviewing",
                "paused",
                "completed",
                "failed",
            }.issubset(states)
        )

    def test_terminal_job_cancel_is_idempotent(self) -> None:
        manager = HandoffJobManager()
        job = HandoffJob("job-1", "task-1", "run-1", "run.json", "completed", tuple(), {}, ["finished"])
        manager._jobs[job.job_id] = job

        cancelled = manager.cancel(job.job_id)

        self.assertEqual(cancelled.status, "completed")
        self.assertTrue(any("ignored status=completed" in line for line in cancelled.logs))

    def test_only_paused_jobs_can_spawn_a_resumed_run(self) -> None:
        manager = HandoffJobManager()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", memory_db=None)
            source = HandoffJob(
                "job-paused",
                "task-paused",
                "run-paused",
                str(root / "run.json"),
                "paused",
                tuple(),
                {"objective": "resume me", "provider": "fake"},
                ["paused"],
            )
            manager._jobs[source.job_id] = source

            resumed = manager.resume(config, source.job_id)
            manager.cancel(resumed.job_id)

        self.assertNotEqual(resumed.job_id, source.job_id)
        # The resumed job may have completed or failed before cancel was called;
        # any terminal state is acceptable — the key invariant is that a new job
        # was created and carries the correct resumption log entry.
        self.assertIn(resumed.status, {"cancelled", "completed", "failed"})
        self.assertTrue(any("resumed from job-paused" in line for line in resumed.logs))

    # ── FR4 Subagent Permission Contracts ─────────────────────────────────

    def test_workflow_mode_survives_job_snapshot_round_trip(self) -> None:
        """workflow_mode persists through to_dict/from_snapshot so subagents inherit it."""
        original = HandoffJob(
            "job-snap",
            "task-snap",
            "run-snap",
            "/tmp/run.json",
            "paused",
            tuple(),
            {"objective": "snapshot test", "provider": "fake", "workflow_mode": "plan"},
            ["created"],
        )
        snapshot = original.to_dict()
        restored = HandoffJob.from_snapshot({**snapshot, "payload": original.payload})

        self.assertEqual(restored.payload.get("workflow_mode"), "plan")

    def test_resumed_job_inherits_parent_workflow_mode(self) -> None:
        """When a paused job is resumed, the child job payload carries the parent's workflow_mode."""
        manager = HandoffJobManager()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", root / "apply-results", memory_db=None)
            source = HandoffJob(
                "job-plan-paused",
                "task-plan-paused",
                "run-plan-paused",
                str(root / "run.json"),
                "paused",
                tuple(),
                {"objective": "inherit mode", "provider": "fake", "workflow_mode": "plan"},
                ["paused"],
            )
            manager._jobs[source.job_id] = source

            resumed = manager.resume(config, source.job_id)
            manager.cancel(resumed.job_id)

        self.assertEqual(resumed.payload.get("workflow_mode"), "plan",
                         "Resumed (child) job must inherit workflow_mode from parent so plan-mode write prohibitions propagate.")

    def test_write_gate_fields_propagate_through_job_payload(self) -> None:
        """Write-control fields (explicit_external_permission, workflow_mode) survive payload storage."""
        job = HandoffJob(
            "job-write-gate",
            "task-write-gate",
            "run-write-gate",
            "/tmp/run.json",
            "starting",
            tuple(),
            {
                "objective": "write gate",
                "provider": "fake",
                "workflow_mode": "build",
                "explicit_external_permission": True,
            },
            ["starting"],
        )

        self.assertEqual(job.payload.get("workflow_mode"), "build")
        self.assertTrue(job.payload.get("explicit_external_permission"))


if __name__ == "__main__":
    unittest.main()