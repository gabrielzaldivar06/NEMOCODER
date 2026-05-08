from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from aider.nemo_platform.phases import AgentPhase


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
    phases: tuple[AgentPhase, ...]
    purpose: str


NEMO_TOOLS: tuple[NemoToolContract, ...] = (
    NemoToolContract(
        "prime_context",
        NemoToolSuite.STARTUP_CONTEXT,
        NemoToolRisk.READ_ONLY,
        (AgentPhase.PLAN, AgentPhase.BUILD, AgentPhase.REVIEW),
        "Load high-priority continuity before agent work.",
    ),
    NemoToolContract(
        "context_bootstrap",
        NemoToolSuite.STARTUP_CONTEXT,
        NemoToolRisk.READ_ONLY,
        (AgentPhase.PLAN,),
        "Load startup context plus a dry-run context portfolio.",
    ),
    NemoToolContract(
        "build_context_portfolio",
        NemoToolSuite.CONTEXT_ECONOMY,
        NemoToolRisk.READ_ONLY,
        (AgentPhase.PLAN, AgentPhase.BUILD, AgentPhase.REVIEW),
        "Compile bounded NEMO context for the current phase.",
    ),
    NemoToolContract(
        "expand_context_evidence",
        NemoToolSuite.CONTEXT_ECONOMY,
        NemoToolRisk.READ_ONLY,
        (AgentPhase.BUILD, AgentPhase.REVIEW),
        "Recover large evidence referenced by a portfolio.",
    ),
    NemoToolContract(
        "record_context_feedback",
        NemoToolSuite.CONTEXT_ECONOMY,
        NemoToolRisk.MEMORY_WRITE,
        (AgentPhase.PLAN, AgentPhase.BUILD, AgentPhase.REVIEW),
        "Update NEMO utility signals after use.",
    ),
    NemoToolContract(
        "get_context_portfolio_stats",
        NemoToolSuite.CONTEXT_ECONOMY,
        NemoToolRisk.READ_ONLY,
        (AgentPhase.REVIEW,),
        "Inspect context portfolio health and token savings.",
    ),
    NemoToolContract(
        "compress_context_artifact",
        NemoToolSuite.CONTEXT_ECONOMY,
        NemoToolRisk.MEMORY_WRITE,
        (AgentPhase.BUILD, AgentPhase.REVIEW),
        "Compact large outputs while preserving recoverable evidence.",
    ),
    NemoToolContract(
        "create_correction",
        NemoToolSuite.MEMORY_MANAGEMENT,
        NemoToolRisk.MEMORY_WRITE,
        (AgentPhase.PLAN, AgentPhase.BUILD, AgentPhase.REVIEW),
        "Persist user corrections immediately.",
    ),
    NemoToolContract(
        "update_memory",
        NemoToolSuite.MEMORY_MANAGEMENT,
        NemoToolRisk.MEMORY_WRITE,
        (AgentPhase.REVIEW,),
        "Update curated memory after review resolves stale facts.",
    ),
    NemoToolContract(
        "store_conversation",
        NemoToolSuite.CONVERSATION,
        NemoToolRisk.MEMORY_WRITE,
        (AgentPhase.PLAN, AgentPhase.BUILD, AgentPhase.REVIEW),
        "Persist meaningful task exchanges and outcomes.",
    ),
    NemoToolContract(
        "get_recent_context",
        NemoToolSuite.CONVERSATION,
        NemoToolRisk.READ_ONLY,
        (AgentPhase.REVIEW,),
        "Inspect recent raw conversation when compact context is insufficient.",
    ),
    NemoToolContract(
        "get_current_time",
        NemoToolSuite.TIME_AND_ENVIRONMENT,
        NemoToolRisk.READ_ONLY,
        (AgentPhase.PLAN, AgentPhase.BUILD, AgentPhase.REVIEW),
        "Attach reliable timestamps to decisions and evidence.",
    ),
    NemoToolContract(
        "get_weather_open_meteo",
        NemoToolSuite.TIME_AND_ENVIRONMENT,
        NemoToolRisk.READ_ONLY,
        (AgentPhase.PLAN,),
        "Optional environment context for user workflows that need weather.",
    ),
    NemoToolContract(
        "create_reminder",
        NemoToolSuite.REMINDERS,
        NemoToolRisk.SCHEDULING_WRITE,
        (AgentPhase.REVIEW,),
        "Create explicit follow-up reminders after review.",
    ),
    NemoToolContract(
        "intent_anchor",
        NemoToolSuite.REMINDERS,
        NemoToolRisk.MEMORY_WRITE,
        (AgentPhase.REVIEW,),
        "Persist condition-triggered future reminders.",
    ),
    NemoToolContract(
        "create_appointment",
        NemoToolSuite.APPOINTMENTS,
        NemoToolRisk.SCHEDULING_WRITE,
        (AgentPhase.REVIEW,),
        "Create explicit appointments only after user intent is captured.",
    ),
    NemoToolContract(
        "store_architectural_decision",
        NemoToolSuite.MEMORY_MANAGEMENT,
        NemoToolRisk.MEMORY_WRITE,
        (AgentPhase.PLAN, AgentPhase.BUILD, AgentPhase.REVIEW),
        "Persist structured architectural decisions (e.g. framework choices) for future consistency.",
    ),
    NemoToolContract(
        "search_memories",
        NemoToolSuite.CONTEXT_ECONOMY,
        NemoToolRisk.READ_ONLY,
        (AgentPhase.PLAN, AgentPhase.BUILD, AgentPhase.REVIEW),
        "Perform semantic or keyword search across the full memory store for technical evidence.",
    ),
    NemoToolContract(
        "get_system_health",
        NemoToolSuite.MAINTENANCE,
        NemoToolRisk.READ_ONLY,
        (AgentPhase.REVIEW,),
        "Inspect NEMO health when memory behavior is suspicious.",
    ),
)


def nemo_tools_for_phase(phase: AgentPhase) -> tuple[NemoToolContract, ...]:
    return tuple(tool for tool in NEMO_TOOLS if phase in tool.phases)