import io
import os
import unittest
import urllib.error
from unittest.mock import patch

from nemo_coding_platform.mission_control_server import ApiRequestError, _lmstudio_chat_completion, _validate_provider_timeout


class NonFunctionalRequirementsContractTests(unittest.TestCase):
    def test_provider_timeout_slos_are_enforced(self) -> None:
        _validate_provider_timeout("subprocess", 5.0, error_code="invalid")
        _validate_provider_timeout("subprocess", 1800.0, error_code="invalid")

        with self.assertRaises(ApiRequestError):
            _validate_provider_timeout("subprocess", 4.0, error_code="invalid")
        with self.assertRaises(ApiRequestError):
            _validate_provider_timeout("subprocess", 1801.0, error_code="invalid")

    def test_lmstudio_errors_never_leak_api_key(self) -> None:
        api_key = "sk-local-super-secret"
        payload = {
            "model": "nvidia.agentic.coder-4b",
            "model_base_url": "http://localhost:1234/v1",
            "provider": "subprocess",
            "timeout_seconds": 30,
        }
        error = urllib.error.HTTPError(
            url="http://localhost:1234/v1/chat/completions",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=io.BytesIO(f"invalid token: {api_key}".encode("utf-8")),
        )

        with patch.dict(os.environ, {"LMSTUDIO_API_KEY": api_key}, clear=False):
            with patch("urllib.request.urlopen", side_effect=error):
                with self.assertRaises(ValueError) as raised:
                    _lmstudio_chat_completion(payload, "hola", "context")

        message = str(raised.exception)
        self.assertIn("LM Studio chat failed", message)
        self.assertNotIn(api_key, message)
        self.assertIn("***", message)


if __name__ == "__main__":
    unittest.main()
