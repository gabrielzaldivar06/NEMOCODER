import unittest

from nemo_coding_platform.core.product import (
    DESKTOP_PRODUCT,
    AutonomyLevel,
    ProductSurface,
    autonomy_contract,
)


class ProductContractTests(unittest.TestCase):
    def test_desktop_is_product_surface_and_cli_is_harness(self) -> None:
        self.assertEqual(DESKTOP_PRODUCT.target_surface, ProductSurface.DESKTOP)
        self.assertEqual(DESKTOP_PRODUCT.cli_role, ProductSurface.CLI_HARNESS)

    def test_desktop_contract_requires_core_ui_surfaces(self) -> None:
        self.assertTrue(DESKTOP_PRODUCT.requires_repo_picker)
        self.assertTrue(DESKTOP_PRODUCT.requires_task_workspace)
        self.assertTrue(DESKTOP_PRODUCT.requires_approval_queue)
        self.assertTrue(DESKTOP_PRODUCT.requires_artifact_timeline)
        self.assertTrue(DESKTOP_PRODUCT.requires_nemo_memory_trace)
        self.assertTrue(DESKTOP_PRODUCT.requires_model_settings)

    def test_high_autonomy_requires_isolation_and_review_gate(self) -> None:
        contract = autonomy_contract(AutonomyLevel.AUTONOMOUS_SANDBOX)

        self.assertTrue(contract.requires_isolation)
        self.assertTrue(contract.can_run_background)
        self.assertTrue(contract.can_spawn_subagents)
        self.assertFalse(contract.can_write_main_workspace_directly)
        self.assertTrue(contract.requires_review_before_merge)

    def test_background_agents_cannot_write_main_workspace_directly(self) -> None:
        contract = autonomy_contract(AutonomyLevel.BACKGROUND_AGENT)

        self.assertTrue(contract.requires_isolation)
        self.assertFalse(contract.can_write_main_workspace_directly)


if __name__ == "__main__":
    unittest.main()
