import unittest
import tempfile
from pathlib import Path

from nemo_coding_platform.core.contracts import ApprovalLevel, ExecutionPhase, RuntimeState
from nemo_coding_platform.core.memory import (
    NEMO_TOOL_REGISTRY,
    NemoToolRisk,
    NemoToolSuite,
    PHASE_PORTFOLIOS,
    default_portfolio_request,
    nemo_tools_by_suite,
    nemo_tools_for_phase,
)
from nemo_coding_platform.core.mutations import FileWrite, MutationPlan, QualityMutationEngine
from nemo_coding_platform.core.orchestrator import ReviewDecision, SupervisedWorkflowRunner, phase_contract
from nemo_coding_platform.core.runtime import RuntimeController, RuntimeSession, SandboxKind
from nemo_coding_platform.core.workspace import Workspace


class WorkflowContractTests(unittest.TestCase):
    def test_plan_phase_is_read_only(self) -> None:
        contract = phase_contract(ExecutionPhase.PLAN)

        self.assertTrue(contract.read_only)
        self.assertFalse(contract.mutation_allowed)
        self.assertEqual(contract.approval_level, ApprovalLevel.NONE)

    def test_execute_phase_uses_fast_portfolio_mode(self) -> None:
        request = default_portfolio_request("Implement feature", ExecutionPhase.EXECUTE, "feature-x")

        self.assertEqual(request.mode, PHASE_PORTFOLIOS[ExecutionPhase.EXECUTE].mode)
        self.assertEqual(request.token_budget, 1500)

    def test_nemo_registry_includes_non_portfolio_tools(self) -> None:
        tool_names = {tool.name for tool in NEMO_TOOL_REGISTRY}

        self.assertIn("build_context_portfolio", tool_names)
        self.assertIn("prime_context", tool_names)
        self.assertIn("create_correction", tool_names)
        self.assertIn("store_conversation", tool_names)
        self.assertIn("get_system_health", tool_names)

    def test_plan_phase_can_read_context_but_not_schedule(self) -> None:
        plan_tools = nemo_tools_for_phase(ExecutionPhase.PLAN)
        plan_tool_names = {tool.name for tool in plan_tools}
        plan_risks = {tool.risk for tool in plan_tools}

        self.assertIn("prime_context", plan_tool_names)
        self.assertIn("build_context_portfolio", plan_tool_names)
        self.assertNotIn(NemoToolRisk.SCHEDULING_WRITE, plan_risks)

    def test_reminder_tools_are_review_gated(self) -> None:
        reminder_tools = nemo_tools_by_suite(NemoToolSuite.REMINDERS)

        self.assertTrue(reminder_tools)
        for tool in reminder_tools:
            self.assertEqual(tool.phase_access, (ExecutionPhase.REVIEW,))

    def test_runtime_transition_requires_valid_edge(self) -> None:
        controller = RuntimeController()
        session = RuntimeSession(session_id="s1", sandbox=SandboxKind.WORKTREE)

        session = controller.transition(session, RuntimeState.READY)
        session = controller.transition(session, RuntimeState.PLANNING)
        session = controller.transition(session, RuntimeState.AWAITING_APPROVAL)

        self.assertEqual(session.state, RuntimeState.AWAITING_APPROVAL)

        with self.assertRaises(ValueError):
            controller.transition(session, RuntimeState.COMPLETED)

    def test_supervised_runner_requires_review_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = SupervisedWorkflowRunner(QualityMutationEngine(Workspace.from_path(tmp)))
            plan = MutationPlan(writes=(FileWrite("result.txt", "ok\n"),), approved=True)

            with self.assertRaises(PermissionError):
                runner.execute_approved_plan(plan, ReviewDecision(approved=False, summary="not ready"))

    def test_supervised_runner_requires_execution_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = SupervisedWorkflowRunner(QualityMutationEngine(Workspace.from_path(tmp)))
            plan = MutationPlan(writes=(FileWrite("result.txt", "ok\n"),), approved=False)

            with self.assertRaises(PermissionError):
                runner.execute_approved_plan(plan, ReviewDecision(approved=True, summary="ready"))

    def test_supervised_runner_applies_after_both_gates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = SupervisedWorkflowRunner(QualityMutationEngine(Workspace.from_path(tmp)))
            plan = MutationPlan(writes=(FileWrite("result.txt", "ok\n"),), approved=True)

            result = runner.execute_approved_plan(plan, ReviewDecision(approved=True, summary="ready"))

            self.assertEqual(result.dry_run.creates, ("result.txt",))
            self.assertEqual(result.applied_files, ("result.txt",))
            self.assertEqual((Path(tmp) / "result.txt").read_text(encoding="utf-8"), "ok\n")


if __name__ == "__main__":
    unittest.main()
