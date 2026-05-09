import json
import unittest
from pathlib import Path


class DesktopReleaseReadinessContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.desktop_root = self.repo_root / "apps" / "nemo-desktop"
        self.package_json = self.desktop_root / "package.json"
        self.tauri_main = self.desktop_root / "src-tauri" / "src" / "main.rs"
        self.frontend_main = self.desktop_root / "src" / "main.tsx"
        self.backend_service = self.desktop_root / "src" / "services" / "backend.ts"

    def test_desktop_package_exposes_rc_scripts(self) -> None:
        payload = json.loads(self.package_json.read_text(encoding="utf-8"))
        scripts = payload.get("scripts", {})

        self.assertIn("build", scripts)
        self.assertIn("tauri:dev", scripts)
        self.assertIn("tauri:build", scripts)

    def test_tauri_backend_registers_required_bridge_commands(self) -> None:
        source = self.tauri_main.read_text(encoding="utf-8")

        required = (
            "start_backend",
            "stop_backend",
            "query_backend_status",
            "get_settings",
            "save_settings",
            "proxy_backend_request",
            "pick_folder",
            "health_check",
        )
        for command in required:
            self.assertIn(command, source)

    def test_tauri_default_settings_support_first_run(self) -> None:
        source = self.tauri_main.read_text(encoding="utf-8")

        self.assertIn("backend_port: 8787", source)
        self.assertIn('lm_studio_url: "http://localhost:1234/v1"', source)
        self.assertIn('nemo_database_path: ".nemo-memory.db"', source)
        self.assertIn("auto_start_backend: true", source)

    def test_frontend_exposes_recovery_ui_when_backend_or_web_is_down(self) -> None:
        source = self.frontend_main.read_text(encoding="utf-8")

        self.assertIn("Desktop is not ready yet", source)
        self.assertIn("Retry Setup", source)
        self.assertIn("Start Backend", source)
        self.assertIn("Save Settings", source)
        self.assertIn("Reset", source)
        self.assertIn("Mission Control UI is not running", source)
        self.assertIn("setup_required", source)
        self.assertIn("degraded", source)
        self.assertIn("fatal_error", source)
        self.assertIn("Connected", source)
        self.assertIn("LM Studio URL", source)
        self.assertIn("NEMO Database Path", source)
        self.assertIn("Auto start backend on launch", source)

    def test_setup_detection_contract_uses_backend_startup_probe(self) -> None:
        source = self.backend_service.read_text(encoding="utf-8")

        self.assertIn('path: "/api/startup"', source)
        self.assertIn("lmStudioFound", source)
        self.assertIn("nemoDbFound", source)
        self.assertIn("ready", source)
        self.assertIn("status", source)
        self.assertIn("issues", source)


if __name__ == "__main__":
    unittest.main()
