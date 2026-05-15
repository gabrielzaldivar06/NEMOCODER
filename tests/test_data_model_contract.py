import unittest

from nemo_coding_platform.core.headless_handoff import HandoffRequest
from nemo_coding_platform.core.headless_runner import execute_headless_handoff
from nemo_coding_platform.core.persistence import headless_result_to_dict
from nemo_coding_platform.core.task_run import NemoMemoryEvent, RunModelProfileLink, WorkflowRecipe


class DataModelContractTests(unittest.TestCase):
    def test_headless_result_exposes_first_class_workflow_recipe(self) -> None:
        result = execute_headless_handoff(
            HandoffRequest("Build", ".", ("ok",), ("python -m unittest",)),
            provider_mode="fake",
        )

        self.assertIsInstance(result.workflow_recipe, WorkflowRecipe)
        self.assertEqual(result.workflow_recipe.mode, "auto")
        self.assertGreaterEqual(len(result.workflow_recipe.step_kinds), 5)
        self.assertIn("generate_specs", result.workflow_recipe.step_kinds)

    def test_run_links_explicit_model_profile(self) -> None:
        result = execute_headless_handoff(
            HandoffRequest("Build", ".", ("ok",), ("python -m unittest",)),
            provider_mode="fake",
        )

        self.assertIsInstance(result.run.model_profile_link, RunModelProfileLink)
        self.assertIsNotNone(result.run.model_profile_link.model)  # auto-resolved, not hardcoded
        self.assertEqual(result.run.model_profile_link.provider_mode, "fake")

    def test_memory_traces_use_explicit_nemo_memory_event_schema(self) -> None:
        result = execute_headless_handoff(
            HandoffRequest("Build", ".", ("ok",), ("python -m unittest",)),
            provider_mode="fake",
        )

        self.assertTrue(result.memory_traces)
        self.assertTrue(all(isinstance(item, NemoMemoryEvent) for item in result.memory_traces))

    def test_persisted_payload_includes_workflow_recipe_and_model_profile_link(self) -> None:
        result = execute_headless_handoff(
            HandoffRequest("Build", ".", ("ok",), ("python -m unittest",)),
            provider_mode="fake",
        )
        payload = headless_result_to_dict(result)

        self.assertIn("workflow_recipe", payload)
        self.assertEqual(payload["workflow_recipe"]["mode"], "auto")
        self.assertEqual(payload["run"]["model_profile_link"]["provider_mode"], "fake")


if __name__ == "__main__":
    unittest.main()
