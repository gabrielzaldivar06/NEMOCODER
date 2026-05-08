"""Reflexion Loop — structured post-task reflection for NemoCode self-improvement.

Based on the Reflexion framework (Shinn & Labash 2023): after every task the agent
generates a typed, evidence-backed reflection and persists it in NEMO so future
tasks can retrieve it via `anticipate` before starting similar work.

Failure reflexions are stored as `create_correction` (high-priority retrieval).
Success reflexions are stored via `cognitive_ingest` as procedural memory.

No LLM is required — all entries are produced from deterministic heuristics over
the run result, keeping the system local-first, fast, and testable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nemo_coding_platform.core.nemo_adapter import InMemoryNemoAdapter, PersistentNemoAdapter
from nemo_coding_platform.core.nemo_lifecycle import NemoLifecyclePhase

if TYPE_CHECKING:
    from nemo_coding_platform.core.headless_runner import HeadlessRunResult


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReflexionEntry:
    """A typed, heuristic-derived reflection produced after one self-mod run."""

    task_type: str
    objective: str
    outcome: str  # "passed" | "failed" | "partial"

    # What we learned
    what_worked: str
    what_failed: str
    root_cause: str
    corrective_action: str

    # Evidence
    files_touched: tuple[str, ...]
    tests_broken: tuple[str, ...]
    validation_summary: str
    repair_attempts_used: int
    stop_reason: str

    # Metadata
    confidence: float = 0.75  # heuristic-based entries start at 0.75
    task_id: str = ""
    run_id: str = ""

    def as_correction_text(self) -> str:
        """Format as a `create_correction` wrong→correct pair."""
        return (
            f"[reflexion] task_type={self.task_type} outcome={self.outcome}\n"
            f"what_failed={self.what_failed}\n"
            f"root_cause={self.root_cause}\n"
            f"corrective_action={self.corrective_action}\n"
            f"files_touched=[{', '.join(self.files_touched)}]\n"
            f"confidence={self.confidence:.2f}"
        )

    def as_procedural_text(self) -> str:
        """Format as a `cognitive_ingest` procedural memory entry."""
        return (
            f"[reflexion] task_type={self.task_type} outcome={self.outcome}\n"
            f"what_worked={self.what_worked}\n"
            f"files_touched=[{', '.join(self.files_touched)}]\n"
            f"validation_summary={self.validation_summary}\n"
            f"confidence={self.confidence:.2f}"
        )

    def tags(self) -> tuple[str, ...]:
        base = ("reflexion", self.task_type, self.outcome)
        if self.task_id:
            base = base + (self.task_id,)
        if self.run_id:
            base = base + (self.run_id,)
        return base


# ---------------------------------------------------------------------------
# Heuristic extraction helpers
# ---------------------------------------------------------------------------


def _extract_failed_commands(result: HeadlessRunResult) -> tuple[str, ...]:
    """Return the commands that failed validation, if any."""
    validation = result.validation
    if validation is None:
        return ()
    return tuple(
        r.command.command
        for r in validation.results
        if not r.passed
    )


def _extract_root_cause(result: HeadlessRunResult) -> str:
    """Heuristic root-cause extraction from validation output."""
    validation = result.validation
    if validation is None:
        return "unknown — no validation data"

    snippets: list[str] = []
    for r in result.validation.results:
        if not r.passed and r.output:
            # First 300 chars of the first failing output
            snippet = r.output[:300].replace("\n", " ").strip()
            if snippet:
                snippets.append(snippet)
                break

    if snippets:
        # Extract the first error-looking line
        first = snippets[0]
        for prefix in ("Error", "FAILED", "assert", "Exception", "TypeError", "AttributeError"):
            match = re.search(rf"{re.escape(prefix)}[^\n]{{0,200}}", first, re.IGNORECASE)
            if match:
                return match.group(0)[:200]
        return first[:200]

    return f"validation_summary={validation.summary()}"


def _extract_what_worked(result: HeadlessRunResult) -> str:
    """Describe what succeeded in a passing run."""
    files = list(result.effective_changed_files or ())
    passed_cmds = []
    if result.validation:
        passed_cmds = [r.command.command for r in result.validation.results if r.passed]
    parts = []
    if files:
        parts.append(f"changed {len(files)} file(s): {', '.join(files[:5])}")
    if passed_cmds:
        parts.append(f"all validation passed: {', '.join(passed_cmds[:3])}")
    return "; ".join(parts) or "run completed without recorded failures"


def _repair_stop_reason(result: HeadlessRunResult) -> tuple[str, int]:
    """Return (stop_reason, repair_attempts_used) from the run."""
    if result.repair_plan is None:
        return ("no_repair_needed", 0)
    attempts = len(result.repair_plan.attempts)
    # Look for the stop reason in the last attempt action text if available
    if result.repair_plan.attempts:
        last = result.repair_plan.attempts[-1]
        return (getattr(last, "action", "unknown"), attempts)
    return ("unknown", attempts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_reflexion(
    result: HeadlessRunResult,
    task_type: str,
    objective: str,
) -> ReflexionEntry:
    """Build a structured ReflexionEntry from a HeadlessRunResult.

    All logic is heuristic — no LLM call is made.
    """
    validation = result.validation
    passed = validation is not None and validation.passed
    outcome = "passed" if passed else "failed"

    files_touched = tuple(result.effective_changed_files or ())
    failed_commands = _extract_failed_commands(result)
    tests_broken = tuple(
        c for c in failed_commands
        if "test" in c.lower() or "pytest" in c.lower()
    )
    stop_reason, repair_attempts = _repair_stop_reason(result)

    if passed:
        what_worked = _extract_what_worked(result)
        what_failed = "none — validation passed"
        root_cause = "n/a"
        corrective_action = "no correction needed; reuse this approach for similar tasks"
        confidence = 0.9
    else:
        what_worked = (
            f"{len(files_touched)} file(s) mutated"
            if files_touched
            else "no files mutated"
        )
        what_failed = (
            f"validation failed on: {', '.join(failed_commands)}"
            if failed_commands
            else "validation did not pass (no specific command recorded)"
        )
        root_cause = _extract_root_cause(result)
        corrective_action = (
            f"Before attempting this task again: retrieve past reflexions for "
            f"task_type={task_type} and avoid pattern: {root_cause[:120]}"
        )
        confidence = max(0.5, 0.85 - 0.1 * repair_attempts)

    val_summary = validation.summary() if validation else "no validation"

    return ReflexionEntry(
        task_type=task_type,
        objective=objective,
        outcome=outcome,
        what_worked=what_worked,
        what_failed=what_failed,
        root_cause=root_cause,
        corrective_action=corrective_action,
        files_touched=files_touched,
        tests_broken=tests_broken,
        validation_summary=val_summary,
        repair_attempts_used=repair_attempts,
        stop_reason=stop_reason,
        confidence=confidence,
        task_id=result.task.id if result.task else "",
        run_id=result.run.id if result.run else "",
    )


def persist_reflexion(
    adapter: PersistentNemoAdapter | InMemoryNemoAdapter,
    entry: ReflexionEntry,
) -> tuple[PersistentNemoAdapter | InMemoryNemoAdapter, dict[str, Any]]:
    """Persist a ReflexionEntry to NEMO.

    Failures → ``create_correction`` (importance=9, maximum retrieval priority)
    Successes → ``cognitive_ingest`` as procedural memory (importance=7)

    Returns the (possibly-updated) adapter and the result payload.
    """
    if entry.outcome == "failed":
        adapter, result = adapter.call(
            NemoLifecyclePhase.REVIEW,
            "create_correction",
            wrong_assumption=(
                f"task_type={entry.task_type} objective='{entry.objective[:120]}' "
                f"would complete without the following failure"
            ),
            correct_answer=entry.as_correction_text(),
            context=(
                f"repair_attempts_used={entry.repair_attempts_used} "
                f"stop_reason={entry.stop_reason} "
                f"files_touched=[{', '.join(entry.files_touched)}]"
            ),
            tags=entry.tags(),
        )
    else:
        adapter, result = adapter.call(
            NemoLifecyclePhase.REVIEW,
            "cognitive_ingest",
            content=entry.as_procedural_text(),
            memory_type="procedural",
            tags=entry.tags(),
            context=(
                f"task_type={entry.task_type} objective='{entry.objective[:120]}' "
                f"outcome={entry.outcome} confidence={entry.confidence:.2f}"
            ),
        )
    return adapter, result.payload
