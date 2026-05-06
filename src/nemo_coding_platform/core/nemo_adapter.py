from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from nemo_coding_platform.core.context_portfolio import build_context_portfolio, make_portfolio_request
from nemo_coding_platform.core.contracts import ExecutionPhase
from nemo_coding_platform.core.memory import MemoryAtom, MemoryAtomType, PHASE_PORTFOLIOS
from nemo_coding_platform.core.memory_persistence import PersistentMemoryStore, StoredMemoryAtom
from nemo_coding_platform.core.nemo_lifecycle import NemoLifecyclePhase, tool_allowed_in_lifecycle


@dataclass(frozen=True, slots=True)
class NemoCall:
    phase: NemoLifecyclePhase
    tool_name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class NemoCallResult:
    call: NemoCall
    ok: bool
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class InMemoryNemoAdapter:
    calls: tuple[NemoCall, ...] = ()

    def call(self, phase: NemoLifecyclePhase, tool_name: str, **arguments: Any) -> tuple[InMemoryNemoAdapter, NemoCallResult]:
        call = NemoCall(phase, tool_name, dict(arguments))
        if not tool_allowed_in_lifecycle(phase, tool_name):
            raise PermissionError(f"NEMO tool {tool_name} is not allowed in lifecycle phase {phase}")
        payload = self._payload_for(tool_name, arguments)
        return replace(self, calls=self.calls + (call,)), NemoCallResult(call, True, payload)

    def _payload_for(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_name in {"prime_context", "context_bootstrap"}:
            return {"context": "compact", "topic": arguments.get("topic")}
        if tool_name == "build_context_portfolio":
            phase = ExecutionPhase(arguments.get("portfolio_phase", arguments.get("execution_phase", ExecutionPhase.PLAN.value)))
            request = make_portfolio_request(
                str(arguments.get("task", arguments.get("topic", "task"))),
                str(arguments.get("topic", "general")),
                phase,
                int(arguments.get("token_budget", 600)),
            )
            return build_context_portfolio(request).to_payload()
        if tool_name == "expand_context_evidence":
            return {"handle": arguments.get("handle"), "content": "in-memory evidence expansion unavailable in MVP"}
        if tool_name == "store_conversation":
            return {"stored": True, "summary": arguments.get("summary", "")}
        return {"tool": tool_name, "accepted": True}


@dataclass(frozen=True, slots=True)
class PersistentNemoAdapter:
    store: PersistentMemoryStore
    calls: tuple[NemoCall, ...] = ()

    def call(self, phase: NemoLifecyclePhase, tool_name: str, **arguments: Any) -> tuple[PersistentNemoAdapter, NemoCallResult]:
        call = NemoCall(phase, tool_name, dict(arguments))
        if not tool_allowed_in_lifecycle(phase, tool_name):
            raise PermissionError(f"NEMO tool {tool_name} is not allowed in lifecycle phase {phase}")
        payload = self._payload_for(phase, tool_name, arguments)
        return replace(self, calls=self.calls + (call,)), NemoCallResult(call, True, payload)

    def _payload_for(self, phase: NemoLifecyclePhase, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_name == "prime_context":
            topic = _optional_string(arguments.get("topic"))
            atoms = self.store.prime_atoms(topic=topic, limit=int(arguments.get("limit", 10)))
            return {"context": _context_from_atoms(atoms), "topic": topic, "memories": [_atom_payload(atom) for atom in atoms], "source": "persistent_store"}
        if tool_name == "context_bootstrap":
            topic = str(arguments.get("topic", "general"))
            task = str(arguments.get("task", topic))
            atoms = self.store.prime_atoms(topic=topic, limit=int(arguments.get("limit", 10)))
            request = make_portfolio_request(task, topic, ExecutionPhase.PLAN, int(arguments.get("token_budget", 600)))
            portfolio = build_context_portfolio(request, tuple(atom.atom for atom in atoms) or None).to_payload()
            return {"context": _context_from_atoms(atoms), "portfolio": portfolio, "source": "persistent_store"}
        if tool_name == "build_context_portfolio":
            return self._build_context_portfolio_payload(phase, arguments)
        if tool_name == "expand_context_evidence":
            handle = str(arguments.get("handle", ""))
            evidence = self.store.expand_evidence(handle)
            if evidence is None:
                return {"handle": handle, "found": False, "content": ""}
            return {
                "handle": evidence.handle,
                "found": True,
                "content": evidence.original_content,
                "compact_claim": evidence.compact_claim,
                "source_run_id": evidence.source_run_id,
                "retrieval_count": evidence.retrieval_count,
            }
        if tool_name == "record_context_feedback":
            feedback_id = self.store.record_feedback(
                atom_id=_optional_string(arguments.get("atom_id")),
                evidence_handle=_optional_string(arguments.get("evidence_handle")),
                event_type=str(arguments.get("event_type", "feedback")),
                was_useful=_optional_bool(arguments.get("was_useful")),
                token_delta=int(arguments.get("token_delta", 0)),
            )
            return {"stored": True, "feedback_id": feedback_id}
        if tool_name == "get_context_portfolio_stats":
            return self.store.stats()
        if tool_name == "compress_context_artifact":
            content = str(arguments.get("content", ""))
            compact_claim = str(arguments.get("title", arguments.get("compact_claim", content[:160])))
            handle = self.store.create_evidence(content, compact_claim, source_task_id=_optional_string(arguments.get("source_id")))
            return {"handle": handle, "compact_claim": compact_claim, "stored": True}
        if tool_name == "create_correction":
            atom_id = self.store.create_atom(
                MemoryAtom(MemoryAtomType.CORRECTION, str(arguments.get("correct_answer", arguments.get("content", ""))), "user"),
                topic=str(arguments.get("topic", "corrections")),
                tags=_string_tuple(arguments.get("tags", ())),
                importance=10,
            )
            return {"stored": True, "atom_id": atom_id, "type": MemoryAtomType.CORRECTION.value}
        if tool_name == "store_conversation":
            content = str(arguments.get("summary", arguments.get("content", "")))
            atom_type = _memory_atom_type(arguments.get("atom_type"), MemoryAtomType.SESSION_SUMMARY)
            atom_id = self.store.create_atom(
                MemoryAtom(atom_type, content, str(arguments.get("source_scope", arguments.get("role", "assistant"))), _optional_string(arguments.get("evidence_handle"))),
                topic=str(arguments.get("topic", arguments.get("session_id", "conversation"))),
                tags=_string_tuple(arguments.get("tags", ())),
                importance=int(arguments.get("importance", 5)),
            )
            return {"stored": True, "atom_id": atom_id, "summary": content, "type": atom_type.value}
        if tool_name == "update_memory":
            return {"tool": tool_name, "accepted": True, "persistent_update": "not_available_in_mvp"}
        return {"tool": tool_name, "accepted": True, "source": "persistent_store"}

    def _build_context_portfolio_payload(self, lifecycle_phase: NemoLifecyclePhase, arguments: dict[str, Any]) -> dict[str, Any]:
        phase = ExecutionPhase(arguments.get("portfolio_phase", arguments.get("execution_phase", _execution_phase_for_lifecycle(lifecycle_phase).value)))
        topic = str(arguments.get("topic", "general"))
        request = make_portfolio_request(
            str(arguments.get("task", topic)),
            topic,
            phase,
            int(arguments.get("token_budget", 600)),
        )
        stored_atoms = self.store.search_atoms(
            topic=topic,
            tags=_string_tuple(arguments.get("tags_include", ())),
            atom_types=PHASE_PORTFOLIOS[phase].reads,
            limit=int(arguments.get("limit", 50)),
        )
        payload = build_context_portfolio(request, tuple(atom.atom for atom in stored_atoms) or None).to_payload()
        payload["source"] = "persistent_store"
        payload["memory_atom_ids"] = [atom.id for atom in stored_atoms]
        return payload


def _context_from_atoms(atoms: tuple[StoredMemoryAtom, ...]) -> str:
    return "\n".join(f"[{atom.atom.atom_type.value}] {atom.atom.content}" for atom in atoms)


def _atom_payload(atom: StoredMemoryAtom) -> dict[str, Any]:
    return {
        "id": atom.id,
        "type": atom.atom.atom_type.value,
        "content": atom.atom.content,
        "source_scope": atom.atom.source_scope,
        "topic": atom.topic,
        "tags": list(atom.tags),
        "importance": atom.importance,
        "evidence_handle": atom.atom.evidence_handle,
    }


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _memory_atom_type(value: Any, default: MemoryAtomType) -> MemoryAtomType:
    if value is None:
        return default
    if isinstance(value, MemoryAtomType):
        return value
    return MemoryAtomType(str(value))


def _execution_phase_for_lifecycle(phase: NemoLifecyclePhase) -> ExecutionPhase:
    return {
        NemoLifecyclePhase.START: ExecutionPhase.PLAN,
        NemoLifecyclePhase.PLAN: ExecutionPhase.PLAN,
        NemoLifecyclePhase.BUILD: ExecutionPhase.EXECUTE,
        NemoLifecyclePhase.REVIEW: ExecutionPhase.REVIEW,
        NemoLifecyclePhase.CLOSE: ExecutionPhase.REVIEW,
    }[phase]