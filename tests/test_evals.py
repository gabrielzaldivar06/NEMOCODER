import unittest

from nemo_coding_platform.core.evals import score_headless_result, score_spec10_lite
from nemo_coding_platform.core.headless_handoff import HandoffRequest
from nemo_coding_platform.core.headless_runner import execute_headless_handoff


def _execute_fake_handoff():
    return execute_headless_handoff(HandoffRequest("Build feature", ".", ("passes tests",), ("python -m unittest",)), provider_mode="fake")


class EvalsTests(unittest.TestCase):
    def test_readiness_score_marks_complete_handoff_ready(self) -> None:
        result = _execute_fake_handoff()

        score = score_headless_result(result)

        self.assertEqual(score.score, 1.0)
        self.assertTrue(score.validation_passed)
        self.assertTrue(score.has_checkpoint)
        self.assertTrue(score.has_review_package)
        self.assertTrue(score.memory_writeback_present)

    def test_readiness_score_handles_persisted_dict(self) -> None:
        result = _execute_fake_handoff()
        from nemo_coding_platform.core.persistence import headless_result_to_dict

        score = score_headless_result(headless_result_to_dict(result))

        self.assertEqual(score.grade, "ready")
        self.assertEqual(score.reasons, ())

    def test_persisted_result_without_changed_files_is_not_ready(self) -> None:
        result = _execute_fake_handoff()
        from nemo_coding_platform.core.persistence import headless_result_to_dict

        payload = headless_result_to_dict(result)
        payload["mutation_result"]["changed_files"] = []
        score = score_headless_result(payload)

        self.assertEqual(score.grade, "needs_review")
        self.assertIn("missing_mutation_result", score.reasons)

    def test_spec10_lite_includes_dimensions_and_checklist(self) -> None:
        result = _execute_fake_handoff()
        from nemo_coding_platform.core.persistence import headless_result_to_dict

        payload = headless_result_to_dict(result)
        payload["task"]["linked_prd"] = "Mission control PRD"
        payload["task"]["linked_specs"] = ["generated-spec.md"]

        spec10 = score_spec10_lite(payload)

        self.assertEqual(spec10["grade"], "ready")
        self.assertEqual(spec10["dimensions"]["traceability"], 1.0)
        self.assertEqual(spec10["checklist"], [])

    def test_spec10_lite_flags_traceability_missing(self) -> None:
        result = _execute_fake_handoff()
        from nemo_coding_platform.core.persistence import headless_result_to_dict

        payload = headless_result_to_dict(result)
        payload["task"]["linked_prd"] = None
        payload["task"]["linked_specs"] = []

        spec10 = score_spec10_lite(payload)

        self.assertLess(spec10["dimensions"]["traceability"], 1.0)
        self.assertIn("linked PRD is missing", spec10["checklist"])
        self.assertIn("linked specs are missing", spec10["checklist"])


if __name__ == "__main__":
    unittest.main()