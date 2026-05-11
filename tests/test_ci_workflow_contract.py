import unittest
from pathlib import Path


class CiWorkflowContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.workflow = self.repo_root / ".github" / "workflows" / "nemo-code-ci.yml"
        self.source = self.workflow.read_text(encoding="utf-8")

    def test_workflow_dispatch_exposes_installer_mode_toggle(self) -> None:
        self.assertIn("workflow_dispatch:", self.source)
        self.assertIn("installer_mode:", self.source)
        self.assertIn("real_mcp_gate:", self.source)
        self.assertIn("type: boolean", self.source)

    def test_global_installer_mode_env_is_defined(self) -> None:
        self.assertIn("INSTALLER_MODE:", self.source)
        self.assertIn("vars.INSTALLER_MODE", self.source)

    def test_real_mcp_release_gate_is_opt_in_and_required_when_enabled(self) -> None:
        self.assertIn("NEMOCODE_REAL_MCP_REQUIRED:", self.source)
        self.assertIn("vars.NEMOCODE_REAL_MCP_REQUIRED", self.source)
        self.assertIn("Run required real NEMO MCP continuity gate", self.source)
        self.assertIn("test_mcp_native_real_server_continuity_across_sessions_and_core_tools", self.source)

    def test_installer_preview_job_is_real_windows_build(self) -> None:
        self.assertIn("installer-preview:", self.source)
        self.assertIn("runs-on: windows-latest", self.source)
        self.assertIn("npm run tauri:build", self.source)
        self.assertIn("tests.test_desktop_release_readiness_contract", self.source)

    def test_installer_evidence_artifact_is_published(self) -> None:
        self.assertIn("installer-evidence.json", self.source)
        self.assertIn("installer-evidence.md", self.source)
        self.assertIn("name: installer-transition-evidence", self.source)

    def test_installer_preview_is_blocked_when_release_is_no_go(self) -> None:
        self.assertIn("Download release dossier evidence", self.source)
        self.assertIn("release-go-no-go.json", self.source)
        self.assertIn("Fail installer preview when release decision is no-go", self.source)
        self.assertIn("$decisionPayload.decision -ne \"go\"", self.source)


if __name__ == "__main__":
    unittest.main()
