from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from nemo_coding_platform.core.aider_interface import AiderProvider, MutationRequest, MutationResult, apply_mutation_request
from nemo_coding_platform.core.mutations import QualityMutationEngine
from nemo_coding_platform.core.repair import RepairBudget, RepairPlan
from nemo_coding_platform.core.validation import ValidationSuiteResult, simulate_validation


@dataclass(frozen=True, slots=True)
class RepairRunResult:
    plan: RepairPlan
    mutation_results: tuple[MutationResult, ...]
    validation: ValidationSuiteResult
    stop_reason: str = ""


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


def run_repair_loop(
    initial_validation: ValidationSuiteResult,
    commands: tuple[str, ...],
    fail_validation: tuple[str, ...],
    budget: RepairBudget,
    engine: QualityMutationEngine,
    provider: AiderProvider,
    base_request: MutationRequest,
    validator: Callable[[], ValidationSuiteResult] | None = None,
) -> RepairRunResult:
    plan = RepairPlan(budget)
    validation = initial_validation
    mutations: list[MutationResult] = []
    stop_reason = ""
    while not validation.passed and plan.can_record_attempt():
        attempt_number = len(plan.attempts) + 1
        failed = ", ".join(result.command.command for result in validation.results if not result.passed) or "validation"
        plan = plan.next_attempt("validation_failed", f"repair failed command(s): {failed}")
        validation_evidence = format_validation_evidence(validation, attempt_number)
        previous_diff = mutations[-1].diff_artifact if mutations else base_request.previous_diff
        repair_context = "\n".join(
            item
            for item in (
                base_request.context,
                "",
                "# Repair Evidence",
                validation_evidence,
                "",
                "# Previous Diff",
                previous_diff or "No previous diff captured.",
            )
            if item is not None
        )
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
            timeout_seconds=base_request.timeout_seconds,
            repair_attempt=attempt_number,
            previous_diff=previous_diff,
        )
        mutation = apply_mutation_request(engine, provider, repair_request)
        mutations.append(mutation)
        if not mutation.changed_files and not mutation.applied_files:
            stop_reason = "repair_noop"
            break
        validation = validator() if validator else simulate_validation(commands, fail_validation)
    if not validation.passed and not stop_reason and not plan.can_record_attempt():
        stop_reason = "repair_budget_exhausted"
    return RepairRunResult(plan, tuple(mutations), validation, stop_reason)
