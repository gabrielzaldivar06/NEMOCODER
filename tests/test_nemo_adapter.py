import unittest
import tempfile
from pathlib import Path

from nemo_coding_platform.core.memory import MemoryAtom, MemoryAtomType
from nemo_coding_platform.core.memory import NEMO_TOOL_REGISTRY
from nemo_coding_platform.core.memory_persistence import PersistentMemoryStore
from nemo_coding_platform.core.nemo_adapter import InMemoryNemoAdapter, PersistentNemoAdapter
from nemo_coding_platform.core.nemo_lifecycle import NemoLifecyclePhase, tool_allowed_in_lifecycle


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
            preference_id = store.create_atom(MemoryAtom(MemoryAtomType.PREFERENCE, "Keep validation focused", "user"), topic="handoff", tags=("handoff",), importance=9)
            decision_id = store.create_atom(MemoryAtom(MemoryAtomType.DECISION, "Use linked continuation runs", "project"), topic="handoff", tags=("handoff",), importance=7)
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
        self.assertEqual(result.payload["included_memory_atom_ids"], [preference_id, decision_id])
        self.assertEqual(result.payload["omitted_memory_atom_ids"], [])
        self.assertEqual(result.payload["included_entries"][0]["type"], "preference")
        self.assertEqual(result.payload["token_budget"], 600)

    def test_persistent_adapter_portfolio_reports_omitted_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentMemoryStore(Path(tmp) / "memory.sqlite")
            evidence_handle = store.create_evidence("Large detailed evidence", "Large evidence claim")
            omitted_id = store.create_atom(
                MemoryAtom(MemoryAtomType.DECISION, "This decision is intentionally long enough to exceed a tiny token budget.", "project", evidence_handle),
                topic="handoff",
                tags=("handoff",),
                importance=7,
            )
            _adapter, result = PersistentNemoAdapter(store).call(
                NemoLifecyclePhase.REVIEW,
                "build_context_portfolio",
                task="continue handoff",
                topic="handoff",
                tags_include=("handoff",),
                token_budget=1,
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.payload["included_memory_atom_ids"], [])
        self.assertEqual(result.payload["omitted_memory_atom_ids"], [omitted_id])
        self.assertEqual(result.payload["omitted_evidence_handles"], [evidence_handle])
        self.assertEqual(result.payload["omitted_entries"][0]["evidence_handle"], evidence_handle)

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

    def test_persistent_adapter_updates_existing_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentMemoryStore(Path(tmp) / "memory.sqlite")
            atom_id = store.create_atom(MemoryAtom(MemoryAtomType.DECISION, "Old decision", "project"), topic="architecture", tags=("old",), importance=3)
            _adapter, result = PersistentNemoAdapter(store).call(
                NemoLifecyclePhase.REVIEW,
                "update_memory",
                memory_id=atom_id,
                content="Updated decision",
                topic="handoff",
                tags=("new",),
                importance_level=9,
            )

            atom = store.get_atom(atom_id)

        self.assertTrue(result.ok)
        self.assertTrue(result.payload["updated"])
        self.assertEqual(atom.atom.content, "Updated decision")
        self.assertEqual(atom.topic, "handoff")
        self.assertEqual(atom.tags, ("new",))
        self.assertEqual(atom.importance, 9)

    def test_persistent_adapter_review_scheduling_tools_store_open_loops(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentMemoryStore(Path(tmp) / "memory.sqlite")
            adapter = PersistentNemoAdapter(store)
            adapter, reminder = adapter.call(NemoLifecyclePhase.REVIEW, "create_reminder", title="Follow up tests", due_datetime="2026-05-07T10:00:00Z")
            adapter, anchor = adapter.call(NemoLifecyclePhase.REVIEW, "intent_anchor", trigger_condition="before release", action="run smoke suite")
            _adapter, appointment = adapter.call(NemoLifecyclePhase.REVIEW, "create_appointment", title="Review checkpoint", scheduled_datetime="2026-05-08T15:00:00Z")

            reminder_atom = store.get_atom(reminder.payload["atom_id"])
            anchor_atom = store.get_atom(anchor.payload["atom_id"])
            appointment_atom = store.get_atom(appointment.payload["atom_id"])

        self.assertEqual(reminder.payload["type"], "reminder")
        self.assertEqual(anchor.payload["type"], "intent_anchor")
        self.assertEqual(appointment.payload["type"], "appointment")
        self.assertEqual(reminder_atom.atom.atom_type, MemoryAtomType.OPEN_LOOP)
        self.assertIn("reminder", reminder_atom.tags)
        self.assertIn("active", reminder_atom.tags)
        self.assertIn("intent_anchor", anchor_atom.tags)
        self.assertIn("appointment", appointment_atom.tags)
        self.assertIn("upcoming", appointment_atom.tags)

    def test_persistent_adapter_manages_reminder_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentMemoryStore(Path(tmp) / "memory.sqlite")
            adapter = PersistentNemoAdapter(store)
            adapter, created = adapter.call(NemoLifecyclePhase.REVIEW, "create_reminder", title="Follow up docs", topic="reminders")
            reminder_id = created.payload["atom_id"]
            adapter, active = adapter.call(NemoLifecyclePhase.PLAN, "get_active_reminders", topic="reminders")
            adapter, completed = adapter.call(NemoLifecyclePhase.REVIEW, "complete_reminder", reminder_id=reminder_id)
            _adapter, completed_list = adapter.call(NemoLifecyclePhase.REVIEW, "get_completed_reminders", topic="reminders")

        self.assertEqual(len(active.payload["reminders"]), 1)
        self.assertEqual(completed.payload["status"], "completed")
        self.assertEqual(completed_list.payload["reminders"][0]["id"], reminder_id)

    def test_persistent_adapter_manages_appointment_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentMemoryStore(Path(tmp) / "memory.sqlite")
            adapter = PersistentNemoAdapter(store)
            adapter, created = adapter.call(NemoLifecyclePhase.REVIEW, "create_appointment", title="Planning", scheduled_datetime="2026-05-08T10:00:00Z", topic="appointments")
            appointment_id = created.payload["atom_id"]
            adapter, upcoming = adapter.call(NemoLifecyclePhase.PLAN, "get_upcoming_appointments", topic="appointments")
            adapter, completed = adapter.call(NemoLifecyclePhase.REVIEW, "complete_appointment", appointment_id=appointment_id)
            _adapter, recent = adapter.call(NemoLifecyclePhase.REVIEW, "get_recent_appointments", topic="appointments")

        self.assertEqual(len(upcoming.payload["appointments"]), 1)
        self.assertEqual(completed.payload["status"], "completed")
        self.assertEqual(recent.payload["appointments"][0]["id"], appointment_id)

    def test_persistent_adapter_advanced_memory_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentMemoryStore(Path(tmp) / "memory.sqlite")
            adapter = PersistentNemoAdapter(store)
            adapter, ingested = adapter.call(NemoLifecyclePhase.PLAN, "cognitive_ingest", content="Use NEMO portfolios for handoff context", topic="handoff", atom_type="decision")
            adapter, anticipate = adapter.call(NemoLifecyclePhase.PLAN, "anticipate", task="handoff context", topic="handoff")
            adapter, salience = adapter.call(NemoLifecyclePhase.PLAN, "salience_score", content="handoff context portfolio", task="handoff context")
            store.create_atom(MemoryAtom(MemoryAtomType.DECISION, "duplicate memory", "test"), topic="handoff")
            store.create_atom(MemoryAtom(MemoryAtomType.DECISION, "duplicate memory", "test"), topic="handoff")
            adapter, redundancy = adapter.call(NemoLifecyclePhase.REVIEW, "detect_redundancy", topic="handoff")
            _adapter, chronicle = adapter.call(NemoLifecyclePhase.REVIEW, "memory_chronicle", topic="handoff")

        self.assertTrue(ingested.payload["stored"])
        self.assertGreaterEqual(len(anticipate.payload["memories"]), 1)
        self.assertGreater(salience.payload["score"], 0)
        self.assertEqual(redundancy.payload["duplicate_count"], 1)
        self.assertGreaterEqual(len(chronicle.payload["memories"]), 3)

    def test_persistent_adapter_compares_and_refreshes_portfolios(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentMemoryStore(Path(tmp) / "memory.sqlite")
            store.create_atom(MemoryAtom(MemoryAtomType.DECISION, "Use structured context portfolios", "project"), topic="handoff")
            adapter = PersistentNemoAdapter(store)
            adapter, compare = adapter.call(NemoLifecyclePhase.PLAN, "compare_context_strategies", task="handoff", topic="handoff")
            _adapter, refresh = adapter.call(NemoLifecyclePhase.PLAN, "refresh_context_portfolio", task="handoff", topic="handoff")

        self.assertIn("raw", compare.payload["strategies"])
        self.assertIn("portfolio", compare.payload["strategies"])
        self.assertTrue(refresh.payload["refreshed"])

    def test_persistent_adapter_weather_tool_has_deterministic_offline_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentMemoryStore(Path(tmp) / "memory.sqlite")
            _adapter, result = PersistentNemoAdapter(store).call(NemoLifecyclePhase.PLAN, "get_weather_open_meteo", offline=True)

        self.assertTrue(result.ok)
        self.assertEqual(result.payload["status"], "offline")
        self.assertEqual(result.payload["source"], "open-meteo")

    def test_persistent_adapter_returns_recent_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentMemoryStore(Path(tmp) / "memory.sqlite")
            adapter = PersistentNemoAdapter(store)
            adapter, write_result = adapter.call(NemoLifecyclePhase.BUILD, "store_conversation", summary="Run checkpoint ready", topic="handoff")
            _adapter, recent_result = adapter.call(NemoLifecyclePhase.REVIEW, "get_recent_context", topic="handoff")

        self.assertTrue(write_result.ok)
        self.assertTrue(recent_result.ok)
        self.assertIn("Run checkpoint ready", recent_result.payload["context"])
        self.assertEqual(recent_result.payload["messages"][0]["type"], "session_summary")

    def test_persistent_adapter_reports_time_environment_and_health(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentMemoryStore(Path(tmp) / "memory.sqlite")
            adapter = PersistentNemoAdapter(store)
            adapter, time_result = adapter.call(NemoLifecyclePhase.PLAN, "get_current_time")
            adapter, env_result = adapter.call(NemoLifecyclePhase.PLAN, "get_environment_info")
            _adapter, health_result = adapter.call(NemoLifecyclePhase.REVIEW, "get_system_health")

        self.assertTrue(time_result.payload["utc"].endswith("+00:00"))
        self.assertIn("python_version", env_result.payload)
        self.assertTrue(env_result.payload["memory_db_exists"])
        self.assertEqual(health_result.payload["status"], "ok")
        self.assertIn("atom_count", health_result.payload)

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

    def test_all_registry_tools_have_persistent_operational_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentMemoryStore(Path(tmp) / "memory.sqlite")
            evidence_handle = store.create_evidence("Full evidence", "Evidence claim")
            atom_id = store.create_atom(MemoryAtom(MemoryAtomType.DECISION, "Seed decision", "test", evidence_handle), topic="general", tags=("registry",), importance=7)
            adapter = PersistentNemoAdapter(store)

            for tool in NEMO_TOOL_REGISTRY:
                phase = _allowed_lifecycle_for_tool(tool.name)
                adapter, result = adapter.call(phase, tool.name, **_arguments_for_tool(tool.name, atom_id, evidence_handle))
                self.assertTrue(result.ok, tool.name)
                self.assertNotEqual(result.payload.get("tool"), tool.name, tool.name)
                self.assertNotIn("accepted", result.payload, tool.name)


def _allowed_lifecycle_for_tool(tool_name: str) -> NemoLifecyclePhase:
    for phase in (NemoLifecyclePhase.START, NemoLifecyclePhase.PLAN, NemoLifecyclePhase.BUILD, NemoLifecyclePhase.REVIEW, NemoLifecyclePhase.CLOSE):
        if tool_allowed_in_lifecycle(phase, tool_name):
            return phase
    raise AssertionError(f"no lifecycle phase allows {tool_name}")


def _arguments_for_tool(tool_name: str, atom_id: str, evidence_handle: str) -> dict[str, object]:
    common: dict[str, object] = {"topic": "general", "limit": 5}
    return {
        "prime_context": common,
        "context_bootstrap": {"task": "registry smoke", "topic": "general", "token_budget": 100},
        "build_context_portfolio": {"task": "registry smoke", "topic": "general", "token_budget": 100},
        "compare_context_strategies": {"task": "registry smoke", "topic": "general", "token_budget": 100},
        "refresh_context_portfolio": {"task": "registry smoke", "topic": "general", "token_budget": 100},
        "expand_context_evidence": {"handle": evidence_handle},
        "record_context_feedback": {"atom_id": atom_id, "event_type": "registry_smoke", "was_useful": True},
        "get_context_portfolio_stats": {},
        "compress_context_artifact": {"content": "Large content", "title": "Large content claim"},
        "create_correction": {"correct_answer": "Correct answer", "topic": "general"},
        "cognitive_ingest": {"content": "Registry ingested memory", "topic": "general"},
        "anticipate": {"task": "Registry", "topic": "general"},
        "detect_redundancy": {"topic": "general"},
        "memory_chronicle": {"topic": "general"},
        "salience_score": {"content": "Registry memory", "task": "Registry"},
        "update_memory": {"memory_id": atom_id, "content": "Updated by registry smoke"},
        "store_conversation": {"summary": "Registry smoke summary", "topic": "general"},
        "get_recent_context": {"topic": "general"},
        "get_current_time": {},
        "get_environment_info": {},
        "get_weather_open_meteo": {"offline": True},
        "create_reminder": {"title": "Registry reminder", "due_datetime": "2026-05-07T10:00:00Z"},
        "get_active_reminders": {"topic": "general"},
        "get_completed_reminders": {"topic": "general"},
        "complete_reminder": {"reminder_id": atom_id},
        "delete_reminder": {"reminder_id": "missing-reminder"},
        "reschedule_reminder": {"reminder_id": atom_id, "due_datetime": "2026-05-09T10:00:00Z"},
        "intent_anchor": {"trigger_condition": "registry trigger", "action": "registry action"},
        "create_appointment": {"title": "Registry appointment", "scheduled_datetime": "2026-05-08T10:00:00Z"},
        "get_recent_appointments": {"topic": "general"},
        "get_upcoming_appointments": {"topic": "general"},
        "cancel_appointment": {"appointment_id": atom_id},
        "complete_appointment": {"appointment_id": atom_id},
        "store_architectural_decision": {"decision": "Registry decision", "topic": "general"},
        "search_memories": {"query": "Registry", "topic": "general"},
        "get_system_health": {},
    }[tool_name]


if __name__ == "__main__":
    unittest.main()