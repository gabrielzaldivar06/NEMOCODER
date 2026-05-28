from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from nemo_coding_platform.core.engine_interface import EngineProvider, MutationRequest, MutationResult, apply_mutation_request
from nemo_coding_platform.core.mutations import QualityMutationEngine
from nemo_coding_platform.core.repair import RepairBudget, RepairPlan
from nemo_coding_platform.core.token_counter import count_tokens
from nemo_coding_platform.core.validation import ValidationSuiteResult, simulate_validation
from nemo_coding_platform.core.context_compaction import compact_context, prune_tool_output
from nemo_coding_platform.core.post_mutation_lint import lint_changed_files, format_lint_evidence
from nemo_coding_platform.core.nemo_patterns import nemo_before_attempt, nemo_after_failure, nemo_after_success
from nemo_coding_platform.core.product import AutonomyLevel


@dataclass(frozen=True, slots=True)
class RepairRunResult:
    plan: RepairPlan
    mutation_results: tuple[MutationResult, ...]
    validation: ValidationSuiteResult
    stop_reason: str = ""
    tokens_consumed: int = 0
    best_score: float = 0.0


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


def _safe_critique(critique_fn: Callable[[str, str, str], float], objective: str, diff: str, validation_summary: str) -> float:
    try:
        return max(0.0, min(10.0, float(critique_fn(objective, diff, validation_summary))))
    except Exception:
        return 0.0


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
    evidence_compactor: Callable[[str, int], tuple[str, str | None]] | None = None,
    nemo_adapter: object = None,
    task_id: str = "",
    critique_fn: Callable[[str, str, str], float] | None = None,
    quality_threshold: float = 0.0,
    autonomy_level: AutonomyLevel | None = None,
) -> RepairRunResult:
    plan = RepairPlan(budget)
    validation = initial_validation
    mutations: list[MutationResult] = []
    stop_reason = ""
    lint_evidence = ""
    best_score: float = 0.0
    start_time = time.time()
    _tokens_consumed = 0
    _last_token_source = "estimated"
    while plan.can_record_attempt():
        _repair_mode = not validation.passed
        _quality_mode = (
            validation.passed
            and quality_threshold > 0
            and critique_fn is not None
            and 0 < best_score < quality_threshold
        )
        if not _repair_mode and not _quality_mode:
            break
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

        if _quality_mode:
            plan = plan.next_attempt("quality_below_threshold", f"score={best_score:.1f}<{quality_threshold:.1f}")
            raw_validation_evidence = (
                f"quality_score={best_score:.1f} quality_target={quality_threshold:.1f} VALIDATION_PASSED=true"
            )
            validation_evidence = raw_validation_evidence
        else:
            failed = ", ".join(result.command.command for result in validation.results if not result.passed) or "validation"
            plan = plan.next_attempt("validation_failed", f"repair failed command(s): {failed}")
            raw_validation_evidence = format_validation_evidence(validation, attempt_number)
            validation_evidence = prune_tool_output(raw_validation_evidence, max_chars=1500)
            if evidence_compactor and len(raw_validation_evidence) > 1500:
                compact_claim, evidence_handle = evidence_compactor(raw_validation_evidence, attempt_number)
                if compact_claim:
                    evidence_lines = [
                        f"repair_attempt={attempt_number}",
                        validation.summary(),
                        f"compacted_validation_claim={compact_claim}",
                    ]
                    if evidence_handle:
                        evidence_lines.append(f"evidence_handle={evidence_handle}")
                    validation_evidence = "\n".join(evidence_lines)
        previous_diff = mutations[-1].diff_artifact if mutations else base_request.previous_diff

        if _repair_mode:
            # --- Loop Detection (repair mode only) ---
            if len(mutations) >= 2 and _diffs_are_identical(mutations[-1].diff_artifact, mutations[-2].diff_artifact):
                stop_reason = "repair_loop_detected"
                break
            if len(mutations) >= 1 and not mutations[-1].diff_artifact.strip():
                if len(mutations) >= 2 and not mutations[-2].diff_artifact.strip():
                    stop_reason = "repair_loop_detected"
                    break

        reminder_prefix = ""
        noop_attempt = not mutations[-1].changed_files if mutations else False

        if _quality_mode:
            nemo_query = f"quality improvement: score={best_score:.1f} objective={base_request.objective[:80]}"
        else:
            # Build a precise NEMO query: include the actual error text so semantic
            # search retrieves memories for this specific error, not just the objective.
            first_failed_output = next(
                (r.output for r in validation.results if not r.passed and r.output),
                "",
            )
            error_snippet = first_failed_output.strip()[:600] if first_failed_output else ""
            nemo_query = f"repair: {failed} — {error_snippet}" if error_snippet else f"repair failure: {failed}"
        nemo_snippet = nemo_before_attempt(
            nemo_adapter,
            query=nemo_query,
            tags=("repair_failure",),
        )

        if _quality_mode:
            repair_evidence_lines = [
                "# Quality Improvement Context",
                validation_evidence,
                *([f"", "# Current Diff", mutations[-1].diff_artifact[:1200]] if mutations else []),
            ]
        else:
            repair_evidence_lines = [
                "# Repair Evidence",
                validation_evidence,
            ]
            if lint_evidence:
                repair_evidence_lines += ["", lint_evidence]
            if previous_diff:
                repair_evidence_lines += ["", "# Previous Diff", previous_diff]

        repair_context = "\n".join(
            item
            for item in (
                nemo_snippet,
                base_request.context,
                "",
                *repair_evidence_lines,
            )
            if item is not None
        )
        
        # --- Context Compaction (inspired by opencode) ---
        repair_context = compact_context(repair_context)

        # Adaptive timeout: give later repair attempts up to 1.5× base timeout,
        # capped by remaining time budget. First attempt uses base timeout (scale=1.0).
        _attempt_scale = min(1.5, 1.0 + (len(plan.attempts) - 1) * 0.1)
        _scaled_timeout = base_request.timeout_seconds * _attempt_scale
        remaining_timeout_seconds = _scaled_timeout
        if time_limit_seconds is not None:
            remaining_budget = time_limit_seconds - (time.time() - start_time)
            if remaining_budget <= 0:
                stop_reason = "repair_time_budget_exhausted"
                break
            remaining_timeout_seconds = min(_scaled_timeout, max(1.0, remaining_budget))
        
        if _quality_mode:
            repair_objective = (
                f"Improve code quality (current: {best_score:.1f}/10, target: {quality_threshold:.1f}/10) "
                f"for: {base_request.objective}"
            )
        elif noop_attempt:
            repair_objective = f"No files were written. Write the required files now for: {base_request.objective}"
        else:
            repair_objective = f"Repair validation failure for: {base_request.objective}"

        repair_request = MutationRequest(
            objective=repair_objective,
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
            repo_map=base_request.repo_map,
        )
        mutation = apply_mutation_request(engine, provider, repair_request)
        mutations.append(mutation)
        
        # --- Post-Mutation Linting ---
        lint_results = lint_changed_files(mutation.changed_files, base_request.runtime_path)
        lint_evidence = format_lint_evidence(lint_results)
        if _repair_mode:
            nemo_after_failure(nemo_adapter, raw_validation_evidence, task_id, attempt_number)

        if not mutation.changed_files and not mutation.applied_files:
            if mutation.returncode is None and "timed out" in mutation.stderr:
                stop_reason = "engine_timeout"
            else:
                stop_reason = "repair_noop"
            break
        if autonomy_level == AutonomyLevel.MANUAL and len(plan.attempts) >= 1:
            stop_reason = "requires_human_supervision"
            break
        # Prefer provider-reported token usage; fallback to tiktoken (with smarter
        # heuristic as last resort). chars/4 under-counts reasoning models by 2-3×
        # because reasoning_content is invisible but still billed.
        if mutation.token_usage and mutation.token_usage.total_tokens > 0:
            _tokens_consumed += mutation.token_usage.total_tokens
            _last_token_source = "real"
        else:
            last_diff = mutations[-1].diff_artifact if mutations else ""
            _tokens_consumed += count_tokens(repair_context) + count_tokens(last_diff)
            _last_token_source = "estimated"
        validation = validator() if validator else simulate_validation(commands, fail_validation)
        if validation.passed and critique_fn is not None:
            _score = _safe_critique(
                critique_fn,
                base_request.objective,
                mutations[-1].diff_artifact if mutations else "",
                validation.summary(),
            )
            best_score = max(best_score, _score)
    if not validation.passed and not stop_reason and not plan.can_record_attempt():
        stop_reason = "repair_budget_exhausted"
    # Evaluate quality when validation passed but critique wasn't called inside the loop
    # (e.g. validation was already passing on entry, so the while body never ran).
    if validation.passed and critique_fn is not None and best_score == 0.0:
        best_score = _safe_critique(
            critique_fn,
            base_request.objective,
            mutations[-1].diff_artifact if mutations else "",
            validation.summary(),
        )
    if validation.passed and quality_threshold > 0 and 0 < best_score < quality_threshold:
        stop_reason = "quality_below_threshold"
    if validation.passed:
        if mutations:
            last_failed_cmd = ", ".join(result.command.command for result in initial_validation.results if not result.passed) or "validation"
            diff_snippet = mutations[-1].diff_artifact[:500]
            outcome_msg = f"fixed: {base_request.objective}. error_was: {last_failed_cmd}. diff: {diff_snippet}"
        else:
            outcome_msg = f"passed_first_try: {base_request.objective}"
        nemo_after_success(nemo_adapter, outcome_msg, task_id)
    return RepairRunResult(plan, tuple(mutations), validation, stop_reason, tokens_consumed=_tokens_consumed, best_score=best_score)

