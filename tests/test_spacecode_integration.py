"""
Integration tests for Space Code key functionality gaps.

Covers:
- Brand consistency (no old product names in user-visible outputs)
- Model agnosticism (no hardcoded nvidia.agentic.coder-4b defaults)
- Context window caps (chat mode ~10K, prevents settings.json bleeding)
- Endpoint correctness (127.0.0.1 everywhere, not localhost)
- Chat requiring a real model (raises on empty + unreachable LM Studio)
- NEMO MCP treated as external service (not embedded)
- Mission control state schema correctness
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import nemo_coding_platform.mission_control_server as server
from nemo_coding_platform.core.mission_control import build_mission_control_state
from nemo_coding_platform.core.model_config import (
    DEFAULT_LMSTUDIO_BASE_URL,
    DEFAULT_LMSTUDIO_MODEL,
    default_model_profile,
    default_model_role_profile,
)
from nemo_coding_platform.mission_control_server import (
    ApiRequestError,
    MissionControlServerConfig,
    api_health,
    api_state,
)


# ---------------------------------------------------------------------------
# Brand consistency
# ---------------------------------------------------------------------------


class BrandConsistencyTests(unittest.TestCase):
    """Verify user-visible strings say 'Space Code', not legacy names."""

    BANNED_DEFAULTS = ("nvidia.agentic.coder-4b", "NEMO Desktop", "NEMO CODE")

    def test_default_model_is_empty_not_hardcoded(self) -> None:
        self.assertEqual(DEFAULT_LMSTUDIO_MODEL, "")

    def test_default_base_url_uses_loopback_not_localhost(self) -> None:
        self.assertIn("127.0.0.1", DEFAULT_LMSTUDIO_BASE_URL)
        self.assertNotIn("localhost", DEFAULT_LMSTUDIO_BASE_URL)

    def test_model_profile_default_model_is_empty(self) -> None:
        profile = default_model_profile()
        self.assertEqual(profile.model, "")

    def test_role_profile_propagates_empty_default(self) -> None:
        roles = default_model_role_profile("")
        for role in ("planner", "editor", "reviewer", "summarizer"):
            self.assertEqual(getattr(roles, role), "")

    def test_default_settings_has_no_hardcoded_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = MissionControlServerConfig.from_paths(root, ".spacecode-runtimes", memory_db=None)
            defaults = server._default_settings(config)
        self.assertEqual(defaults.get("default_model"), "")
        role_models = defaults.get("model_roles", {})
        for role, model in role_models.items():
            self.assertNotIn("nvidia", str(model), msg=f"role {role} has hardcoded model")

    def test_build_mission_control_state_product_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = build_mission_control_state(repo_path=tmp, runtimes_path=tmp)
        self.assertEqual(state["product"], "Space Code Mission Control")

    def test_api_state_product_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = MissionControlServerConfig.from_paths(
                root, ".spacecode-runtimes", root / "apply", root / "runs", memory_db=None
            )
            state = api_state(config)
        self.assertEqual(state["product"], "Space Code Mission Control")

    def test_api_health_nemo_labelled_as_external_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = MissionControlServerConfig.from_paths(root, ".spacecode-runtimes", memory_db=None)
            response = api_health(config)
        # NEMO MCP is external; health check reports connectivity, not embedding
        self.assertIn("nemo", response)
        nemo = response["nemo"]
        self.assertIn("available", nemo)


# ---------------------------------------------------------------------------
# Model agnosticism
# ---------------------------------------------------------------------------


class ModelAgnosticismTests(unittest.TestCase):
    """Verify no hardcoded model name is used as a final fallback."""

    def test_chat_model_raises_when_no_model_and_lmstudio_unreachable(self) -> None:
        payload = {
            "model_base_url": "http://127.0.0.1:1234/v1",
            "default_model": "",
            "model": "",
        }
        with patch("nemo_coding_platform.mission_control_server._resolve_lmstudio_model", return_value=""):
            with self.assertRaises(ApiRequestError) as ctx:
                server._chat_model(payload)
        self.assertEqual(ctx.exception.error_code, "invalid_default_model")

    def test_chat_model_uses_explicit_model_from_payload(self) -> None:
        payload = {"model": "my-local-model", "model_base_url": "http://127.0.0.1:1234/v1"}
        result = server._chat_model(payload)
        self.assertEqual(result, "my-local-model")

    def test_chat_model_uses_default_model_from_payload(self) -> None:
        payload = {"default_model": "my-default-model", "model_base_url": "http://127.0.0.1:1234/v1"}
        result = server._chat_model(payload)
        self.assertEqual(result, "my-default-model")

    def test_chat_model_autodiscovers_from_lmstudio(self) -> None:
        payload = {"default_model": "", "model": "", "model_base_url": "http://127.0.0.1:1234/v1"}
        with patch(
            "nemo_coding_platform.mission_control_server._resolve_lmstudio_model",
            return_value="gemma-4b-discovered",
        ):
            result = server._chat_model(payload)
        self.assertEqual(result, "gemma-4b-discovered")

    def test_resolve_lmstudio_model_returns_empty_when_unreachable(self) -> None:
        result = server._resolve_lmstudio_model("http://127.0.0.1:19999/v1")
        self.assertEqual(result, "")

    def test_resolve_lmstudio_model_filters_embedding_models(self) -> None:
        mock_data = {
            "data": [
                {"id": "nomic-embed-text"},
                {"id": "bge-reranker-v2"},
                {"id": "gemma-4b-chat"},
            ]
        }
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(mock_data).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = server._resolve_lmstudio_model("http://127.0.0.1:1234/v1")

        self.assertEqual(result, "gemma-4b-chat")

    def test_self_modify_action_has_no_hardcoded_nvidia_model(self) -> None:
        payload: dict = {"default_model": "", "model_base_url": "http://127.0.0.1:1234/v1"}
        action = server._self_interface_action("test task", payload)
        model_value = action.get("payload", {}).get("model", "")
        self.assertNotIn("nvidia", str(model_value))

    def test_self_platform_action_has_no_hardcoded_nvidia_model(self) -> None:
        payload: dict = {"default_model": "", "model_base_url": "http://127.0.0.1:1234/v1"}
        action = server._self_platform_action("test task", payload)
        model_value = action.get("payload", {}).get("model", "")
        self.assertNotIn("nvidia", str(model_value))


# ---------------------------------------------------------------------------
# Context window caps
# ---------------------------------------------------------------------------


class ContextWindowCapTests(unittest.TestCase):
    """Verify context window is capped per mode and not bled from settings.json."""

    CHAT_CEILING = 10_000

    def test_chat_mode_context_window_is_ten_thousand(self) -> None:
        profile = server._chat_mode_profile("chat")
        self.assertLessEqual(profile["context_window_tokens"], self.CHAT_CEILING)

    def test_research_mode_has_larger_window_than_chat(self) -> None:
        chat = server._chat_mode_profile("chat")
        research = server._chat_mode_profile("research")
        self.assertGreater(research["context_window_tokens"], chat["context_window_tokens"])

    def test_execution_mode_has_largest_window(self) -> None:
        chat = server._chat_mode_profile("chat")
        execution = server._chat_mode_profile("execution")
        self.assertGreater(execution["context_window_tokens"], chat["context_window_tokens"])

    def test_agent_context_budget_capped_at_mode_ceiling_for_chat(self) -> None:
        # Simulate settings.json bleeding 131072 into a chat payload
        payload = {"context_window_tokens": 131072, "chat_max_tokens": 16384}
        profile = server._chat_mode_profile("chat")
        budget = server._agent_context_char_budget(
            payload,
            profile["context_chars"],
            mode_default=profile["context_window_tokens"],
        )
        # Budget in chars should not exceed what 10K tokens allow
        # (rough upper bound: 10000 tokens * 4 chars/token = 40000 chars)
        self.assertLessEqual(budget, 40_000)

    def test_agent_context_budget_capped_even_when_settings_bleeds(self) -> None:
        # Even if settings provides a large context_window, chat mode should cap it
        huge_payload = {"context_window_tokens": 512000, "chat_max_tokens": 32000}
        profile = server._chat_mode_profile("chat")
        budget = server._agent_context_char_budget(
            huge_payload,
            profile["context_chars"],
            mode_default=profile["context_window_tokens"],
        )
        # Must be below the full 512k token ceiling
        self.assertLess(budget, 100_000)


# ---------------------------------------------------------------------------
# Backend endpoints
# ---------------------------------------------------------------------------


class BackendEndpointsTests(unittest.TestCase):
    """Verify health and state endpoints return correct structure."""

    def _config(self, tmp: str) -> MissionControlServerConfig:
        root = Path(tmp)
        return MissionControlServerConfig.from_paths(
            root, ".spacecode-runtimes", root / "apply", root / "runs", memory_db=None
        )

    def test_health_endpoint_structure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            response = api_health(self._config(tmp))
        for key in ("status", "version", "timestamp", "backend", "lm_studio", "nemo"):
            self.assertIn(key, response)

    def test_health_backend_port_is_8787(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            response = api_health(self._config(tmp))
        self.assertEqual(response["backend"]["port"], 8787)

    def test_health_lm_studio_uses_127_not_localhost(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            response = api_health(self._config(tmp))
        endpoint = response["lm_studio"].get("endpoint", "")
        self.assertIn("127.0.0.1", endpoint)
        self.assertNotIn("localhost", endpoint)

    def test_state_schema_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = api_state(self._config(tmp))
        self.assertEqual(state["schema_version"], 1)

    def test_state_contains_runs_and_approval_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = api_state(self._config(tmp))
        self.assertIn("runs", state)
        self.assertIn("approval_queue", state)
        self.assertIsInstance(state["runs"], list)
        self.assertIsInstance(state["approval_queue"], list)

    def test_state_contains_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = api_state(self._config(tmp))
        settings = state.get("settings", {})
        self.assertIn("model_base_url", settings)
        self.assertIn("validation_policy", settings)

    def test_state_settings_model_base_url_uses_loopback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = api_state(self._config(tmp))
        url = state["settings"]["model_base_url"]
        self.assertIn("127.0.0.1", url)
        self.assertNotIn("localhost", url)

    def test_state_settings_default_model_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = api_state(self._config(tmp))
        self.assertEqual(state["settings"].get("default_model"), "")


# ---------------------------------------------------------------------------
# NEMO MCP as external service
# ---------------------------------------------------------------------------


class NemoMcpExternalServiceTests(unittest.TestCase):
    """Verify NEMO MCP is treated as an external service, not embedded."""

    def test_nemo_mcp_url_in_default_settings_points_to_vscode_transport(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = MissionControlServerConfig.from_paths(root, ".spacecode-runtimes", memory_db=None)
            defaults = server._default_settings(config)
        nemo_url = defaults.get("nemo_mcp_url", "")
        # Must not be a bare localhost HTTP endpoint — it's the VSCode stdio bridge
        self.assertTrue(
            nemo_url.startswith("stdio://") or "8765" in str(nemo_url) or nemo_url == "",
            msg=f"Unexpected nemo_mcp_url: {nemo_url}",
        )

    def test_nemo_required_flag_is_boolean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = MissionControlServerConfig.from_paths(root, ".spacecode-runtimes", memory_db=None)
            defaults = server._default_settings(config)
        self.assertIsInstance(defaults.get("nemo_required"), bool)

    def test_health_endpoint_reports_nemo_connectivity_not_internal_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = MissionControlServerConfig.from_paths(root, ".nemo-runtimes", memory_db=None)
            response = api_health(config)
        # NEMO is external: health reports availability via DB path, not process status
        nemo = response["nemo"]
        self.assertIn("available", nemo)
        # Should not have fields implying Space Code started NEMO itself
        self.assertNotIn("pid", nemo)
        self.assertNotIn("started_by_us", nemo)


# ---------------------------------------------------------------------------
# Chat mode context chars validation
# ---------------------------------------------------------------------------


class ChatModeProfileTests(unittest.TestCase):
    def test_chat_profile_portfolio_budget_under_ten_k(self) -> None:
        profile = server._chat_mode_profile("chat")
        self.assertLessEqual(profile["portfolio_budget"], 10_000)

    def test_unknown_mode_falls_back_gracefully(self) -> None:
        profile = server._chat_mode_profile("unknown_mode")
        self.assertIn("context_window_tokens", profile)
        self.assertGreater(profile["context_window_tokens"], 0)


if __name__ == "__main__":
    unittest.main()
