import tempfile
import unittest
from pathlib import Path

from nemo_coding_platform.core.memory import MemoryAtom, MemoryAtomType
from nemo_coding_platform.core.memory_persistence import PersistentMemoryStore


class PersistentMemoryStoreTests(unittest.TestCase):
    def test_store_and_retrieve_atoms_by_priority_and_topic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentMemoryStore(Path(tmp) / "memory.sqlite")
            low_id = store.create_atom(MemoryAtom(MemoryAtomType.PROJECT_FACT, "General project note", "project"), topic="other", tags=("project",), importance=3)
            correction_id = store.create_atom(MemoryAtom(MemoryAtomType.CORRECTION, "Always use Space Code as active base", "project"), topic="handoff", tags=("space-code", "handoff"), importance=10)
            decision_id = store.create_atom(MemoryAtom(MemoryAtomType.DECISION, "Long handoff continues as linked run", "project"), topic="handoff", tags=("handoff",), importance=7)

            atoms = store.search_atoms(topic="handoff", tags=("handoff",), limit=10)

        self.assertEqual([item.id for item in atoms], [correction_id, decision_id])
        self.assertNotIn(low_id, [item.id for item in atoms])
        self.assertEqual(atoms[0].atom.atom_type, MemoryAtomType.CORRECTION)
        self.assertEqual(atoms[0].tags, ("nemo-code", "handoff"))

    def test_prime_atoms_prefers_corrections_preferences_and_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentMemoryStore(Path(tmp) / "memory.sqlite")
            store.create_atom(MemoryAtom(MemoryAtomType.SESSION_SUMMARY, "Routine note", "session"), topic="handoff", importance=1)
            store.create_atom(MemoryAtom(MemoryAtomType.PREFERENCE, "Keep validation focused", "user"), topic="handoff", importance=8)
            store.create_atom(MemoryAtom(MemoryAtomType.CORRECTION, "Space Code is not just a reference", "project"), topic="handoff", importance=10)
            store.create_atom(MemoryAtom(MemoryAtomType.DECISION, "Resume creates a new linked run", "project"), topic="handoff", importance=7)

            atoms = store.prime_atoms(topic="handoff", limit=3)

        self.assertEqual([item.atom.atom_type for item in atoms], [MemoryAtomType.CORRECTION, MemoryAtomType.PREFERENCE, MemoryAtomType.DECISION])

    def test_evidence_handles_round_trip_and_count_retrievals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentMemoryStore(Path(tmp) / "memory.sqlite")
            handle = store.create_evidence("Large validation output", "Validation failed with NameError", source_task_id="task-1", source_run_id="run-1")

            evidence = store.expand_evidence(handle)
            evidence_again = store.expand_evidence(handle)

        self.assertEqual(evidence.original_content, "Large validation output")
        self.assertEqual(evidence.compact_claim, "Validation failed with NameError")
        self.assertEqual(evidence_again.retrieval_count, 2)

    def test_feedback_updates_atom_usefulness_counters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentMemoryStore(Path(tmp) / "memory.sqlite")
            atom_id = store.create_atom(MemoryAtom(MemoryAtomType.DECISION, "Use linked continuation runs", "project"), topic="handoff")

            store.record_feedback(atom_id=atom_id, event_type="portfolio_used", was_useful=True, token_delta=-42)
            store.record_feedback(atom_id=atom_id, event_type="portfolio_used", was_useful=False, token_delta=12)
            atom = store.get_atom(atom_id)
            stats = store.stats()

        self.assertEqual(atom.useful_count, 1)
        self.assertEqual(atom.not_useful_count, 1)
        self.assertEqual(stats["feedback_count"], 2)
        self.assertEqual(stats["atom_count"], 1)

    def test_missing_evidence_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentMemoryStore(Path(tmp) / "memory.sqlite")

            evidence = store.expand_evidence("missing")

        self.assertIsNone(evidence)


if __name__ == "__main__":
    unittest.main()
