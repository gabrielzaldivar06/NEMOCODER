"""Tests for the Reflexion Loop module.

Covers:
- generate_reflexion produces correct outcome/field values for passing and failing runs
- persist_reflexion routes failures to create_correction and successes to cognitive_ingest
- Full cycle: failure run → correction in NEMO → retrievable via search_memories
- Tags, confidence levels, and correction text formatting
"""
from __future__ import annotations

import unittest

from nemo_coding_platform.core.headless_handoff import HandoffRequest
from nemo_coding_platform.core.headless_runner import execute_headless_handoff
from nemo_coding_platform.core.memory import MemoryAtomType
from nemo_coding_platform.core.nemo_adapter import InMemoryNemoAdapter
from nemo_coding_platform.core.reflexion import (
    ReflexionEntry,
    generate_reflexion,
    persist_reflexion,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_fake(request: HandoffRequest, extra_failing_commands: tuple[str, ...] = ()):
    """Execute a fake headless run and return the HeadlessRunResult."""
    return execute_headless_handoff(request, extra_failing_commands, provider_mode="fake")


def _passing_result():
    return _run_fake(HandoffRequest("Fix bug", ".", ("smoke",), ("python -m unittest",)))


def _failing_result():
    # Passing an extra failing command forces validation to fail
    return _run_fake(
        HandoffRequest("Add feature", ".", ("smoke",), ("python -m unittest",), repair_budget=1),
        ("python -m unittest",),
    )


# ---------------------------------------------------------------------------
# generate_reflexion — outcome detection
# ---------------------------------------------------------------------------

class TestGenerateReflexion(unittest.TestCase):

    def test_passing_run_produces_passed_outcome(self) -> None:
        result = _passing_result()
        entry = generate_reflexion(result, task_type="feature", objective="Fix bug")

        self.assertEqual(entry.outcome, "passed")
        self.assertEqual(entry.task_type, "feature")
        self.assertEqual(entry.objective, "Fix bug")

    def test_failing_run_produces_failed_outcome(self) -> None:
        result = _failing_result()
        entry = generate_reflexion(result, task_type="feature", objective="Add feature")

        self.assertEqual(entry.outcome, "failed")

    def test_passing_entry_has_high_confidence(self) -> None:
        result = _passing_result()
        entry = generate_reflexion(result, task_type="feature", objective="Fix bug")

        self.assertGreaterEqual(entry.confidence, 0.85)

    def test_failing_entry_confidence_decreases_with_attempts(self) -> None:
        result = _failing_result()
        entry = generate_reflexion(result, task_type="feature", objective="Add feature")

        # repair_budget=1 → at least 1 attempt → confidence < 0.85
        self.assertLess(entry.confidence, 0.85)
        self.assertGreaterEqual(entry.confidence, 0.5)

    def test_passing_entry_what_failed_is_none(self) -> None:
        result = _passing_result()
        entry = generate_reflexion(result, task_type="test_fix", objective="Fix tests")

        self.assertIn("none", entry.what_failed.lower())

    def test_failing_entry_records_failed_command(self) -> None:
        result = _failing_result()
        entry = generate_reflexion(result, task_type="feature", objective="Add feature")

        self.assertNotEqual(entry.what_failed, "none — validation passed")
        self.assertIn("validation failed", entry.what_failed.lower())

    def test_tags_include_outcome_and_task_type(self) -> None:
        result = _passing_result()
        entry = generate_reflexion(result, task_type="refactor", objective="Clean up")

        tags = entry.tags()
        self.assertIn("reflexion", tags)
        self.assertIn("refactor", tags)
        self.assertIn("passed", tags)

    def test_failing_tags_include_failed(self) -> None:
        result = _failing_result()
        entry = generate_reflexion(result, task_type="feature", objective="Add feature")

        self.assertIn("failed", entry.tags())

    def test_task_and_run_ids_captured(self) -> None:
        result = _run_fake(
            HandoffRequest("Fix bug", ".", ("smoke",), ("python -m unittest",)),
        )
        entry = generate_reflexion(result, task_type="bugfix", objective="Fix bug")

        self.assertEqual(entry.task_id, result.task.id)
        self.assertEqual(entry.run_id, result.run.id)

    def test_validation_summary_present(self) -> None:
        result = _passing_result()
        entry = generate_reflexion(result, task_type="feature", objective="Fix bug")

        self.assertIn("validation", entry.validation_summary.lower())


# ---------------------------------------------------------------------------
# ReflexionEntry formatting
# ---------------------------------------------------------------------------

class TestReflexionEntryFormatting(unittest.TestCase):

    def _make_failed_entry(self) -> ReflexionEntry:
        return generate_reflexion(
            _failing_result(), task_type="feature", objective="Add feature"
        )

    def _make_passed_entry(self) -> ReflexionEntry:
        return generate_reflexion(
            _passing_result(), task_type="refactor", objective="Refactor module"
        )

    def test_as_correction_text_contains_task_type(self) -> None:
        entry = self._make_failed_entry()
        text = entry.as_correction_text()

        self.assertIn("feature", text)
        self.assertIn("failed", text)

    def test_as_correction_text_contains_root_cause(self) -> None:
        entry = self._make_failed_entry()
        text = entry.as_correction_text()

        self.assertIn("root_cause", text)

    def test_as_procedural_text_contains_what_worked(self) -> None:
        entry = self._make_passed_entry()
        text = entry.as_procedural_text()

        self.assertIn("what_worked", text)
        self.assertIn("passed", text)

    def test_confidence_formatted_in_texts(self) -> None:
        entry = self._make_failed_entry()
        self.assertIn("confidence=", entry.as_correction_text())

        entry2 = self._make_passed_entry()
        self.assertIn("confidence=", entry2.as_procedural_text())


# ---------------------------------------------------------------------------
# persist_reflexion — NEMO routing
# ---------------------------------------------------------------------------

class TestPersistReflexion(unittest.TestCase):

    def test_failure_reflexion_calls_create_correction(self) -> None:
        result = _failing_result()
        entry = generate_reflexion(result, task_type="feature", objective="Add feature")
        adapter = InMemoryNemoAdapter()

        adapter, payload = persist_reflexion(adapter, entry)

        # InMemoryNemoAdapter records the tool calls
        tool_calls = [call.tool_name for call in adapter.calls]
        self.assertIn("create_correction", tool_calls)

    def test_success_reflexion_calls_cognitive_ingest(self) -> None:
        result = _passing_result()
        entry = generate_reflexion(result, task_type="refactor", objective="Refactor module")
        adapter = InMemoryNemoAdapter()

        adapter, payload = persist_reflexion(adapter, entry)

        tool_calls = [call.tool_name for call in adapter.calls]
        self.assertIn("cognitive_ingest", tool_calls)

    def test_failure_does_not_call_cognitive_ingest(self) -> None:
        result = _failing_result()
        entry = generate_reflexion(result, task_type="feature", objective="Add feature")
        adapter = InMemoryNemoAdapter()

        adapter, payload = persist_reflexion(adapter, entry)

        tool_calls = [call.tool_name for call in adapter.calls]
        self.assertNotIn("cognitive_ingest", tool_calls)

    def test_success_does_not_call_create_correction(self) -> None:
        result = _passing_result()
        entry = generate_reflexion(result, task_type="refactor", objective="Refactor module")
        adapter = InMemoryNemoAdapter()

        adapter, payload = persist_reflexion(adapter, entry)

        tool_calls = [call.tool_name for call in adapter.calls]
        self.assertNotIn("create_correction", tool_calls)

    def test_persist_returns_payload_dict(self) -> None:
        result = _failing_result()
        entry = generate_reflexion(result, task_type="feature", objective="Add feature")
        adapter = InMemoryNemoAdapter()

        _, payload = persist_reflexion(adapter, entry)

        self.assertIsInstance(payload, dict)

    def test_adapter_returned_is_same_type(self) -> None:
        adapter = InMemoryNemoAdapter()
        result = _passing_result()
        entry = generate_reflexion(result, task_type="feature", objective="Fix")

        returned_adapter, _ = persist_reflexion(adapter, entry)

        self.assertIsInstance(returned_adapter, InMemoryNemoAdapter)


# ---------------------------------------------------------------------------
# Full cycle: run → reflexion → correction content matches
# ---------------------------------------------------------------------------

class TestReflexionCycle(unittest.TestCase):

    def test_correction_content_includes_task_type(self) -> None:
        result = _failing_result()
        entry = generate_reflexion(result, task_type="ui_change", objective="Update UI")
        adapter = InMemoryNemoAdapter()

        adapter, _ = persist_reflexion(adapter, entry)

        # Find the create_correction call and check its arguments
        correction_calls = [
            call for call in adapter.calls if call.tool_name == "create_correction"
        ]
        self.assertEqual(len(correction_calls), 1)
        correct_answer = correction_calls[0].arguments.get("correct_answer", "")
        self.assertIn("ui_change", correct_answer)

    def test_correction_wrong_assumption_mentions_objective(self) -> None:
        result = _failing_result()
        entry = generate_reflexion(result, task_type="bugfix", objective="Fix the login bug")
        adapter = InMemoryNemoAdapter()

        adapter, _ = persist_reflexion(adapter, entry)

        correction_calls = [
            call for call in adapter.calls if call.tool_name == "create_correction"
        ]
        wrong_assumption = correction_calls[0].arguments.get("wrong_assumption", "")
        self.assertIn("Fix the login bug", wrong_assumption)

    def test_procedural_memory_content_includes_task_type(self) -> None:
        result = _passing_result()
        entry = generate_reflexion(result, task_type="test_fix", objective="Fix flaky tests")
        adapter = InMemoryNemoAdapter()

        adapter, _ = persist_reflexion(adapter, entry)

        ingest_calls = [
            call for call in adapter.calls if call.tool_name == "cognitive_ingest"
        ]
        self.assertEqual(len(ingest_calls), 1)
        content = ingest_calls[0].arguments.get("content", "")
        self.assertIn("test_fix", content)


if __name__ == "__main__":
    unittest.main()
