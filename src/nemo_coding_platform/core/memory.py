from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from nemo_coding_platform.core.contracts import ExecutionPhase, PortfolioMode


class MemoryAtomType(StrEnum):
    CORRECTION = "correction"
    PREFERENCE = "preference"
    PROJECT_FACT = "project_fact"
    DECISION = "decision"
    OPEN_LOOP = "open_loop"
    SESSION_SUMMARY = "session_summary"
    ARTIFACT_STATE = "artifact_state"
    EVIDENCE = "evidence"


class NemoToolSuite(StrEnum):
    STARTUP_CONTEXT = "startup_context"
    CONTEXT_ECONOMY = "context_economy"
    MEMORY_MANAGEMENT = "memory_management"
    CONVERSATION = "conversation"
    TIME_AND_ENVIRONMENT = "time_and_environment"
    REMINDERS = "reminders"
    APPOINTMENTS = "appointments"
    MAINTENANCE = "maintenance"


class NemoToolRisk(StrEnum):
    READ_ONLY = "read_only"
    MEMORY_WRITE = "memory_write"
    SCHEDULING_WRITE = "scheduling_write"
    DESTRUCTIVE = "destructive"


@dataclass(frozen=True, slots=True)
class NemoToolContract:
    name: str
    suite: NemoToolSuite
    risk: NemoToolRisk
    phase_access: tuple[ExecutionPhase, ...]
    purpose: str


@dataclass(frozen=True, slots=True)
class MemoryAtom:
    atom_type: MemoryAtomType
    content: str
    source_scope: str
    evidence_handle: str | None = None


@dataclass(frozen=True, slots=True)
class PortfolioRequest:
    task: str
    phase: ExecutionPhase
    topic: str
    token_budget: int
    mode: PortfolioMode


@dataclass(frozen=True, slots=True)
class PortfolioContract:
    phase: ExecutionPhase
    mode: PortfolioMode
    reads: tuple[MemoryAtomType, ...]
    writes: tuple[MemoryAtomType, ...]
    expands_evidence: bool


