import unittest

from nemo_coding_platform.core.contracts import ExecutionPhase, RuntimeState
from nemo_coding_platform.core.product import AutonomyLevel
from nemo_coding_platform.core.task_run import (
    AppendOnlyTimeline,
    Artifact,
    ArtifactType,
    EventKind,
    HandoffCompletionGuard,
    Run,
    RunEvent,
    Task,
)


class TaskRunModelTests(unittest.TestCase):
    def test_task_supports_full_handoff(self) -> None:
        task = Task("t1", ".", "Build", "Implement PRD", AutonomyLevel.FULL_HANDOFF)

        self.assertEqual(task.autonomy_level, AutonomyLevel.FULL_HANDOFF)

    def test_timeline_is_append_only(self) -> None:
        timeline = AppendOnlyTimeline()
        event = RunEvent("e1", "r1", 1, ExecutionPhase.PLAN, EventKind.PLAN_CREATED, "plan")

        timeline = timeline.append(event)

        self.assertTrue(timeline.has_event_kind(EventKind.PLAN_CREATED))
        with self.assertRaises(ValueError):
            timeline.append(event)

    def test_full_handoff_completion_requires_checkpoint_and_review_package(self) -> None:
        run = Run(
            "r1",
            "t1",
            RuntimeState.REVIEWING,
            ExecutionPhase.REVIEW,
            "rt1",
            ".runtime",
            "local",
            "full-handoff",
            "unit",
        )
        timeline = AppendOnlyTimeline().append(
            RunEvent("e1", "r1", 1, ExecutionPhase.REVIEW, EventKind.CHECKPOINT, "checkpoint")
        )
        artifacts = (
            Artifact.from_content(
                "a1", "r1", ArtifactType.REVIEW_PACKAGE, "review.md", "review", "ok"
            ),
        )

        HandoffCompletionGuard().validate(run, timeline, artifacts)


if __name__ == "__main__":
    unittest.main()