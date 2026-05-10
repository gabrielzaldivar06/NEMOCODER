"""Context assembler: budget-aware, typed context packet builder.

Replaces the ad-hoc string assembly that was inline in mission_control_server.py.
Produces a ContextPacket with:
  - text: compact string ready for LLM prompt injection
  - evidence_handles: typed handles for on-demand expansion
  - sections: ordered list of section names included
  - chars_used: actual character count of text

The assembler respects a max_chars budget and trims aggressively when needed,
always keeping the most critical sections (task objective, corrections/portfolio
context).

Section priority (highest first):
  1. task_objective — task ID + objective (always included, very short)
  2. nemo_context   — NEMO portfolio context (trimmed to fit)
  3. cognitive_preload — reflexions and continuity hints
  4. run_metadata   — run state, mergeable flag
  5. changed_files  — list of changed files
  6. risk_flags     — active risk flags (dropped last)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nemo_coding_platform.core.evidence_handle import EvidenceHandle, parse_evidence_handles


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------

@dataclass
class ContextPacket:
    """Assembled context ready for LLM injection."""

    text: str
    sections: list[str] = field(default_factory=list)
    evidence_handles: list[EvidenceHandle] = field(default_factory=list)
    chars_used: int = 0
    chars_budget: int = 0

    @property
    def utilization(self) -> float:
        """Fraction of budget used (0.0–1.0+)."""
        if self.chars_budget <= 0:
            return 0.0
        return self.chars_used / self.chars_budget

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "sections": self.sections,
            "evidence_handles": [h.as_dict() for h in self.evidence_handles],
            "chars_used": self.chars_used,
            "chars_budget": self.chars_budget,
            "utilization": round(self.utilization, 3),
        }


# ---------------------------------------------------------------------------
# Assembler
# ---------------------------------------------------------------------------

_NO_RUN_TEXT = (
    "No run is selected. Answer the user's chat request and propose a safe next action."
)


def assemble_context(
    *,
    task: dict[str, Any] | None = None,
    run: dict[str, Any] | None = None,
    portfolio: dict[str, Any] | None = None,
    cognitive_preload: str = "",
    changed_files: tuple[str, ...] = (),
    risk_flags: tuple[str, ...] = (),
    mergeable: bool = False,
    max_chars: int = 2400,
) -> ContextPacket:
    """Assemble a budget-aware context packet for LLM injection.

    Args:
        task:            Task dict (id, objective/title fields).
        run:             Run dict (id field).
        portfolio:       NEMO portfolio payload (context, evidence_handles).
        cognitive_preload: Pre-formatted reflexions / continuity string.
        changed_files:   Tuple of changed file paths.
        risk_flags:      Tuple of active risk flag strings.
        mergeable:       Whether the current run is mergeable.
        max_chars:       Hard character budget for the output text.

    Returns:
        ContextPacket with text, sections, evidence_handles, and budget metadata.
    """
    evidence_handles: list[EvidenceHandle] = []

    # --- No-run fast path ---
    if not task and not run and not portfolio:
        base = _NO_RUN_TEXT
        if cognitive_preload.strip():
            base = base + "\n\n" + cognitive_preload.strip()
        trimmed = _trim(base, max_chars)
        return ContextPacket(
            text=trimmed,
            sections=["no_run"],
            evidence_handles=[],
            chars_used=len(trimmed),
            chars_budget=max_chars,
        )

    task = task or {}
    run = run or {}
    portfolio = portfolio or {}

    # Parse evidence handles from portfolio payload
    evidence_handles = parse_evidence_handles(
        portfolio.get("evidence_handles"), source="nemo_portfolio"
    )

    # --- Build sections in priority order ---
    # Section 1: task objective (short, always fits)
    task_id = str(task.get("id") or "unknown")
    run_id = str(run.get("id") or "unknown")
    objective = str(task.get("objective") or task.get("title") or "Untitled")
    section_task = f"Selected task: {task_id} / {run_id}\nObjective: {objective}"

    # Section 2: NEMO context (portfolio context string, trimmed)
    nemo_ctx_raw = str(portfolio.get("context") or "").strip()

    # Section 3: cognitive preload
    preload = cognitive_preload.strip()

    # Section 4: run metadata
    section_meta = f"Mergeable: {mergeable}"

    # Section 5: changed files
    section_files = f"Changed files: {', '.join(changed_files) or 'none'}"

    # Section 6: risk flags
    section_risk = f"Risk flags: {', '.join(risk_flags) or 'none'}"

    # --- Budget allocation ---
    # Reserve chars for mandatory sections first, then fill the rest.
    mandatory = "\n".join([section_task, section_meta, section_files, section_risk])
    remaining = max(0, max_chars - len(mandatory) - 2)  # 2 for "\n\n" separators

    # Split remaining between nemo_context and preload (60/40)
    nemo_budget = int(remaining * 0.6)
    preload_budget = remaining - nemo_budget

    nemo_ctx = _trim(nemo_ctx_raw, nemo_budget) if nemo_ctx_raw else ""
    preload_trimmed = _trim(preload, preload_budget) if preload else ""

    # --- Assemble final text ---
    parts: list[tuple[str, str]] = [
        ("task_objective", section_task),
        ("nemo_context", f"NEMO context:\n{nemo_ctx}" if nemo_ctx else ""),
        ("cognitive_preload", f"Cognitive preload:\n{preload_trimmed}" if preload_trimmed else ""),
        ("run_metadata", section_meta),
        ("changed_files", section_files),
        ("risk_flags", section_risk),
    ]

    included_parts: list[str] = []
    sections: list[str] = []
    for name, content in parts:
        if content.strip():
            included_parts.append(content)
            sections.append(name)

    text = "\n".join(included_parts)
    text = _trim(text, max_chars)

    return ContextPacket(
        text=text,
        sections=sections,
        evidence_handles=evidence_handles,
        chars_used=len(text),
        chars_budget=max_chars,
    )


def _trim(text: str, max_chars: int) -> str:
    """Trim text to max_chars, preserving the tail (most recent content)."""
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]