NEMO_TOOL_REGISTRY: tuple[NemoToolContract, ...] = (
    NemoToolContract(
        name="prime_context",
        suite=NemoToolSuite.STARTUP_CONTEXT,
        risk=NemoToolRisk.READ_ONLY,
        phase_access=(ExecutionPhase.PLAN, ExecutionPhase.EXECUTE, ExecutionPhase.REVIEW),
        purpose="Load high-priority memories, reminders, and recent continuity before work starts.",
    ),
    NemoToolContract(
        name="context_bootstrap",
        suite=NemoToolSuite.STARTUP_CONTEXT,
        risk=NemoToolRisk.READ_ONLY,
        phase_access=(ExecutionPhase.PLAN,),
        purpose="Build lightweight startup context plus a dry-run context portfolio.",
    ),
    NemoToolContract(
        name="build_context_portfolio",
        suite=NemoToolSuite.CONTEXT_ECONOMY,
        risk=NemoToolRisk.READ_ONLY,
        phase_access=(ExecutionPhase.PLAN, ExecutionPhase.EXECUTE, ExecutionPhase.REVIEW),
        purpose="Compile bounded, typed, evidence-backed context for a task phase.",
    ),
    NemoToolContract(
        name="expand_context_evidence",
        suite=NemoToolSuite.CONTEXT_ECONOMY,
        risk=NemoToolRisk.READ_ONLY,
        phase_access=(ExecutionPhase.EXECUTE, ExecutionPhase.REVIEW),
        purpose="Recover full or query-scoped evidence from portfolio handles.",
    ),
    NemoToolContract(
        name="record_context_feedback",
        suite=NemoToolSuite.CONTEXT_ECONOMY,
        risk=NemoToolRisk.MEMORY_WRITE,
        phase_access=(ExecutionPhase.PLAN, ExecutionPhase.EXECUTE, ExecutionPhase.REVIEW),
        purpose="Record whether portfolio entries and evidence expansions were useful.",
    ),
    NemoToolContract(
        name="get_context_portfolio_stats",
        suite=NemoToolSuite.CONTEXT_ECONOMY,
        risk=NemoToolRisk.READ_ONLY,
        phase_access=(ExecutionPhase.REVIEW,),
        purpose="Inspect context economy performance and portfolio health.",
    ),
    NemoToolContract(
        name="compare_context_strategies",
        suite=NemoToolSuite.CONTEXT_ECONOMY,
        risk=NemoToolRisk.READ_ONLY,
        phase_access=(ExecutionPhase.PLAN, ExecutionPhase.EXECUTE, ExecutionPhase.REVIEW),
        purpose="Compare raw, prime, and portfolio context strategies before spending tokens.",
    ),
    NemoToolContract(
        name="refresh_context_portfolio",
        suite=NemoToolSuite.CONTEXT_ECONOMY,
        risk=NemoToolRisk.READ_ONLY,
        phase_access=(ExecutionPhase.PLAN, ExecutionPhase.EXECUTE, ExecutionPhase.REVIEW),
        purpose="Rebuild a context portfolio with fresh memory candidates for the current task.",
    ),
    NemoToolContract(
        name="compress_context_artifact",
        suite=NemoToolSuite.CONTEXT_ECONOMY,
        risk=NemoToolRisk.MEMORY_WRITE,
        phase_access=(ExecutionPhase.EXECUTE, ExecutionPhase.REVIEW),
        purpose="Compact large outputs while preserving recoverable evidence.",
    ),
    NemoToolContract(
        name="create_correction",
        suite=NemoToolSuite.MEMORY_MANAGEMENT,
        risk=NemoToolRisk.MEMORY_WRITE,
        phase_access=(ExecutionPhase.PLAN, ExecutionPhase.EXECUTE, ExecutionPhase.REVIEW),
        purpose="Persist user corrections immediately with maximum retrieval priority.",
    ),
    NemoToolContract(
        name="cognitive_ingest",
        suite=NemoToolSuite.MEMORY_MANAGEMENT,
        risk=NemoToolRisk.MEMORY_WRITE,
        phase_access=(ExecutionPhase.PLAN, ExecutionPhase.EXECUTE, ExecutionPhase.REVIEW),
        purpose="Ingest a memory atom while reusing the persistent typed memory model.",
    ),
    NemoToolContract(
        name="anticipate",
        suite=NemoToolSuite.MEMORY_MANAGEMENT,
        risk=NemoToolRisk.READ_ONLY,
        phase_access=(ExecutionPhase.PLAN, ExecutionPhase.EXECUTE, ExecutionPhase.REVIEW),
        purpose="Predict relevant memories for an upcoming task from durable memory.",
    ),
    NemoToolContract(
        name="detect_redundancy",
        suite=NemoToolSuite.MEMORY_MANAGEMENT,
        risk=NemoToolRisk.READ_ONLY,
        phase_access=(ExecutionPhase.REVIEW,),
        purpose="Find likely duplicate or redundant memory atoms for cleanup review.",
    ),
    NemoToolContract(
        name="memory_chronicle",
        suite=NemoToolSuite.MEMORY_MANAGEMENT,
        risk=NemoToolRisk.READ_ONLY,
        phase_access=(ExecutionPhase.REVIEW,),
        purpose="Browse recent durable memories chronologically for audit and continuity.",
    ),
    NemoToolContract(
        name="salience_score",
        suite=NemoToolSuite.MEMORY_MANAGEMENT,
        risk=NemoToolRisk.READ_ONLY,
        phase_access=(ExecutionPhase.PLAN, ExecutionPhase.EXECUTE, ExecutionPhase.REVIEW),
        purpose="Score candidate memory content for expected task relevance.",
    ),
    NemoToolContract(
        name="update_memory",
        suite=NemoToolSuite.MEMORY_MANAGEMENT,
        risk=NemoToolRisk.MEMORY_WRITE,
        phase_access=(ExecutionPhase.REVIEW,),
        purpose="Update curated memory after review resolves stale or incomplete facts.",
    ),
    NemoToolContract(
        name="store_conversation",
        suite=NemoToolSuite.CONVERSATION,
        risk=NemoToolRisk.MEMORY_WRITE,
        phase_access=(ExecutionPhase.PLAN, ExecutionPhase.EXECUTE, ExecutionPhase.REVIEW),
        purpose="Persist meaningful exchanges and phase outcomes for future sessions.",
    ),
    NemoToolContract(
        name="get_recent_context",
        suite=NemoToolSuite.CONVERSATION,
        risk=NemoToolRisk.READ_ONLY,
        phase_access=(ExecutionPhase.REVIEW,),
        purpose="Inspect recent raw conversation only when portfolio context is insufficient.",
    ),
    NemoToolContract(
        name="get_current_time",
        suite=NemoToolSuite.TIME_AND_ENVIRONMENT,
        risk=NemoToolRisk.READ_ONLY,
        phase_access=(ExecutionPhase.PLAN, ExecutionPhase.EXECUTE, ExecutionPhase.REVIEW),
        purpose="Attach reliable timestamps to decisions, evidence, and reminders.",
    ),
    NemoToolContract(
        name="get_environment_info",
        suite=NemoToolSuite.TIME_AND_ENVIRONMENT,
        risk=NemoToolRisk.READ_ONLY,
        phase_access=(ExecutionPhase.PLAN, ExecutionPhase.EXECUTE, ExecutionPhase.REVIEW),
        purpose="Expose local runtime environment facts for agent planning and diagnostics.",
    ),
    NemoToolContract(
        name="get_weather_open_meteo",
        suite=NemoToolSuite.TIME_AND_ENVIRONMENT,
        risk=NemoToolRisk.READ_ONLY,
        phase_access=(ExecutionPhase.PLAN,),
        purpose="Optional environment context for user workflows that require weather.",
    ),
    NemoToolContract(
        name="create_reminder",
        suite=NemoToolSuite.REMINDERS,
        risk=NemoToolRisk.SCHEDULING_WRITE,
        phase_access=(ExecutionPhase.REVIEW,),
        purpose="Create follow-up reminders for unresolved engineering tasks.",
    ),
    NemoToolContract(
        name="get_active_reminders",
        suite=NemoToolSuite.REMINDERS,
        risk=NemoToolRisk.READ_ONLY,
        phase_access=(ExecutionPhase.PLAN, ExecutionPhase.EXECUTE, ExecutionPhase.REVIEW),
        purpose="List active reminders that may affect the current work.",
    ),
    NemoToolContract(
        name="get_completed_reminders",
        suite=NemoToolSuite.REMINDERS,
        risk=NemoToolRisk.READ_ONLY,
        phase_access=(ExecutionPhase.REVIEW,),
        purpose="List completed reminders for audit and continuity.",
    ),
    NemoToolContract(
        name="complete_reminder",
        suite=NemoToolSuite.REMINDERS,
        risk=NemoToolRisk.SCHEDULING_WRITE,
        phase_access=(ExecutionPhase.REVIEW,),
        purpose="Mark a reminder as completed after its follow-up is resolved.",
    ),
    NemoToolContract(
        name="delete_reminder",
        suite=NemoToolSuite.REMINDERS,
        risk=NemoToolRisk.DESTRUCTIVE,
        phase_access=(ExecutionPhase.REVIEW,),
        purpose="Delete a reminder that should no longer appear in future context.",
    ),
    NemoToolContract(
        name="reschedule_reminder",
        suite=NemoToolSuite.REMINDERS,
        risk=NemoToolRisk.SCHEDULING_WRITE,
        phase_access=(ExecutionPhase.REVIEW,),
        purpose="Update the due time for an existing reminder.",
    ),
    NemoToolContract(
        name="intent_anchor",
        suite=NemoToolSuite.REMINDERS,
        risk=NemoToolRisk.MEMORY_WRITE,
        phase_access=(ExecutionPhase.REVIEW,),
        purpose="Persist condition-triggered reminders for future task contexts.",
    ),
    NemoToolContract(
        name="create_appointment",
        suite=NemoToolSuite.APPOINTMENTS,
        risk=NemoToolRisk.SCHEDULING_WRITE,
        phase_access=(ExecutionPhase.REVIEW,),
        purpose="Create scheduled appointments only from explicit user intent.",
    ),
    NemoToolContract(
        name="get_recent_appointments",
        suite=NemoToolSuite.APPOINTMENTS,
        risk=NemoToolRisk.READ_ONLY,
        phase_access=(ExecutionPhase.REVIEW,),
        purpose="List recent appointments for continuity and audit.",
    ),
    NemoToolContract(
        name="get_upcoming_appointments",
        suite=NemoToolSuite.APPOINTMENTS,
        risk=NemoToolRisk.READ_ONLY,
        phase_access=(ExecutionPhase.PLAN, ExecutionPhase.EXECUTE, ExecutionPhase.REVIEW),
        purpose="List upcoming appointments that may affect the current plan.",
    ),
    NemoToolContract(
        name="cancel_appointment",
        suite=NemoToolSuite.APPOINTMENTS,
        risk=NemoToolRisk.SCHEDULING_WRITE,
        phase_access=(ExecutionPhase.REVIEW,),
        purpose="Mark an appointment as cancelled.",
    ),
    NemoToolContract(
        name="complete_appointment",
        suite=NemoToolSuite.APPOINTMENTS,
        risk=NemoToolRisk.SCHEDULING_WRITE,
        phase_access=(ExecutionPhase.REVIEW,),
        purpose="Mark an appointment as completed.",
    ),
    NemoToolContract(
        name="store_architectural_decision",
        suite=NemoToolSuite.MEMORY_MANAGEMENT,
        risk=NemoToolRisk.MEMORY_WRITE,
        phase_access=(ExecutionPhase.PLAN, ExecutionPhase.EXECUTE, ExecutionPhase.REVIEW),
        purpose="Persist structured architectural decisions (e.g. framework choices) for future consistency.",
    ),
    NemoToolContract(
        name="search_memories",
        suite=NemoToolSuite.CONTEXT_ECONOMY,
        risk=NemoToolRisk.READ_ONLY,
        phase_access=(ExecutionPhase.PLAN, ExecutionPhase.EXECUTE, ExecutionPhase.REVIEW),
        purpose="Perform semantic or keyword search across the full memory store for technical evidence.",
    ),
    NemoToolContract(
        name="get_system_health",
        suite=NemoToolSuite.MAINTENANCE,
        risk=NemoToolRisk.READ_ONLY,
        phase_access=(ExecutionPhase.REVIEW,),
        purpose="Inspect NEMO health when memory behavior appears degraded.",
    ),
)


