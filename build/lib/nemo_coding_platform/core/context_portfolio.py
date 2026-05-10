from __future__ import annotations

from dataclasses import dataclass

from nemo_coding_platform.core.contracts import ExecutionPhase, PortfolioMode
from nemo_coding_platform.core.memory import MemoryAtom, MemoryAtomType, PortfolioRequest


@dataclass(frozen=True, slots=True)
class PortfolioEntry:
    atom: MemoryAtom
    estimated_tokens: int
    utility_score: float
    risk_score: float
    included: bool


@dataclass(frozen=True, slots=True)
class PortfolioResult:
    request: PortfolioRequest
    context: str
    entries: tuple[PortfolioEntry, ...]
    omitted: tuple[PortfolioEntry, ...]
    estimated_tokens: int
    evidence_handles: tuple[str, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "context": self.context,
            "estimated_tokens": self.estimated_tokens,
            "evidence_handles": list(self.evidence_handles),
            "included_entries": [_entry_payload(entry) for entry in self.entries],
            "omitted_entries": [_entry_payload(entry) for entry in self.omitted],
            "omitted_evidence_handles": [handle for handle in self.evidence_handles],
            "included": [entry.atom.content for entry in self.entries],
            "omitted": [entry.atom.content for entry in self.omitted],
            "mode": self.request.mode.value,
            "phase": self.request.phase.value,
            "token_budget": self.request.token_budget,
        }


TYPE_PRIORITY: dict[MemoryAtomType, int] = {
    MemoryAtomType.CORRECTION: 100,
    MemoryAtomType.PREFERENCE: 90,
    MemoryAtomType.PROJECT_FACT: 80,
    MemoryAtomType.DECISION: 70,
    MemoryAtomType.OPEN_LOOP: 60,
    MemoryAtomType.SESSION_SUMMARY: 50,
    MemoryAtomType.ARTIFACT_STATE: 45,
    MemoryAtomType.EVIDENCE: 30,
}

PHASE_BONUS: dict[ExecutionPhase, set[MemoryAtomType]] = {
    ExecutionPhase.PLAN: {MemoryAtomType.CORRECTION, MemoryAtomType.PREFERENCE, MemoryAtomType.PROJECT_FACT, MemoryAtomType.OPEN_LOOP},
    ExecutionPhase.EXECUTE: {MemoryAtomType.CORRECTION, MemoryAtomType.PROJECT_FACT, MemoryAtomType.DECISION},
    ExecutionPhase.REVIEW: {MemoryAtomType.CORRECTION, MemoryAtomType.PREFERENCE, MemoryAtomType.DECISION, MemoryAtomType.EVIDENCE},
}


def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def _entry_payload(entry: PortfolioEntry) -> dict[str, object]:
    return {
        "type": entry.atom.atom_type.value,
        "content": entry.atom.content,
        "source_scope": entry.atom.source_scope,
        "evidence_handle": entry.atom.evidence_handle,
        "estimated_tokens": entry.estimated_tokens,
        "utility_score": entry.utility_score,
        "risk_score": entry.risk_score,
        "included": entry.included,
    }


def default_atoms_for_task(task: str, topic: str) -> tuple[MemoryAtom, ...]:
    return (
        MemoryAtom(MemoryAtomType.CORRECTION, "NEMO CODE is the active product base, not just a reference repository.", "project"),
        MemoryAtom(MemoryAtomType.PREFERENCE, "Use full NEMO MCP memory/tool plane, not portfolio-only memory.", "user"),
        MemoryAtom(MemoryAtomType.PROJECT_FACT, f"Current task: {task}", "session"),
        MemoryAtom(MemoryAtomType.PROJECT_FACT, f"Topic: {topic}", "session"),
        MemoryAtom(MemoryAtomType.DECISION, "Headless Full Handoff must write only inside isolated runtime/worktree until review.", "project"),
    )


def _score_atom(atom: MemoryAtom, request: PortfolioRequest) -> tuple[float, float]:
    utility = float(TYPE_PRIORITY[atom.atom_type])
    if atom.atom_type in PHASE_BONUS[request.phase]:
        utility += 15.0
    haystack = f"{atom.content} {atom.source_scope}".lower()
    for term in {part.lower() for part in (request.task + " " + request.topic).split() if len(part) > 3}:
        if term in haystack:
            utility += 2.0
    risk = 10.0 if atom.atom_type in {MemoryAtomType.CORRECTION, MemoryAtomType.PREFERENCE} else 4.0
    if atom.evidence_handle:
        risk += 1.0
    return utility, risk


def build_context_portfolio(request: PortfolioRequest, atoms: tuple[MemoryAtom, ...] | None = None) -> PortfolioResult:
    candidates = atoms or default_atoms_for_task(request.task, request.topic)
    ranked: list[PortfolioEntry] = []
    for atom in candidates:
        utility, risk = _score_atom(atom, request)
        ranked.append(PortfolioEntry(atom, estimate_tokens(atom.content), utility, risk, False))
    ranked.sort(key=lambda entry: (entry.utility_score, entry.risk_score), reverse=True)

    included: list[PortfolioEntry] = []
    omitted: list[PortfolioEntry] = []
    spent = 0
    for entry in ranked:
        if spent + entry.estimated_tokens <= request.token_budget:
            included_entry = PortfolioEntry(entry.atom, entry.estimated_tokens, entry.utility_score, entry.risk_score, True)
            included.append(included_entry)
            spent += entry.estimated_tokens
        else:
            omitted.append(entry)

    lines = [f"[{entry.atom.atom_type.value}] {entry.atom.content}" for entry in included]
    handles = tuple(entry.atom.evidence_handle for entry in omitted if entry.atom.evidence_handle)
    return PortfolioResult(request, "\n".join(lines), tuple(included), tuple(omitted), spent, handles)


def make_portfolio_request(task: str, topic: str, phase: ExecutionPhase = ExecutionPhase.PLAN, token_budget: int = 600) -> PortfolioRequest:
    mode = {
        ExecutionPhase.PLAN: PortfolioMode.SAFE,
        ExecutionPhase.EXECUTE: PortfolioMode.FAST,
        ExecutionPhase.REVIEW: PortfolioMode.THOROUGH,
    }[phase]
    return PortfolioRequest(task=task, phase=phase, topic=topic, token_budget=token_budget, mode=mode)
