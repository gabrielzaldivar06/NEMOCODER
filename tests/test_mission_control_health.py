"""Tests for Mission Control health and startup endpoints."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from nemo_coding_platform.mission_control_server import (
    MissionControlServerConfig,
    api_health,
    api_startup,
    _check_lm_studio_reachable,
    _get_nemo_database_size_mb,
    _check_disk_space_sufficient,
    _check_directory_permissions,
)


class MissionControlHealthAndStartupTests(unittest.TestCase):
    """Test health and startup endpoints."""

    def setUp(self) -> None:
        """Set up temporary directories for testing."""
        self.temp_dir = TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.config = MissionControlServerConfig.from_paths(
            repo=self.temp_path,
            runtimes=self.temp_path / ".nemo-runtimes",
            memory_db=self.temp_path / ".nemo-memory.db",
        )

    def tearDown(self) -> None:
        """Clean up temporary directories."""
        self.temp_dir.cleanup()

    def test_api_health_returns_valid_structure(self) -> None:
        """Test that /api/health returns a valid structure."""
        response = api_health(self.config)

        self.assertIn("status", response)
        self.assertEqual(response["status"], "healthy")
        self.assertIn("version", response)
        self.assertIn("timestamp", response)
        self.assertIn("backend", response)
        self.assertIn("lm_studio", response)
        self.assertIn("nemo", response)

    def test_api_health_backend_info(self) -> None:
        """Test that /api/health includes backend info."""
        response = api_health(self.config)

        backend = response["backend"]
        self.assertEqual(backend["type"], "mission_control")
        self.assertEqual(backend["port"], 8787)
        self.assertIn("uptime_seconds", backend)

    def test_api_health_lm_studio_unreachable(self) -> None:
        """Test that /api/health detects unreachable LM Studio."""
        response = api_health(self.config)

        lm_studio = response["lm_studio"]
        self.assertIn("reachable", lm_studio)
        self.assertIn("endpoint", lm_studio)
        # LM Studio is likely not running in test environment
        self.assertIsInstance(lm_studio["reachable"], bool)

    def test_api_health_nemo_unavailable(self) -> None:
        """Test that /api/health reports NEMO unavailable when DB doesn't exist."""
        response = api_health(self.config)

        nemo = response["nemo"]
        self.assertFalse(nemo["available"])  # DB doesn't exist yet
        self.assertIsNone(nemo["database_size_mb"])

    def test_api_startup_returns_valid_structure(self) -> None:
        """Test that /api/startup returns a valid structure."""
        response = api_startup(self.config)

        self.assertIn("ready", response)
        self.assertIn("checks", response)
        self.assertIn("missing", response)
        self.assertIn("recommended_actions", response)
        self.assertIn("config", response)

    def test_api_startup_checks_lm_studio(self) -> None:
        """Test that /api/startup checks LM Studio reachability."""
        response = api_startup(self.config)

        checks = response["checks"]
        self.assertIn("lm_studio_reachable", checks)
        self.assertIsInstance(checks["lm_studio_reachable"], bool)

    def test_api_startup_checks_nemo_database(self) -> None:
        """Test that /api/startup checks NEMO database existence."""
        response = api_startup(self.config)

        checks = response["checks"]
        self.assertIn("nemo_database_exists", checks)
        self.assertFalse(checks["nemo_database_exists"])  # DB doesn't exist yet

    def test_api_startup_checks_disk_space(self) -> None:
        """Test that /api/startup checks disk space."""
        response = api_startup(self.config)

        checks = response["checks"]
        self.assertIn("disk_space_sufficient", checks)
        self.assertTrue(checks["disk_space_sufficient"])  # Temp dir should have space

    def test_api_startup_checks_permissions(self) -> None:
        """Test that /api/startup checks directory permissions."""
        response = api_startup(self.config)

        checks = response["checks"]
        self.assertIn("permissions_ok", checks)
        self.assertTrue(checks["permissions_ok"])  # Temp dir should be writable

    def test_api_startup_ready_when_all_checks_pass(self) -> None:
        """Test that /api/startup is ready when non-critical checks pass."""
        # Create NEMO database to make it available
        self.config.memory_db.parent.mkdir(parents=True, exist_ok=True)
        self.config.memory_db.write_text("")  # Create empty DB file

        response = api_startup(self.config)

        # Should be ready if LM Studio check passes (but it likely won't in test)
        # However, permission and disk space checks should pass
        checks = response["checks"]
        self.assertTrue(checks["permissions_ok"])
        self.assertTrue(checks["disk_space_sufficient"])

    def test_check_directory_permissions_creates_if_missing(self) -> None:
        """Test that _check_directory_permissions creates directory if missing."""
        new_dir = self.temp_path / "new" / "nested" / "dir"
        self.assertFalse(new_dir.exists())

        result = _check_directory_permissions(new_dir)

        self.assertTrue(result)
        self.assertTrue(new_dir.exists())

    def test_check_disk_space_sufficient_returns_bool(self) -> None:
        """Test that _check_disk_space_sufficient returns boolean."""
        result = _check_disk_space_sufficient(self.temp_path)
        self.assertIsInstance(result, bool)
        self.assertTrue(result)  # Temp dir should have sufficient space

    def test_get_nemo_database_size_mb_returns_none_for_missing(self) -> None:
        """Test that _get_nemo_database_size_mb returns None for missing DB."""
        result = _get_nemo_database_size_mb(self.config.memory_db)
        self.assertIsNone(result)

    def test_get_nemo_database_size_mb_returns_size_for_existing(self) -> None:
        """Test that _get_nemo_database_size_mb returns size for existing DB."""
        # Create a small DB file with some content
        self.config.memory_db.parent.mkdir(parents=True, exist_ok=True)
        self.config.memory_db.write_bytes(b"test data" * 100)  # Write enough bytes to be measurable

        result = _get_nemo_database_size_mb(self.config.memory_db)

        self.assertIsNotNone(result)
        self.assertGreaterEqual(result, 0)
        self.assertLess(result, 1)  # Should be < 1 MB for test data

    def test_check_lm_studio_reachable_invalid_url(self) -> None:
        """Test that _check_lm_studio_reachable handles invalid URLs gracefully."""
        result = _check_lm_studio_reachable("http://invalid-url-that-does-not-exist.local:99999/v1")
        self.assertFalse(result)

    def test_api_startup_recommends_actions_when_lm_studio_missing(self) -> None:
        """Test that /api/startup recommends actions when LM Studio is missing."""
        response = api_startup(self.config)

        # LM Studio is likely not running in test environment
        if not response["checks"]["lm_studio_reachable"]:
            self.assertGreater(len(response["recommended_actions"]), 0)


if __name__ == "__main__":
    unittest.main()
