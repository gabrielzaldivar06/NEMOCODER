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
            with patch("urllib.request.urlopen", return_value=_mock_urlopen(b"\xff\xd8\xff")):
                result = mcs._pollinations_image({"prompt": "a sunset"}, config)
            self.assertIn("artifact_path", result)
            self.assertEqual(result["model_used"], "flux")
            self.assertTrue(Path(result["artifact_path"]).exists())

    def test_custom_model_and_dimensions(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(tmp)
            with patch("urllib.request.urlopen", return_value=_mock_urlopen(b"\xff\xd8\xff")) as mock_open:
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
        self.assertEqual(result["model_used"], "openai")

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

    def test_unknown_tool_returns_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(tmp)
            result = mcs._execute_pollinations_tool({"tool": "unknown_tool", "params": {}}, config)
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
