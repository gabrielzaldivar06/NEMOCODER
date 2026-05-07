from __future__ import annotations

import json
import uuid
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from platform import platform as platform_name
from platform import python_version
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
class McpNemoAdapter:
    sse_url: str
    tool_prefix: str = ""
    _endpoint_cache: dict[str, str] = field(default_factory=dict)

    def call(self, phase: NemoLifecyclePhase, tool_name: str, **arguments: Any) -> tuple[McpNemoAdapter, NemoCallResult]:
        call = NemoCall(phase, tool_name, dict(arguments))
        if not tool_allowed_in_lifecycle(phase, tool_name):
            raise PermissionError(f"NEMO tool {tool_name} is not allowed in lifecycle phase {phase}")

        try:
            endpoint = self._get_endpoint()
            mcp_tool_name = f"{self.tool_prefix}{tool_name}"
            
            # JSON-RPC tool call
            payload = {
                "jsonrpc": "2.0",
                "id": str(uuid.uuid4()),
                "method": "tools/call",
                "params": {
                    "name": mcp_tool_name,
                    "arguments": arguments
                }
            }

            req = urllib.request.Request(
                endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            
            with urllib.request.urlopen(req, timeout=30) as f:
                response = json.loads(f.read().decode("utf-8"))
                
            if "error" in response:
                return self, NemoCallResult(call, False, {"error": response["error"]})
                
            # MCP response structure: result.content[0].text
            result = response.get("result", {})
            content = result.get("content", [])
            if content and content[0].get("type") == "text":
                text_payload = content[0].get("text", "{}")
                try:
                    data = json.loads(text_payload)
                except json.JSONDecodeError:
                    data = {"raw_text": text_payload}
                return self, NemoCallResult(call, True, data)
            
            return self, NemoCallResult(call, True, result)

        except Exception as e:
            return self, NemoCallResult(call, False, {"error": str(e)})

    def _get_endpoint(self) -> str:
        if self.sse_url in self._endpoint_cache:
            return self._endpoint_cache[self.sse_url]
        
        # Handshake: GET the SSE stream and find the 'endpoint' event
        # This is a bit hacky without a real SSE client, but usually the first few lines contain it.
        with urllib.request.urlopen(self.sse_url, timeout=10) as f:
            for _ in range(20): # Look at first 20 lines
                line = f.readline().decode("utf-8").strip()
                if line.startswith("event: endpoint"):
                    data_line = f.readline().decode("utf-8").strip()
                    if data_line.startswith("data: "):
                        endpoint_path = data_line[6:]
                        # Resolve relative path
                        from urllib.parse import urljoin
                        endpoint = urljoin(self.sse_url, endpoint_path)
                        self._endpoint_cache[self.sse_url] = endpoint
                        return endpoint
        
        raise ConnectionError(f"Could not find MCP endpoint in SSE stream at {self.sse_url}")


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
        if tool_name == "compare_context_strategies":
            return {"source": "in_memory", "strategies": {"portfolio": self._payload_for("build_context_portfolio", arguments)}}
        if tool_name == "refresh_context_portfolio":
            payload = self._payload_for("build_context_portfolio", arguments)
            payload["refreshed"] = True
            return payload
        if tool_name == "expand_context_evidence":
            return {"handle": arguments.get("handle"), "content": "in-memory evidence expansion unavailable in MVP"}
        if tool_name == "get_current_time":
            now = datetime.now(UTC).isoformat()
            return {"utc": now, "iso": now, "timezone": "UTC"}
        if tool_name == "get_environment_info":
            return {"cwd": str(Path.cwd()), "platform": platform_name(), "python_version": python_version()}
        if tool_name == "get_weather_open_meteo":
            return _weather_payload(arguments)
        if tool_name == "get_recent_context":
            return {"context": "", "messages": (), "source": "in_memory_unavailable"}
        if tool_name == "get_system_health":
            return {"status": "ok", "source": "in_memory", "calls": len(self.calls)}
        if tool_name == "compress_context_artifact":
            return {"handle": f"in-memory-evidence-{len(self.calls) + 1}", "compact_claim": str(arguments.get("title", "artifact")), "stored": False}
        if tool_name == "store_conversation":
            return {"stored": True, "summary": arguments.get("summary", "")}
        if tool_name == "create_correction":
            return {"stored": True, "type": MemoryAtomType.CORRECTION.value, "content": arguments.get("correct_answer", arguments.get("content", ""))}
        if tool_name == "cognitive_ingest":
            return {"stored": True, "source": "in_memory", "content": arguments.get("content", arguments.get("memory", ""))}
        if tool_name == "anticipate":
            return {"source": "in_memory", "memories": (), "task": arguments.get("task")}
        if tool_name == "detect_redundancy":
            return {"source": "in_memory", "duplicates": ()}
        if tool_name == "memory_chronicle":
            return {"source": "in_memory", "memories": ()}
        if tool_name == "salience_score":
            content = str(arguments.get("content", arguments.get("query", "")))
            return {"source": "in_memory", "score": min(1.0, len(content) / 200.0)}
        if tool_name == "update_memory":
            return {"updated": False, "source": "in_memory_unavailable", "memory_id": arguments.get("memory_id", arguments.get("atom_id"))}
        if tool_name == "record_context_feedback":
            return {"stored": True, "source": "in_memory", "event_type": arguments.get("event_type", "feedback")}
        if tool_name == "create_reminder":
            return {"stored": True, "type": "reminder", "title": arguments.get("title", arguments.get("text", ""))}
        if tool_name in {"get_active_reminders", "get_completed_reminders"}:
            return {"source": "in_memory", "reminders": ()}
        if tool_name in {"complete_reminder", "delete_reminder", "reschedule_reminder"}:
            return {"updated": False, "source": "in_memory_unavailable", "reminder_id": arguments.get("reminder_id", arguments.get("atom_id"))}
        if tool_name == "intent_anchor":
            return {"stored": True, "type": "intent_anchor", "trigger_condition": arguments.get("trigger_condition")}
        if tool_name == "create_appointment":
            return {"stored": True, "type": "appointment", "title": arguments.get("title")}
        if tool_name in {"get_recent_appointments", "get_upcoming_appointments"}:
            return {"source": "in_memory", "appointments": ()}
        if tool_name in {"cancel_appointment", "complete_appointment"}:
            return {"updated": False, "source": "in_memory_unavailable", "appointment_id": arguments.get("appointment_id", arguments.get("atom_id"))}
        if tool_name == "store_architectural_decision":
            return {"stored": True, "type": MemoryAtomType.DECISION.value, "decision": arguments.get("decision", arguments.get("content", ""))}
        if tool_name == "search_memories":
            return {"query": arguments.get("query"), "memories": (), "source": "in_memory_unavailable"}
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
        if tool_name == "compare_context_strategies":
            return self._compare_context_strategies_payload(phase, arguments)
        if tool_name == "refresh_context_portfolio":
            payload = self._build_context_portfolio_payload(phase, arguments)
            payload["refreshed"] = True
            return payload
        if tool_name == "get_recent_context":
            topic = _optional_string(arguments.get("topic", arguments.get("session_id")))
            atoms = self.store.search_atoms(
                topic=topic,
                atom_types=(MemoryAtomType.SESSION_SUMMARY, MemoryAtomType.ARTIFACT_STATE),
                limit=int(arguments.get("limit", 10)),
            )
            return {"context": _context_from_atoms(atoms), "messages": [_atom_payload(atom) for atom in atoms], "source": "persistent_store"}
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
        if tool_name == "get_current_time":
            now = datetime.now(UTC).isoformat()
            return {"utc": now, "iso": now, "timezone": "UTC"}
        if tool_name == "get_environment_info":
            return {
                "cwd": str(Path.cwd()),
                "platform": platform_name(),
                "python_version": python_version(),
                "memory_db": str(self.store.db_path),
                "memory_db_exists": self.store.db_path.exists(),
            }
        if tool_name == "get_weather_open_meteo":
            return _weather_payload(arguments)
        if tool_name == "get_system_health":
            stats = self.store.stats()
            return {
                "status": "ok",
                "source": "persistent_store",
                "memory_db": str(self.store.db_path),
                "memory_db_exists": self.store.db_path.exists(),
                "calls_recorded": len(self.calls),
                **stats,
            }
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
        if tool_name == "cognitive_ingest":
            content = str(arguments.get("content", arguments.get("memory", arguments.get("text", ""))))
            atom_type = _memory_atom_type(arguments.get("atom_type", arguments.get("type")), MemoryAtomType.PROJECT_FACT)
            atom_id = self.store.create_atom(
                MemoryAtom(atom_type, content, str(arguments.get("source_scope", "cognitive_ingest")), _optional_string(arguments.get("evidence_handle"))),
                topic=str(arguments.get("topic", "general")),
                tags=_string_tuple(arguments.get("tags", ())),
                importance=int(arguments.get("importance", arguments.get("importance_level", 5))),
            )
            return {"stored": True, "atom_id": atom_id, "type": atom_type.value, "source": "persistent_store"}
        if tool_name == "anticipate":
            query = _optional_string(arguments.get("task", arguments.get("query")))
            atoms = self.store.search_atoms(query=query, topic=_optional_string(arguments.get("topic")), limit=int(arguments.get("limit", 10)))
            return {"task": query, "memories": [_atom_payload(atom) for atom in atoms], "source": "persistent_store"}
        if tool_name == "detect_redundancy":
            atoms = self.store.search_atoms(topic=_optional_string(arguments.get("topic")), limit=int(arguments.get("limit", 100)))
            duplicates = _duplicate_groups(atoms)
            return {"duplicates": duplicates, "duplicate_count": len(duplicates), "source": "persistent_store"}
        if tool_name == "memory_chronicle":
            atoms = self.store.search_atoms(topic=_optional_string(arguments.get("topic")), limit=int(arguments.get("limit", 20)))
            return {"memories": [_atom_payload(atom) for atom in sorted(atoms, key=lambda item: item.created_at, reverse=True)], "source": "persistent_store"}
        if tool_name == "salience_score":
            content = str(arguments.get("content", arguments.get("query", "")))
            task = str(arguments.get("task", arguments.get("topic", "")))
            return {"score": _salience_score(content, task), "content": content, "task": task, "source": "persistent_store"}
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
            atom_id = str(arguments.get("memory_id", arguments.get("atom_id", "")))
            updated = self.store.update_atom(
                atom_id,
                content=_optional_string(arguments.get("content")),
                topic=_optional_string(arguments.get("topic")),
                tags=None if arguments.get("tags") is None else _string_tuple(arguments.get("tags")),
                importance=None if arguments.get("importance") is None and arguments.get("importance_level") is None else int(arguments.get("importance", arguments.get("importance_level"))),
                evidence_handle=_optional_string(arguments.get("evidence_handle")),
            )
            return {"updated": True, "memory": _atom_payload(updated), "source": "persistent_store"}
        if tool_name == "create_reminder":
            title = str(arguments.get("title", arguments.get("text", ""))).strip() or "Reminder"
            due = _optional_string(arguments.get("due", arguments.get("due_datetime", arguments.get("scheduled_datetime"))))
            content = _format_record("Reminder", title, {"status": "active", "due": due, "description": _optional_string(arguments.get("description"))})
            atom_id = self.store.create_atom(
                MemoryAtom(MemoryAtomType.OPEN_LOOP, content, "reminder"),
                topic=str(arguments.get("topic", "reminders")),
                tags=("reminder", "active") + _string_tuple(arguments.get("tags", ())),
                importance=int(arguments.get("importance", arguments.get("importance_level", 7))),
            )
            return {"stored": True, "atom_id": atom_id, "type": "reminder", "title": title, "due": due}
        if tool_name == "get_active_reminders":
            return {"reminders": _record_payloads(self.store.search_atoms(topic=_optional_string(arguments.get("topic")), tags=("reminder",), limit=int(arguments.get("limit", 20))), include_tags=("active",), exclude_tags=("completed", "deleted")), "source": "persistent_store"}
        if tool_name == "get_completed_reminders":
            return {"reminders": _record_payloads(self.store.search_atoms(topic=_optional_string(arguments.get("topic")), tags=("reminder",), limit=int(arguments.get("limit", 20))), include_tags=("completed",)), "source": "persistent_store"}
        if tool_name == "complete_reminder":
            return _complete_record(self.store, str(arguments.get("reminder_id", arguments.get("atom_id", ""))), "completed")
        if tool_name == "delete_reminder":
            atom_id = str(arguments.get("reminder_id", arguments.get("atom_id", "")))
            return {"deleted": self.store.delete_atom(atom_id), "atom_id": atom_id, "source": "persistent_store"}
        if tool_name == "reschedule_reminder":
            atom_id = str(arguments.get("reminder_id", arguments.get("atom_id", "")))
            due = _optional_string(arguments.get("due", arguments.get("due_datetime", arguments.get("scheduled_datetime"))))
            current = self.store.get_atom(atom_id)
            updated = self.store.update_atom(atom_id, content=f"{current.atom.content} | due={due}", tags=_replace_status_tags(current.tags, "active"))
            return {"updated": True, "atom_id": atom_id, "due": due, "memory": _atom_payload(updated), "source": "persistent_store"}
        if tool_name == "intent_anchor":
            trigger = str(arguments.get("trigger_condition", "")).strip()
            action = str(arguments.get("action", "")).strip()
            content = _format_record("Intent Anchor", action or "Remember anchored action", {"trigger_condition": trigger})
            atom_id = self.store.create_atom(
                MemoryAtom(MemoryAtomType.OPEN_LOOP, content, "intent_anchor"),
                topic=str(arguments.get("topic", "intent_anchors")),
                tags=("intent_anchor",) + _string_tuple(arguments.get("tags", ())),
                importance=int(arguments.get("importance", arguments.get("importance_level", 7))),
            )
            return {"stored": True, "atom_id": atom_id, "type": "intent_anchor", "trigger_condition": trigger, "action": action}
        if tool_name == "create_appointment":
            title = str(arguments.get("title", "Appointment")).strip() or "Appointment"
            scheduled = _optional_string(arguments.get("scheduled_datetime", arguments.get("due_datetime")))
            content = _format_record(
                "Appointment",
                title,
                {
                    "status": "upcoming",
                    "scheduled_datetime": scheduled,
                    "location": _optional_string(arguments.get("location")),
                    "description": _optional_string(arguments.get("description")),
                    "recurrence_pattern": _optional_string(arguments.get("recurrence_pattern")),
                    "recurrence_count": _optional_string(arguments.get("recurrence_count")),
                },
            )
            atom_id = self.store.create_atom(
                MemoryAtom(MemoryAtomType.OPEN_LOOP, content, "appointment"),
                topic=str(arguments.get("topic", "appointments")),
                tags=("appointment", "upcoming") + _string_tuple(arguments.get("tags", ())),
                importance=int(arguments.get("importance", arguments.get("importance_level", 7))),
            )
            return {"stored": True, "atom_id": atom_id, "type": "appointment", "title": title, "scheduled_datetime": scheduled}
        if tool_name == "get_recent_appointments":
            atoms = self.store.search_atoms(topic=_optional_string(arguments.get("topic")), tags=("appointment",), limit=int(arguments.get("limit", 20)))
            return {"appointments": _record_payloads(atoms, include_tags=("completed", "cancelled"), any_include=True), "source": "persistent_store"}
        if tool_name == "get_upcoming_appointments":
            atoms = self.store.search_atoms(topic=_optional_string(arguments.get("topic")), tags=("appointment",), limit=int(arguments.get("limit", 20)))
            return {"appointments": _record_payloads(atoms, include_tags=("upcoming",), exclude_tags=("completed", "cancelled")), "source": "persistent_store"}
        if tool_name == "cancel_appointment":
            return _complete_record(self.store, str(arguments.get("appointment_id", arguments.get("atom_id", ""))), "cancelled")
        if tool_name == "complete_appointment":
            return _complete_record(self.store, str(arguments.get("appointment_id", arguments.get("atom_id", ""))), "completed")
        if tool_name == "store_architectural_decision":
            content = str(arguments.get("decision", arguments.get("content", "")))
            atom_id = self.store.create_atom(
                MemoryAtom(MemoryAtomType.DECISION, content, "assistant", _optional_string(arguments.get("evidence_handle"))),
                topic=str(arguments.get("topic", arguments.get("session_id", "architecture"))),
                tags=_string_tuple(arguments.get("tags", ())),
                importance=int(arguments.get("importance", 8)),
            )
            return {"stored": True, "atom_id": atom_id, "decision": content, "type": MemoryAtomType.DECISION.value}
        if tool_name == "search_memories":
            query = _optional_string(arguments.get("query"))
            topic = _optional_string(arguments.get("topic"))
            atoms = self.store.search_atoms(
                query=query,
                topic=topic,
                limit=int(arguments.get("limit", 20)),
            )
            return {"query": query, "memories": [_atom_payload(atom) for atom in atoms], "source": "persistent_store"}
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
        included_keys = {_atom_key(entry) for entry in payload.get("included_entries", ()) if isinstance(entry, dict)}
        omitted_keys = {_atom_key(entry) for entry in payload.get("omitted_entries", ()) if isinstance(entry, dict)}
        payload["included_memory_atom_ids"] = [atom.id for atom in stored_atoms if _stored_atom_key(atom) in included_keys]
        payload["omitted_memory_atom_ids"] = [atom.id for atom in stored_atoms if _stored_atom_key(atom) in omitted_keys]
        return payload

    def _compare_context_strategies_payload(self, lifecycle_phase: NemoLifecyclePhase, arguments: dict[str, Any]) -> dict[str, Any]:
        topic = str(arguments.get("topic", "general"))
        atoms = self.store.search_atoms(topic=topic, limit=int(arguments.get("limit", 50)))
        raw_context = _context_from_atoms(atoms)
        portfolio = self._build_context_portfolio_payload(lifecycle_phase, arguments)
        raw_tokens = max(1, (len(raw_context) + 3) // 4) if raw_context else 0
        portfolio_tokens = int(portfolio.get("estimated_tokens", 0))
        savings = raw_tokens - portfolio_tokens
        return {
            "source": "persistent_store",
            "strategies": {
                "raw": {"estimated_tokens": raw_tokens, "memory_count": len(atoms)},
                "portfolio": {"estimated_tokens": portfolio_tokens, "memory_count": len(portfolio.get("included_memory_atom_ids", ())), "payload": portfolio},
            },
            "estimated_token_savings": savings,
        }


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


def _stored_atom_key(atom: StoredMemoryAtom) -> tuple[str, str, str, str | None]:
    return (atom.atom.atom_type.value, atom.atom.content, atom.atom.source_scope, atom.atom.evidence_handle)


def _atom_key(entry: dict[str, Any]) -> tuple[str, str, str, str | None]:
    evidence_handle = entry.get("evidence_handle")
    return (str(entry.get("type", "")), str(entry.get("content", "")), str(entry.get("source_scope", "")), None if evidence_handle is None else str(evidence_handle))


def _format_record(kind: str, title: str, fields: dict[str, str | None]) -> str:
    details = [f"{key}={value}" for key, value in fields.items() if value]
    suffix = " | " + " | ".join(details) if details else ""
    return f"{kind}: {title}{suffix}"


def _record_payloads(
    atoms: tuple[StoredMemoryAtom, ...],
    *,
    include_tags: tuple[str, ...] = (),
    exclude_tags: tuple[str, ...] = (),
    any_include: bool = False,
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    include_set = set(include_tags)
    exclude_set = set(exclude_tags)
    for atom in atoms:
        tags = set(atom.tags)
        if include_set and (tags.isdisjoint(include_set) if any_include else not include_set.issubset(tags)):
            continue
        if exclude_set and not tags.isdisjoint(exclude_set):
            continue
        payloads.append(_atom_payload(atom))
    return payloads


def _replace_status_tags(tags: tuple[str, ...], status: str) -> tuple[str, ...]:
    status_tags = {"active", "completed", "deleted", "upcoming", "cancelled"}
    retained = tuple(tag for tag in tags if tag not in status_tags)
    return retained + (status,)


def _complete_record(store: PersistentMemoryStore, atom_id: str, status: str) -> dict[str, Any]:
    current = store.get_atom(atom_id)
    updated = store.update_atom(
        atom_id,
        content=f"{current.atom.content} | status={status}",
        tags=_replace_status_tags(current.tags, status),
    )
    return {"updated": True, "atom_id": atom_id, "status": status, "memory": _atom_payload(updated), "source": "persistent_store"}


def _duplicate_groups(atoms: tuple[StoredMemoryAtom, ...]) -> list[dict[str, Any]]:
    buckets: dict[str, list[StoredMemoryAtom]] = {}
    for atom in atoms:
        key = " ".join(atom.atom.content.lower().split())
        buckets.setdefault(key, []).append(atom)
    return [
        {"content": group[0].atom.content, "memory_ids": [atom.id for atom in group], "count": len(group)}
        for group in buckets.values()
        if len(group) > 1
    ]


def _salience_score(content: str, task: str) -> float:
    content_terms = {term.lower() for term in content.split() if len(term) > 3}
    task_terms = {term.lower() for term in task.split() if len(term) > 3}
    if not content_terms or not task_terms:
        return 0.0
    overlap = len(content_terms & task_terms)
    return round(min(1.0, overlap / max(1, len(task_terms))), 3)


def _weather_payload(arguments: dict[str, Any]) -> dict[str, Any]:
    latitude = float(arguments.get("latitude", 46.3369))
    longitude = float(arguments.get("longitude", -94.6467))
    timezone = str(arguments.get("timezone_str", arguments.get("timezone", "auto")))
    if arguments.get("offline"):
        return {"status": "offline", "source": "open-meteo", "latitude": latitude, "longitude": longitude}
    query = urllib.parse.urlencode(
        {
            "latitude": latitude,
            "longitude": longitude,
            "timezone": timezone,
            "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "forecast_days": 1,
        }
    )
    url = f"https://api.open-meteo.com/v1/forecast?{query}"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
        return {"status": "ok", "source": "open-meteo", "latitude": latitude, "longitude": longitude, "forecast": data}
    except Exception as error:
        return {"status": "unavailable", "source": "open-meteo", "latitude": latitude, "longitude": longitude, "error": str(error)}


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