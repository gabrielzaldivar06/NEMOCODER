"""Tests for Pollinations media tool helpers."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import nemo_coding_platform.mission_control_server as mcs
from nemo_coding_platform.mission_control_server import MissionControlServerConfig


def _make_config(tmp: str) -> MissionControlServerConfig:
    root = Path(tmp)
    repo = root / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / ".git").mkdir(exist_ok=True)
    return MissionControlServerConfig.from_paths(
        repo, root / "runtimes", root / "apply", memory_db=None
    )


def _mock_urlopen(content: bytes) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.read.return_value = content
    mock_resp.headers.get.return_value = "application/octet-stream"
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


class TestPollinationsImage(unittest.TestCase):
    def test_saves_artifact_and_returns_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(tmp)
            with patch("urllib.request.urlopen", return_value=_mock_urlopen(b"\xff\xd8\xff" + b"x" * 1100)):
                result = mcs._pollinations_image({"prompt": "a sunset"}, config)
            self.assertIn("artifact_path", result)
            self.assertEqual(result["model_used"], "flux")
            self.assertTrue(Path(result["artifact_path"]).exists())

    def test_custom_model_and_dimensions(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(tmp)
            with patch("urllib.request.urlopen", return_value=_mock_urlopen(b"\xff\xd8\xff" + b"x" * 1100)) as mock_open:
                mcs._pollinations_image({"prompt": "city", "model": "flux-realism", "width": 512, "height": 512}, config)
            call_url = str(mock_open.call_args[0][0].full_url)
        self.assertIn("flux-realism", call_url)
        self.assertIn("width=512", call_url)

    def test_missing_prompt_returns_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(tmp)
            result = mcs._pollinations_image({}, config)
        self.assertIn("error", result)

    def test_network_error_returns_error_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(tmp)
            with patch("urllib.request.urlopen", side_effect=Exception("connection refused")):
                result = mcs._pollinations_image({"prompt": "test"}, config)
        self.assertIn("error", result)


class TestPollinationsAudio(unittest.TestCase):
    def test_saves_mp3_and_returns_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(tmp)
            with patch("urllib.request.urlopen", return_value=_mock_urlopen(b"ID3fake")):
                result = mcs._pollinations_audio({"text": "hello world"}, config)
        self.assertIn("artifact_path", result)
        self.assertEqual(result["voice_used"], "nova")
        self.assertTrue(result["artifact_path"].endswith(".mp3"))

    def test_custom_voice(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(tmp)
            with patch("urllib.request.urlopen", return_value=_mock_urlopen(b"ID3fake")) as mock_open:
                mcs._pollinations_audio({"text": "hi", "voice": "heart"}, config)
            call_url = str(mock_open.call_args[0][0].full_url)
        self.assertIn("voice=heart", call_url)
        self.assertIn("text.pollinations.ai", call_url)
        self.assertIn("openai-audio", call_url)

    def test_missing_text_returns_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(tmp)
            result = mcs._pollinations_audio({}, config)
        self.assertIn("error", result)


class TestPollinationsText(unittest.TestCase):
    def test_returns_text_inline(self):
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(b"Once upon a time")):
            result = mcs._pollinations_text({"prompt": "tell me a story"})
        self.assertIn("text", result)
        self.assertEqual(result["text"], "Once upon a time")
        self.assertEqual(result["model_used"], "openai-fast")

    def test_custom_model(self):
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(b"code here")) as mock_open:
            mcs._pollinations_text({"prompt": "write a sort", "model": "qwen-coder"})
        call_url = str(mock_open.call_args[0][0].full_url)
        self.assertIn("qwen-coder", call_url)

    def test_missing_prompt_returns_error(self):
        result = mcs._pollinations_text({})
        self.assertIn("error", result)

    def test_does_not_save_to_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(tmp)
            with patch("urllib.request.urlopen", return_value=_mock_urlopen(b"result")):
                result = mcs._pollinations_text({"prompt": "test"})
            artifacts = list((Path(tmp) / "runtimes").rglob("*"))
        self.assertNotIn("artifact_path", result)
        self.assertEqual(artifacts, [])


class TestExecutePollinationsTool(unittest.TestCase):
    def test_dispatches_to_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(tmp)
            with patch("nemo_coding_platform.mission_control_server._pollinations_image",
                       return_value={"artifact_path": "/fake/path.jpg"}) as mock_img:
                result = mcs._execute_pollinations_tool(
                    {"tool": "generate_image", "params": {"prompt": "test"}}, config
                )
        mock_img.assert_called_once()
        self.assertEqual(result["artifact_path"], "/fake/path.jpg")

    def test_dispatches_to_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(tmp)
            with patch("nemo_coding_platform.mission_control_server._pollinations_audio",
                       return_value={"artifact_path": "/fake/audio.mp3"}) as mock_aud:
                mcs._execute_pollinations_tool(
                    {"tool": "generate_audio", "params": {"text": "hello"}}, config
                )
        mock_aud.assert_called_once()

    def test_dispatches_to_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(tmp)
            with patch("nemo_coding_platform.mission_control_server._pollinations_text",
                       return_value={"text": "result"}) as mock_txt:
                mcs._execute_pollinations_tool(
                    {"tool": "generate_text", "params": {"prompt": "task"}}, config
                )
        mock_txt.assert_called_once()

    def test_dispatches_to_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(tmp)
            with patch("nemo_coding_platform.mission_control_server._pollinations_video",
                       return_value={"artifact_path": "/fake/video.mp4"}) as mock_vid:
                mcs._execute_pollinations_tool(
                    {"tool": "generate_video", "params": {"prompt": "a cat"}}, config
                )
        mock_vid.assert_called_once()

    def test_unknown_tool_returns_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(tmp)
            result = mcs._execute_pollinations_tool({"tool": "unknown_tool", "params": {}}, config)
        self.assertIn("error", result)


class TestPollinationsVideo(unittest.TestCase):
    def test_returns_not_available_error(self):
        """Video endpoint is not available — function returns descriptive error immediately."""
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(tmp)
            result = mcs._pollinations_video({"prompt": "a cat running"}, config)
        self.assertIn("error", result)
        self.assertIn("not available", result["error"])

    def test_returns_not_available_error_with_any_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(tmp)
            result = mcs._pollinations_video({"prompt": "a dog jumping"}, config)
        self.assertIn("error", result)

    def test_missing_prompt_returns_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(tmp)
            result = mcs._pollinations_video({}, config)
        self.assertIn("error", result)


class TestApiAgentMessagePollinationsIntegration(unittest.TestCase):
    def _make_config_and_payload(self, tmp: str) -> tuple:
        config = _make_config(tmp)
        payload = {
            "message": "generate an image of a sunset",
            "model_base_url": "http://localhost:1234/v1",
            "history": [],
            "nemo_mcp_url": "",
        }
        return config, payload

    def _base_patches(self):
        """Return a list of context managers for common patches."""
        return [
            patch("nemo_coding_platform.mission_control_server._require_nemo_mcp_url",
                  return_value="http://127.0.0.1:8765/mcp/sse"),
            patch("nemo_coding_platform.mission_control_server._nemo_chat_tool_call",
                  return_value={"ok": True, "payload": {"context": "", "portfolio": {"estimated_tokens": 0}, "memories": []}}),
        ]

    def test_pollinations_image_result_appended_to_response(self):
        """When model response contains generate_image JSON, result annotation appears in output."""
        fake_model_response = '{"tool": "generate_image", "params": {"prompt": "a sunset over the ocean"}}'
        with tempfile.TemporaryDirectory() as tmp:
            config, payload = self._make_config_and_payload(tmp)
            with patch("nemo_coding_platform.mission_control_server._lmstudio_chat_completion",
                       return_value=(fake_model_response, None, "flux")), \
                 patch("nemo_coding_platform.mission_control_server._require_nemo_mcp_url",
                       return_value="http://127.0.0.1:8765/mcp/sse"), \
                 patch("nemo_coding_platform.mission_control_server._nemo_chat_tool_call",
                       return_value={"ok": True, "payload": {"context": "", "portfolio": {"estimated_tokens": 0}, "memories": []}}), \
                 patch("nemo_coding_platform.mission_control_server._pollinations_image",
                       return_value={"artifact_path": "/fake/sunset.jpg", "url": "https://img", "model_used": "flux"}):
                result = mcs.api_agent_message(config, payload, server=None)
        response_text = result.get("message", {}).get("content", "")
        self.assertIn("sunset.jpg", response_text)
        self.assertIn("[Media artifacts generated]", response_text)

    def test_no_pollinations_call_leaves_response_unchanged(self):
        """When model response has no Pollinations tool call, no annotation is added."""
        fake_model_response = "Here is a poem about the ocean."
        with tempfile.TemporaryDirectory() as tmp:
            config, payload = self._make_config_and_payload(tmp)
            with patch("nemo_coding_platform.mission_control_server._lmstudio_chat_completion",
                       return_value=(fake_model_response, None, "flux")), \
                 patch("nemo_coding_platform.mission_control_server._require_nemo_mcp_url",
                       return_value="http://127.0.0.1:8765/mcp/sse"), \
                 patch("nemo_coding_platform.mission_control_server._nemo_chat_tool_call",
                       return_value={"ok": True, "payload": {"context": "", "portfolio": {"estimated_tokens": 0}, "memories": []}}):
                result = mcs.api_agent_message(config, payload, server=None)
        response_text = result.get("message", {}).get("content", "")
        self.assertNotIn("[Media artifacts generated]", response_text)

    def test_video_returns_not_available_error(self):
        """Video endpoint is not publicly available — returns descriptive error without HTTP calls."""
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(tmp)
            result = mcs._pollinations_video({"prompt": "test"}, config)
        self.assertIn("error", result)
        self.assertIn("not available", result["error"])

    def test_video_error_suggests_alternatives(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(tmp)
            result = mcs._pollinations_video({"prompt": "test"}, config)
        self.assertIn("generate_image", result["error"])

    def test_image_size_guard_rejects_small_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(tmp)
            with patch("urllib.request.urlopen", return_value=_mock_urlopen(b"tiny")):
                result = mcs._pollinations_image({"prompt": "test"}, config)
        self.assertIn("error", result)
        self.assertIn("suspiciously small", result["error"])


class TestPollinationsToolCatalog(unittest.TestCase):
    def test_catalog_contains_all_four_tool_names(self):
        for tool in ("generate_image", "generate_audio", "generate_video", "generate_text"):
            self.assertIn(tool, mcs._AGENT_TOOL_CATALOG, f"Tool '{tool}' not in catalog")

    def test_schemas_contain_all_four_tool_names(self):
        schema_names = {s["function"]["name"] for s in mcs._AGENT_TOOL_SCHEMAS}
        for tool in ("generate_image", "generate_audio", "generate_video", "generate_text"):
            self.assertIn(tool, schema_names, f"Tool '{tool}' not in schemas")

    def test_pollinations_tools_constant_has_all_four(self):
        self.assertEqual(
            mcs.POLLINATIONS_TOOLS,
            frozenset({"generate_image", "generate_audio", "generate_video", "generate_text"}),
        )


class TestPlanLoopPollinationsIntegration(unittest.TestCase):
    def _fake_nemo(self, config, tool_calls, tool_name, **kw):
        return {"ok": True, "payload": {"context": "", "portfolio": {"estimated_tokens": 0}, "memories": []}}

    def test_generate_image_artifact_path_in_critique_prompt(self):
        """When code_response contains generate_image, the artifact path appears in the critique."""
        captured_critique_users = []

        def _lm_side(payload, system, user, **kw):
            if "evaluator" in system.lower():
                captured_critique_users.append(user)
                return '{"score":7,"present":[],"missing":[],"improvements":[],"summary":"ok"}'
            return '{"tool": "generate_image", "params": {"prompt": "chart"}}\nprint("hello")'

        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(tmp)
            with patch("nemo_coding_platform.mission_control_server._plan_lm_call",
                       side_effect=_lm_side), \
                 patch("nemo_coding_platform.mission_control_server._nemo_chat_tool_call",
                       side_effect=self._fake_nemo), \
                 patch("nemo_coding_platform.mission_control_server._pollinations_image",
                       return_value={"artifact_path": "/fake/chart.jpg", "url": "https://img", "model_used": "flux"}), \
                 patch("time.sleep"):
                list(mcs.api_agent_plan_gen(config, {
                    "objective": "print hello",
                    "max_iterations": 1,
                    "quality_threshold": 99.0,
                    "model_base_url": "http://localhost:1234/v1",
                }))
        self.assertTrue(any("chart.jpg" in u for u in captured_critique_users),
                        f"artifact path not found in critique prompts: {captured_critique_users}")


if __name__ == "__main__":
    unittest.main()
