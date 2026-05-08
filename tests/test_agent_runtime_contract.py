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
        self.assertEqual(resumed.status, "cancelled")
        self.assertTrue(any("resumed from job-paused" in line for line in resumed.logs))


if __name__ == "__main__":
    unittest.main()