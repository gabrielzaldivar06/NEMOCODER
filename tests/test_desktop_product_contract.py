import unittest

from nemo_coding_platform.core.product import (
    BackendProtocol,
    DESKTOP_PRODUCT,
    DesktopShell,
    ProductSurface,
)


class DesktopProductContractTests(unittest.TestCase):
    def test_desktop_product_uses_tauri_shell(self) -> None:
        self.assertEqual(DESKTOP_PRODUCT.target_surface, ProductSurface.DESKTOP)
        self.assertEqual(DESKTOP_PRODUCT.cli_role, ProductSurface.CLI_HARNESS)
        self.assertEqual(DESKTOP_PRODUCT.shell, DesktopShell.TAURI)

    def test_desktop_product_uses_local_http_backend_protocol(self) -> None:
        self.assertEqual(DESKTOP_PRODUCT.backend_protocol, BackendProtocol.HTTP_LOCAL)

    def test_desktop_product_requires_mission_control_core_surfaces(self) -> None:
        self.assertTrue(DESKTOP_PRODUCT.requires_repo_picker)
        self.assertTrue(DESKTOP_PRODUCT.requires_task_workspace)
        self.assertTrue(DESKTOP_PRODUCT.requires_approval_queue)
        self.assertTrue(DESKTOP_PRODUCT.requires_artifact_timeline)
        self.assertTrue(DESKTOP_PRODUCT.requires_nemo_memory_trace)
        self.assertTrue(DESKTOP_PRODUCT.requires_model_settings)


if __name__ == "__main__":
    unittest.main()