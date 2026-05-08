import unittest

from aider.nemo_platform.audit import AuditEvent, AuditEventType, AuditLog, RiskFlag
from aider.nemo_platform.autonomy import AutonomyLevel, IsolationKind, autonomy_contract
from aider.nemo_platform.nemo_tools import NEMO_TOOLS, NemoToolRisk, NemoToolSuite, nemo_tools_for_phase
from aider.nemo_platform.permissions import (
    PermissionAction,
    PermissionEvaluator,
    PermissionRule,
    default_aider_product_rules,
)
from aider.nemo_platform.platform_info import platform_info, platform_info_json
from aider.nemo_platform.phases import AgentPhase, phase_from_execution_phase
from aider.nemo_platform.runtime import AgentRuntimeSession, RuntimeState, RuntimeStateMachine


class NemoPlatformContractTests(unittest.TestCase):
    def test_opencode_style_permission_precedence(self) -> None:
        evaluator = PermissionEvaluator()
        rules = [
            PermissionRule("bash", "*", PermissionAction.ASK, precedence=10),
            PermissionRule("bash", "git *", PermissionAction.ALLOW, precedence=20),
            PermissionRule("bash", "git push", PermissionAction.DENY, precedence=30),
        ]

        decision = evaluator.evaluate("bash", "git push", rules)

        self.assertEqual(decision.action, PermissionAction.DENY)

    def test_default_rules_keep_main_workspace_merge_review_gated(self) -> None:
        evaluator = PermissionEvaluator()

        decision = evaluator.evaluate(
            "merge_to_main_workspace",
            "feature-worktree",
            default_aider_product_rules(),
        )

        self.assertEqual(decision.action, PermissionAction.ASK)

    def test_high_autonomy_requires_isolation(self) -> None:
        contract = autonomy_contract(AutonomyLevel.AUTONOMOUS_SANDBOX)

        self.assertEqual(contract.isolation, IsolationKind.WORKTREE)
        self.assertTrue(contract.can_spawn_subagents)
        self.assertTrue(contract.can_run_background)
        self.assertFalse(contract.can_write_main_workspace_directly)
        self.assertTrue(contract.requires_review_before_merge)

    def test_full_handoff_is_unattended_but_review_gated(self) -> None:
        contract = autonomy_contract(AutonomyLevel.FULL_HANDOFF)

        self.assertEqual(contract.isolation, IsolationKind.CONTAINER)
        self.assertTrue(contract.can_spawn_subagents)
        self.assertTrue(contract.can_run_background)
        self.assertTrue(contract.can_continue_without_human_interaction)
        self.assertTrue(contract.requires_spec_driven_development)
        self.assertTrue(contract.requires_checkpoints)
        self.assertFalse(contract.can_write_main_workspace_directly)
        self.assertTrue(contract.requires_review_before_merge)

    def test_openhands_style_runtime_state_machine(self) -> None:
        machine = RuntimeStateMachine()
        session = AgentRuntimeSession("task-1")

        session = machine.transition(session, RuntimeState.READY)
        session = machine.transition(session, RuntimeState.PLANNING)
        session = machine.transition(session, RuntimeState.BUILDING)
        session = machine.transition(session, RuntimeState.REVIEWING)

        self.assertEqual(session.state, RuntimeState.REVIEWING)

        with self.assertRaises(ValueError):
            machine.transition(session, RuntimeState.BUILDING)

    def test_nemo_tools_cover_more_than_portfolios(self) -> None:
        plan_tools = nemo_tools_for_phase(AgentPhase.PLAN)
        names = {tool.name for tool in plan_tools}

        self.assertIn("prime_context", names)
        self.assertIn("context_bootstrap", names)
        self.assertIn("build_context_portfolio", names)
        self.assertIn("create_correction", names)
        self.assertIn("store_conversation", names)

    def test_nemo_tools_match_sprint_critical_root_surface(self) -> None:
        names = {tool.name for tool in NEMO_TOOLS}

        self.assertIn("get_current_time", names)
        self.assertIn("compress_context_artifact", names)
        self.assertIn("update_memory", names)
        self.assertIn("intent_anchor", names)

    def test_execution_phase_mapping_keeps_execute_as_build(self) -> None:
        self.assertEqual(phase_from_execution_phase("execute"), AgentPhase.BUILD)
        self.assertEqual(phase_from_execution_phase("plan"), AgentPhase.PLAN)

    def test_scheduling_tools_are_review_gated(self) -> None:
        review_tools = nemo_tools_for_phase(AgentPhase.REVIEW)
        scheduling_tools = [tool for tool in review_tools if tool.risk == NemoToolRisk.SCHEDULING_WRITE]

        self.assertTrue(scheduling_tools)
        for tool in scheduling_tools:
            self.assertIn(tool.suite, {NemoToolSuite.REMINDERS, NemoToolSuite.APPOINTMENTS})
            self.assertEqual(tool.phases, (AgentPhase.REVIEW,))

    def test_platform_info_serializes_core_contracts(self) -> None:
        info = platform_info()

        self.assertEqual(info["product_base"], "aider")
        self.assertIn("full_handoff", info["autonomy"])
        self.assertFalse(info["autonomy"]["full_handoff"]["can_write_main_workspace_directly"])
        self.assertTrue(info["autonomy"]["full_handoff"]["can_continue_without_human_interaction"])
        self.assertIn("prime_context", info["nemo_tools_by_phase"]["plan"])
        self.assertIn("create_correction", {tool["name"] for tool in info["nemo_tools"]})

    def test_platform_info_json_is_stable_machine_readable_output(self) -> None:
        output = platform_info_json(indent=None)

        self.assertIn('"schema_version": 1', output)
        self.assertIn('"extension_package": "aider.nemo_platform"', output)
        self.assertIn('"full_handoff"', output)

    def test_platform_info_includes_command_and_audit_capabilities(self) -> None:
        info = platform_info()

        self.assertEqual(info["command"]["entrypoint"], "python -m aider.nemo_platform")
        self.assertTrue(info["capabilities"]["audit_events"])
        self.assertIn("checkpoint_created", info["audit_event_capabilities"]["event_types"])
        self.assertIn("validation_failure", info["audit_event_capabilities"]["risk_flags"])

    def test_audit_events_are_append_only_and_serializable(self) -> None:
        log = AuditLog()
        event = AuditEvent(
            event_id="e1",
            run_id="r1",
            sequence=1,
            event_type=AuditEventType.CHECKPOINT_CREATED,
            summary="checkpoint",
            risk_flags=(RiskFlag.VALIDATION_FAILURE,),
        )

        log = log.append(event)

        self.assertEqual(log.events[0].to_dict()["event_type"], "checkpoint_created")
        self.assertEqual(log.events[0].to_dict()["risk_flags"], ["validation_failure"])
        with self.assertRaises(ValueError):
            log.append(event)


if __name__ == "__main__":
    unittest.main()