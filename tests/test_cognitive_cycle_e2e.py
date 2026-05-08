"""End-to-end tests for the cognitive self-improvement cycle.

Verifies that the full loop works as designed:

  1. A failing task persists a ``create_correction`` to a real NEMO DB
     (via headless_runner Phase 1 + reflexion Phase 2b).
  2. A second task on the same DB calls ``anticipate`` and retrieves
     the correction in its context (Mission Control Phase 3 path).
  3. ``query_self_mod_risk_patterns`` surfaces the correction in the
     risk-map endpoint.

All tests use a real SQLite DB in a temp directory — no mocks.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nemo_coding_platform.core.headless_handoff import HandoffRequest
from nemo_coding_platform.core.headless_runner import execute_headless_handoff
from nemo_coding_platform.core.memory import MemoryAtomType
from nemo_coding_platform.core.memory_persistence import PersistentMemoryStore
from nemo_coding_platform.core.nemo_adapter import PersistentNemoAdapter, NemoLifecyclePhase
from nemo_coding_platform.core.reflexion import generate_reflexion, persist_reflexion
from nemo_coding_platform.core.self_modification import query_self_mod_risk_patterns


def _run_fake(request: HandoffRequest, extra_failing: tuple[str, ...] = (), **kwargs):
    return execute_headless_handoff(request, extra_failing, provider_mode="fake", **kwargs)


def _failing_request(title: str = "Add login feature") -> HandoffRequest:
    return HandoffRequest(title, ".", ("smoke",), ("python -m unittest",), repair_budget=1)


def _passing_request(title: str = "Fix typo in README") -> HandoffRequest:
    return HandoffRequest(title, ".", ("smoke",), ("python -m unittest",))


# ---------------------------------------------------------------------------
# Phase 1 integration: headless repair failure → NEMO correction
# ---------------------------------------------------------------------------

class TestRepairFailurePersistsCorrection(unittest.TestCase):
    """Verify that headless_runner writes a CORRECTION atom on repair exhaustion."""

    def test_failing_run_creates_correction_atom_in_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentMemoryStore(Path(tmp) / "nemo.sqlite")
            adapter = PersistentNemoAdapter(store)

            result = _run_fake(
                _failing_request("Add login feature"),
                ("python -m unittest",),
                nemo_adapter=adapter,
                task_id="e2e-fail-task",
                run_id="e2e-fail-run",
            )

            corrections = store.search_atoms(
                atom_types=(MemoryAtomType.CORRECTION,), limit=20
            )

        self.assertFalse(result.validation.passed)
        self.assertTrue(
            len(corrections) >= 1,
            "Expected at least one CORRECTION atom after a repair failure"
        )

    def test_correction_content_references_task_title(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentMemoryStore(Path(tmp) / "nemo.sqlite")
            adapter = PersistentNemoAdapter(store)

            _run_fake(
                _failing_request("Fix the payment gateway"),
                ("python -m unittest",),
                nemo_adapter=adapter,
                task_id="e2e-payment-task",
                run_id="e2e-payment-run",
            )

            # Content stored as "repair exhausted for task 'Fix the payment gateway'; ..."
            corrections = store.search_atoms(
                atom_types=(MemoryAtomType.CORRECTION,),
                query="Fix the payment gateway",
                limit=20,
            )

        self.assertGreaterEqual(len(corrections), 1)
        all_content = " ".join(atom.atom.content for atom in corrections)
        self.assertIn("Fix the payment gateway", all_content)

    def test_passing_run_does_not_create_correction_atom(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentMemoryStore(Path(tmp) / "nemo.sqlite")
            adapter = PersistentNemoAdapter(store)

            result = _run_fake(
                _passing_request("Fix typo"),
                nemo_adapter=adapter,
                task_id="e2e-pass-task",
                run_id="e2e-pass-run",
            )

            corrections = store.search_atoms(
                atom_types=(MemoryAtomType.CORRECTION,), limit=20
            )

        self.assertTrue(result.validation.passed)
        self.assertEqual(
            len(corrections), 0,
            "Passing run must not produce CORRECTION atoms"
        )


# ---------------------------------------------------------------------------
# Phase 2 integration: reflexion → correction in same DB
# ---------------------------------------------------------------------------

class TestReflexionPersistenceInRealDB(unittest.TestCase):
    """Verify persist_reflexion writes to a real SQLite store."""

    def test_failure_reflexion_produces_correction_in_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentMemoryStore(Path(tmp) / "nemo.sqlite")
            adapter = PersistentNemoAdapter(store)

            result = _run_fake(_failing_request("Refactor auth module"), ("python -m unittest",))
            entry = generate_reflexion(result, task_type="refactor", objective="Refactor auth module")
            adapter, _ = persist_reflexion(adapter, entry)

            corrections = store.search_atoms(
                atom_types=(MemoryAtomType.CORRECTION,), limit=20
            )

        self.assertTrue(len(corrections) >= 1)
        content = corrections[0].atom.content
        self.assertIn("refactor", content)

    def test_success_reflexion_produces_no_correction_in_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentMemoryStore(Path(tmp) / "nemo.sqlite")
            adapter = PersistentNemoAdapter(store)

            result = _run_fake(_passing_request("Clean up imports"))
            entry = generate_reflexion(result, task_type="refactor", objective="Clean up imports")
            adapter, _ = persist_reflexion(adapter, entry)

            corrections = store.search_atoms(
                atom_types=(MemoryAtomType.CORRECTION,), limit=20
            )

        self.assertEqual(len(corrections), 0)


# ---------------------------------------------------------------------------
# Phase 3 integration: anticipate retrieves corrections from prior run
# ---------------------------------------------------------------------------

class TestAnticipateretrievesCorrections(unittest.TestCase):
    """Verify that `anticipate` surfaces past corrections for a similar task."""

    def test_anticipate_returns_correction_from_prior_failing_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentMemoryStore(Path(tmp) / "nemo.sqlite")

            # Run 1: failing task — writes correction to DB
            adapter1 = PersistentNemoAdapter(store)
            result1 = _run_fake(
                _failing_request("Add OAuth login"),
                ("python -m unittest",),
                nemo_adapter=adapter1,
                task_id="e2e-oauth-fail",
                run_id="e2e-oauth-run-1",
            )
            self.assertFalse(result1.validation.passed)

            # Run 2: similar task on same DB — call anticipate
            adapter2 = PersistentNemoAdapter(store)
            adapter2, anticipate_result = adapter2.call(
                NemoLifecyclePhase.PLAN,
                "anticipate",
                # Exact task title → matches "repair exhausted for task 'Add OAuth login'; ..."
                task="Add OAuth login",
                limit=10,
            )
            memories = anticipate_result.payload.get("memories", [])

        # At least one returned memory must be a correction referencing the prior failure
        correction_memories = [
            m for m in memories
            if m.get("type") == MemoryAtomType.CORRECTION.value
        ]
        self.assertTrue(
            len(correction_memories) >= 1,
            f"Expected anticipate to return at least one correction; got: {[m.get('type') for m in memories]}"
        )

    def test_anticipate_content_matches_prior_failure_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentMemoryStore(Path(tmp) / "nemo.sqlite")

            # Failing run: stores correction with task title in content
            adapter1 = PersistentNemoAdapter(store)
            _run_fake(
                _failing_request("Implement rate limiter"),
                ("python -m unittest",),
                nemo_adapter=adapter1,
                task_id="e2e-ratelimit-fail",
                run_id="e2e-ratelimit-run-1",
            )

            # Anticipate on new adapter with same DB
            adapter2 = PersistentNemoAdapter(store)
            adapter2, anticipate_result = adapter2.call(
                NemoLifecyclePhase.PLAN,
                "anticipate",
                # Exact task title → matches stored correction content
                task="Implement rate limiter",
                limit=10,
            )
            memories = anticipate_result.payload.get("memories", [])

        all_content = " ".join(m.get("content", "") for m in memories)
        self.assertIn("Implement rate limiter", all_content)

    def test_anticipate_returns_empty_on_fresh_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentMemoryStore(Path(tmp) / "nemo.sqlite")
            adapter = PersistentNemoAdapter(store)

            adapter, anticipate_result = adapter.call(
                NemoLifecyclePhase.PLAN,
                "anticipate",
                task="Some brand new task never done before",
                limit=10,
            )
            memories = anticipate_result.payload.get("memories", [])

        self.assertEqual(memories, [])


# ---------------------------------------------------------------------------
# Risk map: query_self_mod_risk_patterns surfaces corrections
# ---------------------------------------------------------------------------

class TestRiskMapSurfacesCorrections(unittest.TestCase):
    """Verify that the risk-map query finds CORRECTION atoms after failures."""

    def test_risk_map_finds_correction_after_failing_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "nemo.sqlite"
            store = PersistentMemoryStore(db_path)
            adapter = PersistentNemoAdapter(store)

            _run_fake(
                _failing_request("Deploy new billing module"),
                ("python -m unittest",),
                nemo_adapter=adapter,
                task_id="e2e-billing-fail",
                run_id="e2e-billing-run-1",
            )

            risk_result = query_self_mod_risk_patterns(db_path, limit=20)

        # The correction atom written by headless_runner is tagged "self-mod-risk"
        self.assertGreaterEqual(risk_result["count"], 1)
        all_content = " ".join(p["content"] for p in risk_result["patterns"])
        self.assertIn("Deploy new billing module", all_content)

    def test_risk_map_empty_on_fresh_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "nemo.sqlite"

            risk_result = query_self_mod_risk_patterns(db_path, limit=20)

        self.assertEqual(risk_result["count"], 0)
        self.assertEqual(risk_result["patterns"], [])


# ---------------------------------------------------------------------------
# Full cycle: fail → correction → anticipate → risk map (single DB)
# ---------------------------------------------------------------------------

class TestFullCognitiveCycle(unittest.TestCase):
    """Integration test for the complete cognitive cycle in one shared DB."""

    def test_full_cycle_fail_anticipate_riskmap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "nemo.sqlite"

            # Step 1 — failing run writes correction + intent anchor
            store1 = PersistentMemoryStore(db_path)
            adapter1 = PersistentNemoAdapter(store1)
            fail_result = _run_fake(
                HandoffRequest(
                    "Migrate database schema",
                    ".",
                    ("smoke",),
                    ("python -m unittest",),
                    repair_budget=1,
                ),
                ("python -m unittest",),
                nemo_adapter=adapter1,
                task_id="e2e-full-task",
                run_id="e2e-full-run-1",
            )

            # Step 2 — anticipate on second adapter (same DB)
            store2 = PersistentMemoryStore(db_path)
            adapter2 = PersistentNemoAdapter(store2)
            adapter2, anticipate_result = adapter2.call(
                NemoLifecyclePhase.PLAN,
                "anticipate",
                # Exact task title → matches stored correction content
                task="Migrate database schema",
                limit=10,
            )
            memories = anticipate_result.payload.get("memories", [])

            # Step 3 — risk map query
            risk_result = query_self_mod_risk_patterns(db_path, limit=20)

        # Assertions
        self.assertFalse(fail_result.validation.passed, "Run 1 should fail")

        self.assertTrue(
            len(memories) >= 1,
            "anticipate must return at least one memory from the prior failure"
        )

        self.assertGreaterEqual(
            risk_result["count"], 1,
            "risk map must expose at least one pattern after the failure"
        )

        # Both anticipate and risk map should reference the original task
        anticipate_content = " ".join(m.get("content", "") for m in memories)
        risk_content = " ".join(p["content"] for p in risk_result["patterns"])
        self.assertIn("Migrate database schema", anticipate_content)
        self.assertIn("Migrate database schema", risk_content)


if __name__ == "__main__":
    unittest.main()
