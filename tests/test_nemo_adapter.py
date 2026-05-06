import unittest
import tempfile
from pathlib import Path

from nemo_coding_platform.core.memory import MemoryAtom, MemoryAtomType
from nemo_coding_platform.core.memory_persistence import PersistentMemoryStore
from nemo_coding_platform.core.nemo_adapter import InMemoryNemoAdapter, PersistentNemoAdapter
from nemo_coding_platform.core.nemo_lifecycle import NemoLifecyclePhase


class NemoAdapterTests(unittest.TestCase):
    def test_adapter_records_allowed_call(self) -> None:
        adapter, result = InMemoryNemoAdapter().call(NemoLifecyclePhase.START, "prime_context", topic="demo")

        self.assertTrue(result.ok)
        self.assertEqual(result.payload["topic"], "demo")
        self.assertEqual(adapter.calls[0].tool_name, "prime_context")

    def test_adapter_rejects_build_scheduling(self) -> None:
        with self.assertRaises(PermissionError):
            InMemoryNemoAdapter().call(NemoLifecyclePhase.BUILD, "create_reminder", text="later")

    def test_adapter_builds_context_portfolio_payload(self) -> None:
        _adapter, result = InMemoryNemoAdapter().call(NemoLifecyclePhase.BUILD, "build_context_portfolio", task="Build", topic="Aider")

        self.assertTrue(result.ok)
        self.assertIn("context", result.payload)
        self.assertIn("estimated_tokens", result.payload)

    def test_persistent_adapter_primes_context_from_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentMemoryStore(Path(tmp) / "memory.sqlite")
            store.create_atom(MemoryAtom(MemoryAtomType.CORRECTION, "Aider is the active base", "project"), topic="handoff", importance=10)
            adapter, result = PersistentNemoAdapter(store).call(NemoLifecyclePhase.START, "prime_context", topic="handoff")

        self.assertTrue(result.ok)
        self.assertIn("Aider is the active base", result.payload["context"])
        self.assertEqual(result.payload["memories"][0]["type"], "correction")
        self.assertEqual(adapter.calls[0].tool_name, "prime_context")

    def test_persistent_adapter_builds_portfolio_from_stored_atoms(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentMemoryStore(Path(tmp) / "memory.sqlite")
            store.create_atom(MemoryAtom(MemoryAtomType.PREFERENCE, "Keep validation focused", "user"), topic="handoff", tags=("handoff",), importance=9)
            store.create_atom(MemoryAtom(MemoryAtomType.DECISION, "Use linked continuation runs", "project"), topic="handoff", tags=("handoff",), importance=7)
            _adapter, result = PersistentNemoAdapter(store).call(
                NemoLifecyclePhase.REVIEW,
                "build_context_portfolio",
                task="continue handoff",
                topic="handoff",
                tags_include=("handoff",),
            )

        self.assertTrue(result.ok)
        self.assertIn("Keep validation focused", result.payload["context"])
        self.assertIn("Use linked continuation runs", result.payload["context"])
        self.assertEqual(result.payload["source"], "persistent_store")

    def test_persistent_adapter_writes_conversation_and_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentMemoryStore(Path(tmp) / "memory.sqlite")
            adapter = PersistentNemoAdapter(store)
            adapter, write_result = adapter.call(NemoLifecyclePhase.BUILD, "store_conversation", summary="Checkpoint ready", topic="handoff")
            atom_id = write_result.payload["atom_id"]
            _adapter, feedback_result = adapter.call(NemoLifecyclePhase.BUILD, "record_context_feedback", atom_id=atom_id, event_type="used", was_useful=True)

            atom = store.get_atom(atom_id)

        self.assertTrue(write_result.ok)
        self.assertTrue(feedback_result.ok)
        self.assertEqual(atom.atom.atom_type, MemoryAtomType.SESSION_SUMMARY)
        self.assertEqual(atom.useful_count, 1)

    def test_persistent_adapter_expands_evidence_from_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentMemoryStore(Path(tmp) / "memory.sqlite")
            handle = store.create_evidence("Full validation output", "Validation failed", source_run_id="run-1")
            _adapter, result = PersistentNemoAdapter(store).call(NemoLifecyclePhase.BUILD, "expand_context_evidence", handle=handle)

        self.assertTrue(result.ok)
        self.assertEqual(result.payload["handle"], handle)
        self.assertEqual(result.payload["content"], "Full validation output")

    def test_persistent_adapter_preserves_lifecycle_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentMemoryStore(Path(tmp) / "memory.sqlite")

            with self.assertRaises(PermissionError):
                PersistentNemoAdapter(store).call(NemoLifecyclePhase.BUILD, "create_reminder", text="later")


if __name__ == "__main__":
    unittest.main()