import tempfile
import unittest
from pathlib import Path

from nemo_coding_platform.core.contracts import RuntimeState
from nemo_coding_platform.core.runtime import AgentRuntime, SandboxKind


class AgentRuntimeTests(unittest.TestCase):
    def test_create_initializes_worktree_runtime_ready_for_use(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = AgentRuntime.create("task one", "run one", tmp)

            self.assertEqual(runtime.state, RuntimeState.READY)
            self.assertEqual(runtime.session.sandbox, SandboxKind.WORKTREE)
            self.assertTrue(runtime.worktree_path.exists())
            self.assertEqual(runtime.runtime_id, "task-one-run-one")
            self.assertEqual(runtime.worktree.runtime_root, Path(tmp).resolve())

    def test_transition_returns_updated_runtime_without_mutating_original(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = AgentRuntime.create("task", "run", tmp)
            planning = runtime.transition(RuntimeState.PLANNING)

            self.assertEqual(runtime.state, RuntimeState.READY)
            self.assertEqual(planning.state, RuntimeState.PLANNING)
            self.assertEqual(planning.runtime_id, runtime.runtime_id)

    def test_to_status_dict_exposes_ui_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = AgentRuntime.create("task", "run", tmp)
            status = runtime.to_status_dict()

            self.assertEqual(status["runtime_id"], "task-run")
            self.assertEqual(status["sandbox"], "worktree")
            self.assertEqual(status["state"], "ready")
            self.assertIn("task-run", status["worktree_path"])

    def test_rejects_unsupported_sandbox_for_now(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                AgentRuntime.create("task", "run", tmp, sandbox=SandboxKind.PROCESS)


if __name__ == "__main__":
    unittest.main()
