import unittest

from nemo_coding_platform.core.headless_handoff import (
    HandoffRequest,
    HandoffStepKind,
    build_handoff_plan,
    validate_handoff_request,
)


class HeadlessHandoffTests(unittest.TestCase):
    def test_handoff_plan_covers_prd_to_code_lifecycle(self) -> None:
        request = HandoffRequest(
            prd="Build feature",
            repo_path=".",
            acceptance_criteria=("passes tests",),
            validation_commands=("python -m unittest",),
        )

        plan = build_handoff_plan(request)
        kinds = set(plan.step_kinds())

        self.assertTrue(plan.can_run_unattended)
        self.assertTrue(plan.review_gate_required)
        self.assertIn(HandoffStepKind.GENERATE_SPECS, kinds)
        self.assertIn(HandoffStepKind.GENERATE_TESTS, kinds)
        self.assertIn(HandoffStepKind.IMPLEMENT_WITH_AIDER, kinds)
        self.assertIn(HandoffStepKind.REPAIR_FAILURES, kinds)
        self.assertIn(HandoffStepKind.CREATE_CHECKPOINT, kinds)
        self.assertIn(HandoffStepKind.CREATE_REVIEW_PACKAGE, kinds)
        self.assertIn(HandoffStepKind.WRITE_NEMO_MEMORY, kinds)
        self.assertFalse(any(step.requires_human_prompt for step in plan.steps))

    def test_handoff_request_requires_prd_acceptance_and_validation(self) -> None:
        with self.assertRaises(ValueError):
            validate_handoff_request(HandoffRequest("", ".", (), ()))


if __name__ == "__main__":
    unittest.main()