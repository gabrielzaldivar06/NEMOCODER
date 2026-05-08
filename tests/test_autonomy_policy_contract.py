import unittest

from nemo_coding_platform.core.product import AutonomyLevel, autonomy_contract


class AutonomyPolicyContractTests(unittest.TestCase):
    def test_manual_mode_stays_human_gated(self) -> None:
        contract = autonomy_contract(AutonomyLevel.MANUAL)

        self.assertFalse(contract.requires_isolation)
        self.assertFalse(contract.can_run_background)
        self.assertFalse(contract.can_spawn_subagents)
        self.assertFalse(contract.can_write_main_workspace_directly)
        self.assertTrue(contract.requires_review_before_merge)

    def test_autonomous_sandbox_mode_requires_isolation_and_merge_gate(self) -> None:
        contract = autonomy_contract(AutonomyLevel.AUTONOMOUS_SANDBOX)

        self.assertTrue(contract.requires_isolation)
        self.assertTrue(contract.can_run_background)
        self.assertTrue(contract.can_spawn_subagents)
        self.assertFalse(contract.can_write_main_workspace_directly)
        self.assertTrue(contract.requires_review_before_merge)

    def test_background_and_full_handoff_require_checkpoints(self) -> None:
        background = autonomy_contract(AutonomyLevel.BACKGROUND_AGENT)
        full_handoff = autonomy_contract(AutonomyLevel.FULL_HANDOFF)

        self.assertTrue(background.can_continue_without_human_interaction)
        self.assertTrue(background.requires_checkpoints)
        self.assertTrue(full_handoff.can_continue_without_human_interaction)
        self.assertTrue(full_handoff.requires_spec_driven_development)
        self.assertTrue(full_handoff.requires_checkpoints)

    def test_team_ci_mode_keeps_review_gate_and_isolation(self) -> None:
        contract = autonomy_contract(AutonomyLevel.TEAM_CI)

        self.assertTrue(contract.requires_isolation)
        self.assertTrue(contract.can_run_background)
        self.assertTrue(contract.can_spawn_subagents)
        self.assertFalse(contract.can_write_main_workspace_directly)
        self.assertTrue(contract.requires_review_before_merge)


if __name__ == "__main__":
    unittest.main()