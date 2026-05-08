from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from nemo_coding_platform.core.engine_interface import EngineProvider, MutationRequest, MutationResult, apply_mutation_request
from nemo_coding_platform.core.mutations import QualityMutationEngine
from nemo_coding_platform.core.repair import RepairBudget, RepairPlan
from nemo_coding_platform.core.validation import ValidationSuiteResult, simulate_validation
from nemo_coding_platform.core.context_compaction import compact_context
from nemo_coding_platform.core.post_mutation_lint import lint_changed_files, format_lint_evidence


@dataclass(frozen=True, slots=True)
class RepairRunResult:
    plan: RepairPlan
    mutation_results: tuple[MutationResult, ...]
    validation: ValidationSuiteResult
    stop_reason: str = ""
    tokens_consumed: int = 0


def format_validation_evidence(validation: ValidationSuiteResult, attempt: int) -> str:
    lines = [f"repair_attempt={attempt}", validation.summary()]
    for index, result in enumerate(validation.results, start=1):
        if result.passed and result.command.required:
            continue
        lines.extend(
            (
                f"command[{index}]={result.command.command}",
                f"status[{index}]={result.status.value}",
                f"returncode[{index}]={result.returncode}",
                f"output[{index}]={result.output or '<empty>'}",
            )
        )
    return "\n".join(lines)


def _diffs_are_identical(a: str, b: str) -> bool:
    """Return True when two diffs represent the same change (or are both empty)."""
    return a.strip() == b.strip()


def run_repair_loop(
    initial_validation: ValidationSuiteResult,
    commands: tuple[str, ...],
    fail_validation: tuple[str, ...],
    budget: RepairBudget,
    engine: QualityMutationEngine,
    provider: EngineProvider,
    base_request: MutationRequest,
    validator: Callable[[], ValidationSuiteResult] | None = None,
    todo_reminder: str = "",
    time_limit_seconds: float | None = None,
    on_attempt: Callable[[int, float], None] | None = None,
    token_budget: int | None = None,
    attempt_offset: int = 0,
) -> RepairRunResult:
    plan = RepairPlan(budget)
    validation = initial_validation
    mutations: list[MutationResult] = []
    stop_reason = ""
    lint_evidence = ""
    start_time = time.time()
    _tokens_consumed = 0
    _CHARS_PER_TOKEN = 4  # standard heuristic
    _last_token_source = "estimated"
    while not validation.passed and plan.can_record_attempt():
        attempt_number = attempt_offset + len(plan.attempts) + 1
        elapsed_seconds = time.time() - start_time
        if on_attempt:
            on_attempt(attempt_number, elapsed_seconds)

        if time_limit_seconds is not None and elapsed_seconds >= time_limit_seconds:
            stop_reason = "repair_time_budget_exhausted"
            break

        if token_budget is not None and _tokens_consumed >= token_budget:
            stop_reason = (
                "token_budget_exhausted_real"
                if _last_token_source == "real"
                else "token_budget_exhausted_estimated"
            )
            break

        failed = ", ".join(result.command.command for result in validation.results if not result.passed) or "validation"
        plan = plan.next_attempt("validation_failed", f"repair failed command(s): {failed}")
        validation_evidence = format_validation_evidence(validation, attempt_number)
        previous_diff = mutations[-1].diff_artifact if mutations else base_request.previous_diff

        # --- Loop Detection (inspired by deer-flow loop_detection_middleware) ---
        # If the most recent repair produced the same diff as the one before it,
        # stop immediately instead of exhausting the budget.
        if len(mutations) >= 2 and _diffs_are_identical(mutations[-1].diff_artifact, mutations[-2].diff_artifact):
            stop_reason = "repair_loop_detected"
            break
        # Also stop if the last repair was a no-op and we've seen it before.
        if len(mutations) >= 1 and not mutations[-1].diff_artifact.strip():
            if len(mutations) >= 2 and not mutations[-2].diff_artifact.strip():
                stop_reason = "repair_loop_detected"
                break

        # Prepend the todo reminder to the context on the first attempt only.
        reminder_prefix = (todo_reminder + "\n\n") if todo_reminder and attempt_number == 1 else ""

        repair_context = "\n".join(
            item
            for item in (
                reminder_prefix + base_request.context,
                "",
                "# Repair Evidence",
                validation_evidence,
                "",
                lint_evidence,
                "",
                "# Previous Diff",
                previous_diff or "No previous diff captured.",
            )
            if item is not None
        )
        
        # --- Context Compaction (inspired by opencode) ---
        repair_context = compact_context(repair_context)

        remaining_timeout_seconds = base_request.timeout_seconds
        if time_limit_seconds is not None:
            remaining_budget = time_limit_seconds - (time.time() - start_time)
            if remaining_budget <= 0:
                stop_reason = "repair_time_budget_exhausted"
                break
            remaining_timeout_seconds = min(base_request.timeout_seconds, max(1.0, remaining_budget))
        
        repair_request = MutationRequest(
            objective=f"Repair validation failure for: {base_request.objective}",
            spec_path=base_request.spec_path,
            acceptance_criteria=base_request.acceptance_criteria,
            context=repair_context,
            provider_mode=base_request.provider_mode,
            repo_path=base_request.repo_path,
            runtime_path=base_request.runtime_path,
            target_files=base_request.target_files,
            model_profile=base_request.model_profile,
            validation_output=validation_evidence,
            timeout_seconds=remaining_timeout_seconds,
            repair_attempt=attempt_number,
            previous_diff=previous_diff,
            skill_prompt=base_request.skill_prompt,
        )
        mutation = apply_mutation_request(engine, provider, repair_request)
        mutations.append(mutation)
        
        # --- Post-Mutation Linting (inspired by aider) ---
        lint_results = lint_changed_files(mutation.changed_files, base_request.runtime_path)
        lint_evidence = format_lint_evidence(lint_results)
        
        if not mutation.changed_files and not mutation.applied_files:
            stop_reason = "repair_noop"
            break
        # Prefer provider-reported token usage; fallback to heuristic estimate.
        if mutation.token_usage and mutation.token_usage.total_tokens > 0:
            _tokens_consumed += mutation.token_usage.total_tokens
            _last_token_source = "real"
        else:
            last_diff = mutations[-1].diff_artifact if mutations else ""
            _tokens_consumed += (len(repair_context) + len(last_diff)) // _CHARS_PER_TOKEN
            _last_token_source = "estimated"
        validation = validator() if validator else simulate_validation(commands, fail_validation)
    if not validation.passed and not stop_reason and not plan.can_record_attempt():
        stop_reason = "repair_budget_exhausted"
    return RepairRunResult(plan, tuple(mutations), validation, stop_reason, tokens_consumed=_tokens_consumed)

