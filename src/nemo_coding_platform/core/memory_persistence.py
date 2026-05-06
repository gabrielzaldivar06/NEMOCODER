from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from nemo_coding_platform.core.memory import MemoryAtom, MemoryAtomType


@dataclass(frozen=True, slots=True)
class StoredMemoryAtom:
    id: str
    atom: MemoryAtom
    topic: str
    tags: tuple[str, ...]
    importance: int
    created_at: str
    last_accessed_at: str | None
    access_count: int
    useful_count: int
    not_useful_count: int


@dataclass(frozen=True, slots=True)
class StoredEvidence:
    handle: str
    original_content: str
    compact_claim: str
    source_task_id: str | None
    source_run_id: str | None
    content_hash: str
    created_at: str
    expires_at: str | None
    retrieval_count: int


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json_tuple(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    loaded = json.loads(value)
    return tuple(str(item) for item in loaded)


class PersistentMemoryStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_atoms (
                    id TEXT PRIMARY KEY,
                    atom_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source_scope TEXT NOT NULL,
                    topic TEXT NOT NULL DEFAULT '',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    importance INTEGER NOT NULL DEFAULT 5,
                    evidence_handle TEXT,
                    created_at TEXT NOT NULL,
                    last_accessed_at TEXT,
                    access_count INTEGER NOT NULL DEFAULT 0,
                    useful_count INTEGER NOT NULL DEFAULT 0,
                    not_useful_count INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_memory_atoms_topic ON memory_atoms(topic);
                CREATE INDEX IF NOT EXISTS idx_memory_atoms_type ON memory_atoms(atom_type);
                CREATE INDEX IF NOT EXISTS idx_memory_atoms_importance ON memory_atoms(importance DESC);

                CREATE TABLE IF NOT EXISTS evidence (
                    handle TEXT PRIMARY KEY,
                    original_content TEXT NOT NULL,
                    compact_claim TEXT NOT NULL,
                    source_task_id TEXT,
                    source_run_id TEXT,
                    content_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    retrieval_count INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS feedback (
                    id TEXT PRIMARY KEY,
                    atom_id TEXT,
                    evidence_handle TEXT,
                    event_type TEXT NOT NULL,
                    was_useful INTEGER,
                    token_delta INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                """
            )

    def create_atom(
        self,
        atom: MemoryAtom,
        *,
        topic: str = "",
        tags: tuple[str, ...] = (),
        importance: int = 5,
        atom_id: str | None = None,
    ) -> str:
        created_at = _now()
        stable_input = "|".join((atom.atom_type.value, atom.content, atom.source_scope, topic, created_at))
        memory_id = atom_id or "mem_" + hashlib.sha256(stable_input.encode("utf-8")).hexdigest()[:16]
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO memory_atoms (
                    id, atom_type, content, source_scope, topic, tags_json, importance,
                    evidence_handle, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory_id,
                    atom.atom_type.value,
                    atom.content,
                    atom.source_scope,
                    topic,
                    json.dumps(list(tags), sort_keys=True),
                    importance,
                    atom.evidence_handle,
                    created_at,
                ),
            )
        return memory_id

    def get_atom(self, atom_id: str) -> StoredMemoryAtom:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM memory_atoms WHERE id = ?", (atom_id,)).fetchone()
        if row is None:
            raise KeyError(atom_id)
        return self._row_to_atom(row)

    def search_atoms(
        self,
        *,
        topic: str | None = None,
        tags: tuple[str, ...] = (),
        atom_types: tuple[MemoryAtomType, ...] = (),
        limit: int = 20,
    ) -> tuple[StoredMemoryAtom, ...]:
        clauses: list[str] = []
        params: list[Any] = []
        if topic is not None:
            clauses.append("topic = ?")
            params.append(topic)
        if atom_types:
            clauses.append("atom_type IN (" + ",".join("?" for _ in atom_types) + ")")
            params.extend(atom_type.value for atom_type in atom_types)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM memory_atoms
                {where}
                ORDER BY importance DESC, useful_count DESC, access_count DESC, created_at DESC
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
        atoms = tuple(self._row_to_atom(row) for row in rows)
        if tags:
            required = set(tags)
            atoms = tuple(atom for atom in atoms if required.issubset(set(atom.tags)))
        self._mark_atoms_accessed(tuple(atom.id for atom in atoms))
        return atoms

    def prime_atoms(self, *, topic: str | None = None, limit: int = 10) -> tuple[StoredMemoryAtom, ...]:
        priority_types = (
            MemoryAtomType.CORRECTION,
            MemoryAtomType.PREFERENCE,
            MemoryAtomType.DECISION,
            MemoryAtomType.PROJECT_FACT,
            MemoryAtomType.OPEN_LOOP,
            MemoryAtomType.SESSION_SUMMARY,
        )
        return self.search_atoms(topic=topic, atom_types=priority_types, limit=limit)

    def create_evidence(
        self,
        original_content: str,
        compact_claim: str,
        *,
        source_task_id: str | None = None,
        source_run_id: str | None = None,
        expires_at: str | None = None,
        handle: str | None = None,
    ) -> str:
        content_hash = hashlib.sha256(original_content.encode("utf-8")).hexdigest()
        evidence_handle = handle or "ev_" + content_hash[:16]
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO evidence (
                    handle, original_content, compact_claim, source_task_id, source_run_id,
                    content_hash, created_at, expires_at, retrieval_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT retrieval_count FROM evidence WHERE handle = ?), 0))
                """,
                (
                    evidence_handle,
                    original_content,
                    compact_claim,
                    source_task_id,
                    source_run_id,
                    content_hash,
                    _now(),
                    expires_at,
                    evidence_handle,
                ),
            )
        return evidence_handle

    def expand_evidence(self, handle: str) -> StoredEvidence | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM evidence WHERE handle = ?", (handle,)).fetchone()
            if row is None:
                return None
            connection.execute("UPDATE evidence SET retrieval_count = retrieval_count + 1 WHERE handle = ?", (handle,))
            updated = connection.execute("SELECT * FROM evidence WHERE handle = ?", (handle,)).fetchone()
        return self._row_to_evidence(updated)

    def record_feedback(
        self,
        *,
        atom_id: str | None = None,
        evidence_handle: str | None = None,
        event_type: str,
        was_useful: bool | None = None,
        token_delta: int = 0,
    ) -> str:
        created_at = _now()
        feedback_id = "fb_" + hashlib.sha256(f"{atom_id}|{evidence_handle}|{event_type}|{created_at}".encode("utf-8")).hexdigest()[:16]
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO feedback (id, atom_id, evidence_handle, event_type, was_useful, token_delta, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (feedback_id, atom_id, evidence_handle, event_type, None if was_useful is None else int(was_useful), token_delta, created_at),
            )
            if atom_id and was_useful is True:
                connection.execute("UPDATE memory_atoms SET useful_count = useful_count + 1 WHERE id = ?", (atom_id,))
            if atom_id and was_useful is False:
                connection.execute("UPDATE memory_atoms SET not_useful_count = not_useful_count + 1 WHERE id = ?", (atom_id,))
        return feedback_id

    def stats(self) -> dict[str, int]:
        with self._connect() as connection:
            atom_count = connection.execute("SELECT COUNT(*) FROM memory_atoms").fetchone()[0]
            evidence_count = connection.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
            feedback_count = connection.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
        return {"atom_count": int(atom_count), "evidence_count": int(evidence_count), "feedback_count": int(feedback_count)}

    def list_atoms_by_id(self, atom_ids: tuple[str, ...]) -> tuple[StoredMemoryAtom, ...]:
        if not atom_ids:
            return ()
        placeholders = ",".join("?" for _ in atom_ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM memory_atoms WHERE id IN ({placeholders}) ORDER BY importance DESC, created_at DESC",
                atom_ids,
            ).fetchall()
        return tuple(self._row_to_atom(row) for row in rows)

    def list_evidence(self, *, limit: int = 20) -> tuple[StoredEvidence, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM evidence ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(self._row_to_evidence(row) for row in rows)

    def list_feedback(self, *, limit: int = 20) -> tuple[dict[str, Any], ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM feedback ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple({key: row[key] for key in row.keys()} for row in rows)

    def _mark_atoms_accessed(self, atom_ids: tuple[str, ...]) -> None:
        if not atom_ids:
            return
        accessed_at = _now()
        with self._connect() as connection:
            connection.executemany(
                "UPDATE memory_atoms SET last_accessed_at = ?, access_count = access_count + 1 WHERE id = ?",
                ((accessed_at, atom_id) for atom_id in atom_ids),
            )

    def _row_to_atom(self, row: sqlite3.Row) -> StoredMemoryAtom:
        return StoredMemoryAtom(
            str(row["id"]),
            MemoryAtom(MemoryAtomType(str(row["atom_type"])), str(row["content"]), str(row["source_scope"]), row["evidence_handle"]),
            str(row["topic"]),
            _json_tuple(row["tags_json"]),
            int(row["importance"]),
            str(row["created_at"]),
            row["last_accessed_at"],
            int(row["access_count"]),
            int(row["useful_count"]),
            int(row["not_useful_count"]),
        )

    def _row_to_evidence(self, row: sqlite3.Row) -> StoredEvidence:
        return StoredEvidence(
            str(row["handle"]),
            str(row["original_content"]),
            str(row["compact_claim"]),
            row["source_task_id"],
            row["source_run_id"],
            str(row["content_hash"]),
            str(row["created_at"]),
            row["expires_at"],
            int(row["retrieval_count"]),
        )
