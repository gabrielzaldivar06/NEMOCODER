import unittest

from nemo_coding_platform.core.nemo_lifecycle import (
    NemoLifecyclePhase,
    lifecycle_contract,
    nemo_is_not_portfolio_only,
    scheduling_tools_are_review_or_close_gated,
    tool_allowed_in_lifecycle,
)


class NemoLifecycleTests(unittest.TestCase):
    def test_start_bootstraps_context(self) -> None:
        contract = lifecycle_contract(NemoLifecyclePhase.START)

        self.assertIn("prime_context", contract.tool_names)
        self.assertFalse(contract.scheduling_allowed)

    def test_build_can_checkpoint_but_not_schedule(self) -> None:
        contract = lifecycle_contract(NemoLifecyclePhase.BUILD)

        self.assertTrue(contract.checkpoint_writeback)
        self.assertFalse(contract.scheduling_allowed)
        self.assertFalse(tool_allowed_in_lifecycle(NemoLifecyclePhase.BUILD, "create_reminder"))

    def test_review_and_close_can_write_memory(self) -> None:
        self.assertTrue(lifecycle_contract(NemoLifecyclePhase.REVIEW).memory_write_allowed)
        self.assertTrue(lifecycle_contract(NemoLifecyclePhase.CLOSE).memory_write_allowed)

    def test_nemo_is_full_memory_plane(self) -> None:
        self.assertTrue(nemo_is_not_portfolio_only())
        self.assertTrue(scheduling_tools_are_review_or_close_gated())


if __name__ == "__main__":
    unittest.main()