PHASE_PORTFOLIOS: dict[ExecutionPhase, PortfolioContract] = {
    ExecutionPhase.PLAN: PortfolioContract(
        phase=ExecutionPhase.PLAN,
        mode=PortfolioMode.SAFE,
        reads=(
            MemoryAtomType.CORRECTION,
            MemoryAtomType.PREFERENCE,
            MemoryAtomType.PROJECT_FACT,
            MemoryAtomType.OPEN_LOOP,
            MemoryAtomType.SESSION_SUMMARY,
        ),
        writes=(MemoryAtomType.DECISION,),
        expands_evidence=False,
    ),
    ExecutionPhase.EXECUTE: PortfolioContract(
        phase=ExecutionPhase.EXECUTE,
        mode=PortfolioMode.FAST,
        reads=(
            MemoryAtomType.CORRECTION,
            MemoryAtomType.PROJECT_FACT,
            MemoryAtomType.DECISION,
            MemoryAtomType.ARTIFACT_STATE,
        ),
        writes=(MemoryAtomType.ARTIFACT_STATE, MemoryAtomType.EVIDENCE, MemoryAtomType.DECISION),
        expands_evidence=True,
    ),
    ExecutionPhase.REVIEW: PortfolioContract(
        phase=ExecutionPhase.REVIEW,
        mode=PortfolioMode.THOROUGH,
        reads=(
            MemoryAtomType.CORRECTION,
            MemoryAtomType.PREFERENCE,
            MemoryAtomType.PROJECT_FACT,
            MemoryAtomType.DECISION,
            MemoryAtomType.ARTIFACT_STATE,
            MemoryAtomType.EVIDENCE,
        ),
        writes=(MemoryAtomType.SESSION_SUMMARY, MemoryAtomType.DECISION),
        expands_evidence=True,
    ),
}


def default_portfolio_request(task: str, phase: ExecutionPhase, topic: str) -> PortfolioRequest:
    contract = PHASE_PORTFOLIOS[phase]
    budget = {
        ExecutionPhase.PLAN: 2000,
        ExecutionPhase.EXECUTE: 1500,
        ExecutionPhase.REVIEW: 2500,
    }[phase]
    return PortfolioRequest(task=task, phase=phase, topic=topic, token_budget=budget, mode=contract.mode)


def nemo_tools_for_phase(phase: ExecutionPhase) -> tuple[NemoToolContract, ...]:
    return tuple(tool for tool in NEMO_TOOL_REGISTRY if phase in tool.phase_access)


def nemo_tools_by_suite(suite: NemoToolSuite) -> tuple[NemoToolContract, ...]:
    return tuple(tool for tool in NEMO_TOOL_REGISTRY if tool.suite == suite)
