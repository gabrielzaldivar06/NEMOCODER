import unittest

from nemo_coding_platform.core.model_config import DEFAULT_LMSTUDIO_BASE_URL, DEFAULT_LMSTUDIO_MODEL, default_model_profile


class ModelConfigTests(unittest.TestCase):
    def test_default_lmstudio_model_is_small_coder(self) -> None:
        profile = default_model_profile()

        self.assertEqual(profile.base_url, DEFAULT_LMSTUDIO_BASE_URL)
        self.assertEqual(profile.model, DEFAULT_LMSTUDIO_MODEL)
        self.assertEqual(profile.model, "nvidia.agentic.coder-4b")


if __name__ == "__main__":
    unittest.main()
