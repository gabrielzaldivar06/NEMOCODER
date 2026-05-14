import unittest

from nemo_coding_platform.core.model_config import DEFAULT_LMSTUDIO_BASE_URL, DEFAULT_LMSTUDIO_MODEL, default_model_profile, default_model_role_profile


class ModelConfigTests(unittest.TestCase):
    def test_default_lmstudio_model_is_empty_for_auto_discovery(self) -> None:
        profile = default_model_profile()

        self.assertEqual(profile.base_url, DEFAULT_LMSTUDIO_BASE_URL)
        self.assertEqual(profile.model, DEFAULT_LMSTUDIO_MODEL)
        self.assertEqual(profile.model, "")

    def test_model_role_profile_defaults_to_primary_model(self) -> None:
        roles = default_model_role_profile("role-base-model")

        self.assertEqual(roles.planner, "role-base-model")
        self.assertEqual(roles.editor, "role-base-model")
        self.assertEqual(roles.reviewer, "role-base-model")
        self.assertEqual(roles.summarizer, "role-base-model")

    def test_model_role_lookup_validates_supported_roles(self) -> None:
        roles = default_model_role_profile("role-base-model")

        self.assertEqual(roles.model_for_role("planner"), "role-base-model")
        self.assertEqual(roles.model_for_role("editor"), "role-base-model")
        self.assertEqual(roles.model_for_role("reviewer"), "role-base-model")
        self.assertEqual(roles.model_for_role("summarizer"), "role-base-model")
        with self.assertRaises(ValueError):
            roles.model_for_role("unknown")


if __name__ == "__main__":
    unittest.main()
