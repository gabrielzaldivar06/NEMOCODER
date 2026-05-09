from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nemo_coding_platform.core.engine_interface import EngineProvider, MutationRequest, MutationResult, apply_mutation_request, create_engine_provider
from nemo_coding_platform.core.checkpoint import build_checkpoint_markdown, restore_file_snapshot, save_execution_snapshot, snapshot_changed_files
from nemo_coding_platform.core.contracts import ExecutionPhase, RuntimeState
from nemo_coding_platform.core.headless_handoff import HandoffRequest, build_handoff_plan, validate_handoff_request
from nemo_coding_platform.core.memory import MemoryAtomType
from nemo_coding_platform.core.model_config import ModelProfile, default_model_profile
from nemo_coding_platform.core.mutations import QualityMutationEngine
from nemo_coding_platform.core.nemo_adapter import InMemoryNemoAdapter, NemoCallResult, PersistentNemoAdapter
from nemo_coding_platform.core.nemo_lifecycle import NemoLifecyclePhase
from nemo_coding_platform.core.permission_engine import PermissionRuleset, load_ruleset_from_file, default_ruleset, PermissionAction
from nemo_coding_platform.core.product import AutonomyLevel
from nemo_coding_platform.core.product_factory import get_platform_info
from nemo_coding_platform.core.repair import RepairAttempt, RepairBudget, RepairPlan
from nemo_coding_platform.core.repair_engine import RepairRunResult, run_repair_loop
from nemo_coding_platform.core.review_package import ReviewPackage, build_review_package
from nemo_coding_platform.core.runtime import AgentRuntime
from nemo_coding_platform.core.task_run import (
    AppendOnlyTimeline,
    Artifact,
    ArtifactType,
    EventKind,
    HandoffCompletionGuard,
    MemoryTrace,
    Run,
    RunEvent,
    Task,
)
from nemo_coding_platform.core.todo_guard import build_todo_reminder, extract_todos_from_plan
from nemo_coding_platform.core.validation import VALIDATION_SKIPPED_COMMAND, ValidationCommand, ValidationResult, ValidationStatus, ValidationSuiteResult, format_validation_report, run_validation_suite, simulate_validation, write_python_validation_script
from nemo_coding_platform.core.workspace import Workspace
from nemo_coding_platform.core.worktree_runtime import snapshot_runtime_files, write_runtime_file


_CONTEXT_OVERFLOW_MARKERS = (
    "context size has been exceeded",
    "maximum context length",
    "context window",
    "prompt is too long",
    "token limit",
    "midstreamfallbackerror",
)
_MAX_NEMO_CONTEXT_CHARS = 4000


def _contains_context_overflow(text: str) -> bool:
    normalized = text.lower()
    return any(marker in normalized for marker in _CONTEXT_OVERFLOW_MARKERS)


def _mutation_has_context_overflow(result: MutationResult) -> bool:
    return _contains_context_overflow(result.stdout) or _contains_context_overflow(result.stderr)


def _validation_is_simulated(validation: ValidationSuiteResult) -> bool:
    for item in validation.results:
        output = (item.output or "").lower()
        if "simulated pass" in output or "simulated failure" in output:
            return True
    return False


def _enforce_validation_integrity(
    validation: ValidationSuiteResult,
    *,
    real_validation: bool,
    mutation_result: MutationResult,
) -> ValidationSuiteResult:
    if not real_validation:
        return validation

    integrity_failures: list[ValidationResult] = []
    if _validation_is_simulated(validation):
        integrity_failures.append(
            ValidationResult(
                ValidationCommand("validation integrity guard"),
                ValidationStatus.FAILED,
                "simulated validation detected while real_validation=true",
                None,
            )
        )
    if _mutation_has_context_overflow(mutation_result):
        integrity_failures.append(
            ValidationResult(
                ValidationCommand("mutation integrity guard"),
                ValidationStatus.FAILED,
                "context window exceeded during mutation output",
                mutation_result.returncode,
            )
        )

    if not integrity_failures:
        return validation
    return ValidationSuiteResult(validation.results + tuple(integrity_failures))


def _should_retry_chunked(request: HandoffRequest, mutation_result: MutationResult) -> bool:
    if not _mutation_has_context_overflow(mutation_result):
        return False
    objective = (request.objective_summary or request.prd).strip()
    if len(objective) >= 180:
        return True
    lowered = objective.lower()
    return "iteration" in lowered or "iteraciones" in lowered or "full log" in lowered


def _chunked_retry_objective(request: HandoffRequest) -> str:
    objective = (request.objective_summary or request.prd).strip()
    guidance = (
        "\n\nCHUNKED EXECUTION MODE (required due previous context overflow):\n"
        "- Do not emit the full artifact inline in chat output.\n"
        "- Write target files incrementally in small chunks (for example 5-10 sections per write).\n"
        "- Keep each intermediate update compact and continue until all acceptance criteria are complete.\n"
        "- Prioritize producing valid files over verbose reasoning text.\n"
    )
    return objective + guidance


def _bounded_nemo_context(text: str, max_chars: int = _MAX_NEMO_CONTEXT_CHARS) -> str:
    normalized = text.strip()
    if len(normalized) <= max_chars:
        return normalized
    clipped = normalized[:max_chars].rstrip()
    return f"{clipped}\n\n[truncated_nemo_context chars={len(normalized)} limit={max_chars}]"


@dataclass(frozen=True, slots=True)
class HeadlessRunResult:
    task: Task
    run: Run
    timeline: AppendOnlyTimeline
    artifacts: tuple[Artifact, ...]
    memory_traces: tuple[MemoryTrace, ...]
    validation: ValidationSuiteResult
    review_package: ReviewPackage
    nemo_results: tuple[NemoCallResult, ...] = ()
    repair_plan: RepairPlan | None = None
    execution_snapshots: dict[str, Any] | None = None  # Maps checkpoint_id -> snapshot dict
    platform_info: dict[str, Any] | None = None
    portfolio: dict[str, Any] | None = None
    mutation_result: MutationResult | None = None
    repair_result: RepairRunResult | None = None
    runtime_files: tuple[str, ...] = ()

    @property
    def effective_mutation_result(self) -> MutationResult | None:
        if self.mutation_result and (self.mutation_result.changed_files or self.mutation_result.applied_files):
            return self.mutation_result
        if self.repair_result:
            for mutation in reversed(self.repair_result.mutation_results):
                if mutation.changed_files or mutation.applied_files:
                    return mutation
        return self.mutation_result

    @property
    def effective_changed_files(self) -> tuple[str, ...]:
        mutation = self.effective_mutation_result
        return mutation.changed_files if mutation else ()

    def to_summary_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task.id,
            "run_id": self.run.id,
            "events": len(self.timeline.events),
            "artifacts": len(self.artifacts),
            "validation_passed": self.validation.passed,
            "changed_files": self.effective_changed_files,
        }


def _build_generated_spec(request: HandoffRequest) -> str:
    objective = (request.objective_summary or request.prd).strip()
    prd_source = (request.linked_prd or request.prd).strip()
    target_lines = [f"- {item}" for item in request.validation_commands]
    acceptance_lines = [f"- {item}" for item in request.acceptance_criteria]
    lines = [
        "# Generated Spec",
        "",
        f"- spec_mode: {request.spec_mode}",
        f"- objective: {objective}",
        f"- repo_path: {request.repo_path}",
        "",
        "## Acceptance Criteria",
        *acceptance_lines,
        "",
        "## Validation Commands",
        *(target_lines or ["- none"]),
    ]
    if request.spec_mode == "sdd":
        lines.extend(
            (
                "",
                "## Implementation Plan",
                "- derive or update executable specs before mutation",
                "- run validation before final review",
                "- keep checkpoints and review package replayable",
                "",
                "## Source PRD",
                prd_source,
            )
        )
    else:
        lines.extend(
            (
                "",
                "## Context",
                prd_source,
            )
        )
    return "\n".join(lines).strip() + "\n"


def execute_headless_handoff(
    request: HandoffRequest,
    fail_validation: tuple[str, ...] = (),
    task_id: str = "task-1",
    run_id: str = "run-1",
    real_validation: bool = False,
    validation_cwd: str | Path = ".",
    nemo_adapter: InMemoryNemoAdapter | PersistentNemoAdapter | None = None,
    mutation_provider: EngineProvider | None = None,
    provider_mode: str = "subprocess",
    model_profile: ModelProfile | None = None,
    engine_command: tuple[str, ...] | None = None,
    timeout_seconds: float = 300.0,
    target_files: tuple[str, ...] = (),
    validation_python_scripts: tuple[str, ...] = (),
    validation_policy: str = "smoke",
    bounded_simulation: bool = False,
    skill_prompt: str = "",
    permissions_file: str = "",
    image_path: str = "",
    repair_time_limit_seconds: float | None = None,
    validation_time_budget_seconds: float | None = None,
    validation_escalation_mode: bool = False,
    token_budget: int | None = None,
    resume_checkpoint_id: str | None = None,
    resume_repair_cursor: int = 0,
    resume_validation_state: tuple[str, ...] = (),
    resume_mode: str = "phase_boundary",
    resume_snapshot_runtime_path: str | None = None,
) -> HeadlessRunResult:
    validate_handoff_request(request)
    plan = build_handoff_plan(request)
    spec_content = _build_generated_spec(request)
    task = Task(
        task_id,
        request.repo_path,
        "Headless Handoff",
        request.objective_summary or request.prd,
        AutonomyLevel.FULL_HANDOFF,
        linked_prd=request.linked_prd,
        linked_specs=("generated-spec.md",),
    )
    agent_runtime = AgentRuntime.create(task.id, run_id, ".nemo-runtimes")
    runtime = agent_runtime.worktree
    execution_snapshots: dict[str, Any] = {}

    def _capture_execution_snapshot(
        checkpoint_id: str,
        *,
        phase: ExecutionPhase,
        changed_files: tuple[str, ...],
        mutation_count: int,
        repair_attempts: int,
        validation_results: tuple[str, ...],
        risk_flags: tuple[str, ...],
        resume_mode: str = "phase_boundary",
        repair_cursor: int = 0,
        runtime_snapshot_manifest: tuple[str, ...] = (),
        validation_state: tuple[str, ...] = (),
        nemo_evidence_handles: tuple[str, ...] = (),
    ) -> None:
        # Physically copy changed repo files into the runtime file-snapshot store.
        # This enables deterministic restore of file state on atomic resume.
        stored_files = snapshot_changed_files(
            repo_path=request.repo_path,
            changed_files=list(changed_files),
            runtime_path=runtime.worktree_path,
            checkpoint_id=checkpoint_id,
        )
        effective_manifest = tuple(stored_files) if stored_files else runtime_snapshot_manifest
        checkpoint_path = save_execution_snapshot(
            runtime_path=runtime.worktree_path,
            checkpoint_id=checkpoint_id,
            phase=phase,
            phase_elapsed_seconds=0.0,
            global_elapsed_seconds=0.0,
            changed_files=list(changed_files),
            timeline_events=[],
            mutation_count=mutation_count,
            repair_attempts=repair_attempts,
            validation_results=list(validation_results),
            risk_flags=list(risk_flags),
            resume_mode=resume_mode,
            repair_cursor=repair_cursor,
            runtime_snapshot_manifest=list(effective_manifest),
            validation_state=list(validation_state),
            nemo_evidence_handles=list(nemo_evidence_handles),
            snapshot_runtime_path=str(runtime.worktree_path),
        )
        payload = json.loads(Path(checkpoint_path).read_text(encoding="utf-8"))
        execution_snapshots[checkpoint_id] = payload

    def _nemo_handles() -> tuple[str, ...]:
        handles: list[str] = []
        for item in nemo_results:
            handle = item.payload.get("handle")
            if isinstance(handle, str) and handle:
                handles.append(handle)
        return tuple(dict.fromkeys(handles))
    if request.linked_prd:
        write_runtime_file(runtime, "source-prd.md", request.linked_prd)
    write_runtime_file(runtime, "generated-spec.md", spec_content)
    write_runtime_file(runtime, "generated-test.txt", "\n".join(request.acceptance_criteria))
    adapter = nemo_adapter or InMemoryNemoAdapter()
    nemo_results: list[NemoCallResult] = []
    adapter, result = adapter.call(NemoLifecyclePhase.START, "prime_context", topic=task.title)
    nemo_results.append(result)
    # Use the objective summary (or first 200 chars of the PRD) as the portfolio
    # task query — a focused signal produces better semantic retrieval than the
    # full PRD text, and the token budget scales with objective complexity.
    _portfolio_query = (request.objective_summary or request.prd[:200]).strip()
    # Budget heuristic: use query length in chars as a proxy for task complexity.
    # Simple tasks (≤50 chars) get 300 tokens; longer objectives get up to 800.
    _portfolio_budget = min(800, max(300, len(_portfolio_query) + 200))
    adapter, portfolio_result = adapter.call(
        NemoLifecyclePhase.BUILD,
        "build_context_portfolio",
        task=_portfolio_query,
        topic=task.title,
        portfolio_phase=ExecutionPhase.EXECUTE.value,
        token_budget=_portfolio_budget,
    )
    nemo_results.append(portfolio_result)
    nemo_context = _bounded_nemo_context(str(portfolio_result.payload.get("context", "")))
    if not nemo_context:
        search_query = f"{task.title}: {request.prd[:120]}"
        adapter, search_result = adapter.call(
            NemoLifecyclePhase.BUILD,
            "search_memories",
            query=search_query,
            compact=True,
            limit=5,
        )
        nemo_results.append(search_result)
        nemo_context = _bounded_nemo_context(str(search_result.payload.get("results", "")))
    platform = get_platform_info("nemo-code")
    run = Run(
        run_id,
        task.id,
        RuntimeState.REVIEWING,
        ExecutionPhase.REVIEW,
        agent_runtime.runtime_id,
        str(runtime.worktree_path),
        "local-model",
        "full-handoff",
        validation_policy,
    )

    profile = model_profile or default_model_profile()
    provider = mutation_provider or create_engine_provider(provider_mode, engine_command, cwd=Path("product/nemo_code_runtime"))
    engine = QualityMutationEngine(Workspace.from_path(runtime.worktree_path))

    # --- Permission System (inspired by opencode) ---
    repo_root = Path(request.repo_path).resolve()
    perm_path = Path(permissions_file) if permissions_file else repo_root / ".nemocode-permissions.json"
    ruleset = load_ruleset_from_file(perm_path) if perm_path.exists() else default_ruleset(AutonomyLevel.FULL_HANDOFF)
    
    allowed_files = []
    denied_files = []
    for f in target_files:
        if ruleset.evaluate("write_file", f) == PermissionAction.ALLOW:
            allowed_files.append(f)
        else:
            denied_files.append(f)

    if target_files and denied_files and not allowed_files:
        raise PermissionError(f"all requested target files denied by permissions policy: {', '.join(denied_files)}")

    # If user provided explicit targets, honor permissions by keeping only allowed ones.
    effective_targets = tuple(allowed_files) if target_files else target_files
    for relative_target in effective_targets:
        source_path = repo_root / relative_target
        if not source_path.exists() or not source_path.is_file():
            continue
        runtime_target = runtime.resolve_inside(relative_target)
        runtime_target.parent.mkdir(parents=True, exist_ok=True)
        runtime_target.write_bytes(source_path.read_bytes())

    mutation_request = MutationRequest(
        request.prd,
        "generated-spec.md",
        request.acceptance_criteria,
        nemo_context,
        provider_mode,
        request.repo_path,
        str(runtime.worktree_path),
        effective_targets,
        profile,
        "",
        timeout_seconds,
        skill_prompt=skill_prompt,
        image_path=image_path,
    )
    mutation_result = apply_mutation_request(engine, provider, mutation_request)
    if _should_retry_chunked(request, mutation_result):
        retry_request = MutationRequest(
            _chunked_retry_objective(request),
            mutation_request.spec_path,
            mutation_request.acceptance_criteria,
            mutation_request.context,
            mutation_request.provider_mode,
            mutation_request.repo_path,
            mutation_request.runtime_path,
            mutation_request.target_files,
            mutation_request.model_profile,
            (
                "Previous mutation failed due context overflow. "
                "Retrying with chunked artifact generation instructions."
            ),
            mutation_request.timeout_seconds,
            repair_attempt=1,
            previous_diff=mutation_result.diff_artifact,
            skill_prompt=mutation_request.skill_prompt,
            image_path=mutation_request.image_path,
            role=mutation_request.role,
        )
        retried_mutation = apply_mutation_request(engine, provider, retry_request)
        if retried_mutation.changed_files or retried_mutation.applied_files:
            mutation_result = retried_mutation

    resolved_validation_cwd: str | Path
    if validation_cwd == "runtime":
        resolved_validation_cwd = runtime.worktree_path
    elif validation_cwd == "repo":
        resolved_validation_cwd = request.repo_path
    else:
        resolved_validation_cwd = validation_cwd
    validation_commands = request.validation_commands
    if validation_python_scripts:
        generated_commands = tuple(
            write_python_validation_script(runtime.worktree_path, f"validation_script_{index}.py", source)
            for index, source in enumerate(validation_python_scripts, start=1)
        )
        validation_commands = validation_commands + generated_commands
    mutation_changed = bool(mutation_result.changed_files or mutation_result.applied_files)
    if validation_policy == "none":
        validation = ValidationSuiteResult(
            (ValidationResult(ValidationCommand(VALIDATION_SKIPPED_COMMAND, required=False), ValidationStatus.SKIPPED, "validation skipped by policy:none"),)
        )
    elif not mutation_changed:
        validation = ValidationSuiteResult(
            (
                ValidationResult(
                    ValidationCommand("mutation changed files"),
                    ValidationStatus.FAILED,
                    "mutation produced no changed files; validation skipped to avoid stale runtime pass",
                    mutation_result.returncode,
                ),
            )
        )
    elif real_validation:
        validation = run_validation_suite(
            validation_commands,
            cwd=resolved_validation_cwd,
            timeout_seconds=timeout_seconds,
            time_budget_seconds=validation_time_budget_seconds,
            escalation_mode=validation_escalation_mode,
        )
    else:
        validation = simulate_validation(validation_commands, fail_validation)
    validation = _enforce_validation_integrity(
        validation,
        real_validation=real_validation,
        mutation_result=mutation_result,
    )
    repair_result: RepairRunResult | None = None
    seeded_repair_cursor = max(0, int(resume_repair_cursor or 0))
    seeded_attempts = tuple(
        RepairAttempt(index + 1, "resume_seed", f"restored from atomic checkpoint {resume_checkpoint_id or 'unknown'}")
        for index in range(seeded_repair_cursor)
    )
    repair_plan = RepairPlan(RepairBudget(request.repair_budget), attempts=seeded_attempts)
    todo_reminder_injected = False
    resume_restore_content = ""
    if resume_checkpoint_id:
        # Physical file restore: copy previously-snapshotted files back to the repo.
        restored_files: list[str] = []
        if resume_snapshot_runtime_path:
            try:
                restored_files = restore_file_snapshot(
                    snapshot_runtime_path=resume_snapshot_runtime_path,
                    checkpoint_id=resume_checkpoint_id,
                    repo_path=request.repo_path,
                )
            except OSError:
                pass  # File restore is best-effort; logical cursor still provides continuity.
        validation_state_summary = ", ".join(resume_validation_state) if resume_validation_state else "none"
        restored_summary = ", ".join(restored_files) if restored_files else "none"
        resume_restore_content = "\n".join(
            (
                "# Resume Restore",
                "",
                f"resume_mode={resume_mode}",
                f"resume_checkpoint_id={resume_checkpoint_id}",
                f"resume_repair_cursor={seeded_repair_cursor}",
                f"resume_validation_state={validation_state_summary}",
                f"restored_files={restored_summary}",
                f"snapshot_runtime_path={resume_snapshot_runtime_path or 'none'}",
            )
        ) + "\n"
        write_runtime_file(runtime, "resume-restore.md", resume_restore_content)
    if not validation.passed:
        # --- Todo-Awareness Guard (inspired by deer-flow TodoMiddleware) ---
        # If validation failed, build a system_reminder from incomplete plan
        # steps and prepend it to the repair context so the agent cannot
        # silently exit without completing the original checklist.
        todos = extract_todos_from_plan(plan)
        todo_reminder = build_todo_reminder(todos)
        if todo_reminder:
            todo_reminder_injected = True

        if real_validation:
            validator = lambda: run_validation_suite(
                validation_commands,
                cwd=resolved_validation_cwd,
                timeout_seconds=timeout_seconds,
                time_budget_seconds=validation_time_budget_seconds,
                escalation_mode=validation_escalation_mode,
            )
        else:
            validator = lambda: simulate_validation(validation_commands, fail_validation)

        def _compress_repair_evidence(content: str, attempt_number: int) -> tuple[str, str | None]:
            nonlocal adapter
            adapter, compressed = adapter.call(
                NemoLifecyclePhase.BUILD,
                "compress_context_artifact",
                content=content,
                title=f"repair-validation-attempt-{attempt_number}",
                token_budget=180,
                source_id=run.id,
                persist_evidence=True,
            )
            nemo_results.append(compressed)
            payload = compressed.payload
            claim = str(payload.get("compact_claim", "")).strip() or "Validation evidence compacted."
            handle = str(payload.get("handle", "")).strip() or None
            return claim, handle

        remaining_repair_attempts = max(0, request.repair_budget - seeded_repair_cursor)
        if remaining_repair_attempts <= 0:
            repair_result = RepairRunResult(
                plan=RepairPlan(RepairBudget(0)),
                mutation_results=(),
                validation=validation,
                stop_reason="repair_budget_exhausted",
                tokens_consumed=0,
            )
        else:
            repair_result = run_repair_loop(
                validation,
                validation_commands,
                fail_validation,
                RepairBudget(remaining_repair_attempts),
                engine,
                provider,
                mutation_request,
                validator=validator,
                todo_reminder=todo_reminder,
                time_limit_seconds=repair_time_limit_seconds,
                on_attempt=lambda attempt, _elapsed: _capture_execution_snapshot(
                    f"checkpoint-repair-{attempt:05d}",
                    phase=ExecutionPhase.EXECUTE,
                    changed_files=tuple(mutation_result.changed_files or mutation_result.applied_files),
                    mutation_count=attempt,
                    repair_attempts=max(0, attempt - 1),
                    validation_results=(validation.summary(),),
                    risk_flags=("validation_failure",),
                    resume_mode="atomic",
                    repair_cursor=max(0, attempt - 1),
                    runtime_snapshot_manifest=("checkpoint-execute.md",),
                    validation_state=(validation.summary(),),
                    nemo_evidence_handles=_nemo_handles(),
                ),
                token_budget=token_budget,
                attempt_offset=seeded_repair_cursor,
                evidence_compactor=_compress_repair_evidence,
            )
        merged_attempts = seeded_attempts + tuple(
            RepairAttempt(seeded_repair_cursor + index + 1, item.reason, item.action)
            for index, item in enumerate(repair_result.plan.attempts)
        )
        repair_plan = RepairPlan(RepairBudget(request.repair_budget), attempts=merged_attempts)
        validation = repair_result.validation
        # --- Cognitive Learning: persist repair failure pattern to NEMO ---
        # Every exhausted repair budget feeds the empirical risk map so future
        # tasks can avoid the same file/command combinations.
        if isinstance(adapter, PersistentNemoAdapter) and not validation.passed:
            _failed_cmds = ", ".join(
                r.command.command for r in validation.results if not r.passed
            ) or "unknown"
            _files_touched = ", ".join(
                mutation_result.changed_files or mutation_result.applied_files or ()
            ) or "unknown"
            _task_desc = (request.objective_summary or request.prd)[:120]
            adapter, _repair_failure_trace = adapter.call(
                NemoLifecyclePhase.REVIEW,
                "create_correction",
                wrong_assumption=(
                    f"task '{_task_desc}' would pass validation within "
                    f"{request.repair_budget} repair attempts"
                ),
                correct_answer=(
                    f"repair exhausted for task '{_task_desc}'; "
                    f"stop_reason={repair_result.stop_reason}; "
                    f"failed_commands=[{_failed_cmds}]; files_touched=[{_files_touched}]"
                ),
                context=(
                    f"repair_budget={request.repair_budget} "
                    f"attempts_used={len(repair_result.mutation_results)} "
                    f"task={_task_desc}"
                ),
                topic="NEMOCODE self-modification",
                tags=("repair_failure", "self-mod-risk", "nemocode-repair"),
            )
            nemo_results.append(_repair_failure_trace)
            # --- Semantic Continuity Anchor: surface incomplete task in future sessions ---
            adapter, _anchor_trace = adapter.call(
                NemoLifecyclePhase.REVIEW,
                "intent_anchor",
                trigger_condition=f"starting a task similar to: {_task_desc}",
                action=(
                    f"review incomplete run task={task.id} run={run.id}; "
                    f"repair stopped at {repair_result.stop_reason}; "
                    f"files_in_progress=[{_files_touched}]"
                ),
                importance_level=7,
            )
            nemo_results.append(_anchor_trace)
    effective_mutation_result = mutation_result
    if repair_result and not (effective_mutation_result.changed_files or effective_mutation_result.applied_files):
        for repair_mutation in reversed(repair_result.mutation_results):
            if repair_mutation.changed_files or repair_mutation.applied_files:
                effective_mutation_result = repair_mutation
                break
    adapter, result = adapter.call(NemoLifecyclePhase.BUILD, "record_context_feedback", was_useful=True)
    nemo_results.append(result)
    memory_traces = (
        MemoryTrace("mem-1", run.id, "prime_context", "startup_context", "read_only", "Loaded startup context."),
        MemoryTrace("mem-2", run.id, "build_context_portfolio", "context_economy", "read_only", f"Built context portfolio tokens={portfolio_result.payload.get('estimated_tokens', 0)}."),
        MemoryTrace("mem-3", run.id, "store_conversation", "conversation", "memory_write", "Prepared final writeback."),
    )
    runtime_files = snapshot_runtime_files(runtime)
    permission_risks = tuple(f"permission_denied:{path}" for path in denied_files)
    risk_flags = tuple(
        filter(
            None,
            (
                "validation_failure" if not validation.passed else "",
                "simulated_validation" if any("simulated " in (result.output or "").lower() for result in validation.results) else "",
                "context_window_exceeded" if _mutation_has_context_overflow(mutation_result) else "",
                "no_changed_files" if not effective_mutation_result.changed_files and not effective_mutation_result.applied_files else "",
                *permission_risks,
            ),
        )
    )
    engine_output = "\n".join(
        (
            f"provider={mutation_result.provider}",
            f"provider_mode={mutation_result.provider_mode}",
            f"returncode={mutation_result.returncode if mutation_result.returncode is not None else ''}",
            f"duration_ms={mutation_result.duration_ms}",
            "",
            "# stdout",
            mutation_result.stdout or "",
            "",
            "# stderr",
            mutation_result.stderr or "",
        )
    )
    write_runtime_file(runtime, "engine-output.txt", engine_output)

    checkpoint_inputs = (
        (
            "checkpoint-plan.md",
            ExecutionPhase.PLAN.value,
            (),
            "Execute NEMO CODE mutation in isolated runtime.",
            (),
        ),
        (
            "checkpoint-execute.md",
            ExecutionPhase.EXECUTE.value,
            effective_mutation_result.changed_files or effective_mutation_result.applied_files,
            "Create final review package." if validation.passed else "Review blocked run and repair evidence.",
            risk_flags,
        ),
        (
            "checkpoint-review.md",
            ExecutionPhase.REVIEW.value,
            effective_mutation_result.changed_files or effective_mutation_result.applied_files,
            "Write NEMO memory and await human review.",
            risk_flags,
        ),
    ) if bounded_simulation else (
        (
            "checkpoint.md",
            ExecutionPhase.EXECUTE.value,
            effective_mutation_result.changed_files or effective_mutation_result.applied_files,
            "Create final review package." if validation.passed else "Review blocked run and repair evidence.",
            risk_flags,
        ),
    )
    checkpoint_contents: dict[str, str] = {}
    for checkpoint_path, checkpoint_phase, changed_files, next_action, checkpoint_risks in checkpoint_inputs:
        checkpoint_content = build_checkpoint_markdown(
            checkpoint_phase,
            tuple(changed_files),
            validation,
            memory_traces,
            next_action,
            tuple(checkpoint_risks),
            effective_mutation_result,
        )
        checkpoint_contents[checkpoint_path] = checkpoint_content
        write_runtime_file(runtime, checkpoint_path, checkpoint_content)
        snapshot_id = checkpoint_path.replace(".md", "")
        resume_mode = "atomic" if checkpoint_phase == ExecutionPhase.EXECUTE.value and repair_result is not None else "phase_boundary"
        validation_summaries = tuple(result.command.command for result in validation.results)
        _capture_execution_snapshot(
            snapshot_id,
            phase=ExecutionPhase(checkpoint_phase),
            changed_files=tuple(changed_files),
            mutation_count=1 + (len(repair_result.mutation_results) if repair_result else 0),
            repair_attempts=len(repair_plan.attempts),
            validation_results=validation_summaries,
            risk_flags=tuple(checkpoint_risks),
            resume_mode=resume_mode,
            repair_cursor=len(repair_plan.attempts),
            runtime_snapshot_manifest=(checkpoint_path,),
            validation_state=(validation.summary(),),
            nemo_evidence_handles=_nemo_handles(),
        )
        if bounded_simulation:
            adapter, checkpoint_result = adapter.call(
                NemoLifecyclePhase.BUILD,
                "store_conversation",
                summary=f"Checkpoint writeback prepared: {checkpoint_path} for run {run.id}",
                topic=task.title,
                tags=(task.id, run.id, checkpoint_phase, "checkpoint"),
                atom_type=MemoryAtomType.ARTIFACT_STATE.value,
                source_scope="handoff_runtime",
                importance=3,
            )
            nemo_results.append(checkpoint_result)

    timeline = AppendOnlyTimeline()
    if bounded_simulation:
        event_specs = (
            (ExecutionPhase.EXECUTE, EventKind.RESUMED, f"Resumed execution from checkpoint {resume_checkpoint_id}." if resume_checkpoint_id else "", "resume-restore.md" if resume_checkpoint_id else None),
            (ExecutionPhase.PLAN, EventKind.CONTEXT_BOOTSTRAPPED, "NEMO context portfolio bootstrapped.", None),
            (ExecutionPhase.PLAN, EventKind.PLAN_CREATED, f"Created handoff plan with {len(plan.steps)} steps.", None),
            (ExecutionPhase.PLAN, EventKind.CHECKPOINT, "Created plan checkpoint.", "checkpoint-plan.md"),
            (ExecutionPhase.PLAN, EventKind.PERMISSION_DECIDED, "Full Handoff permissions preapproved in sandbox.", None),
            (ExecutionPhase.EXECUTE, EventKind.MUTATION_CREATED, mutation_result.summary, "engine-output.txt"),
            (ExecutionPhase.EXECUTE, EventKind.VALIDATION_RUN, validation.summary(), None),
            (ExecutionPhase.EXECUTE, EventKind.CHECKPOINT, "Created execute checkpoint.", "checkpoint-execute.md"),
            (ExecutionPhase.REVIEW, EventKind.REVIEW_PACKAGE_CREATED, "Created review package.", None),
            (ExecutionPhase.REVIEW, EventKind.CHECKPOINT, "Created review checkpoint.", "checkpoint-review.md"),
            (ExecutionPhase.REVIEW, EventKind.MEMORY_WRITTEN, "Prepared NEMO writeback summary.", None),
        )
    else:
        event_specs = (
            (ExecutionPhase.EXECUTE, EventKind.RESUMED, f"Resumed execution from checkpoint {resume_checkpoint_id}." if resume_checkpoint_id else "", "resume-restore.md" if resume_checkpoint_id else None),
            (ExecutionPhase.PLAN, EventKind.CONTEXT_BOOTSTRAPPED, "NEMO context portfolio bootstrapped.", None),
            (ExecutionPhase.PLAN, EventKind.PLAN_CREATED, f"Created handoff plan with {len(plan.steps)} steps.", None),
            (ExecutionPhase.PLAN, EventKind.PERMISSION_DECIDED, "Full Handoff permissions preapproved in sandbox.", None),
            (ExecutionPhase.EXECUTE, EventKind.MUTATION_CREATED, mutation_result.summary, "engine-output.txt"),
            (ExecutionPhase.EXECUTE, EventKind.VALIDATION_RUN, validation.summary(), None),
            (ExecutionPhase.EXECUTE, EventKind.CHECKPOINT, "Created unattended checkpoint.", "checkpoint.md"),
            (ExecutionPhase.REVIEW, EventKind.REVIEW_PACKAGE_CREATED, "Created review package.", None),
            (ExecutionPhase.REVIEW, EventKind.MEMORY_WRITTEN, "Prepared NEMO writeback summary.", None),
        )
    if denied_files:
        event_specs = event_specs + (
            (
                ExecutionPhase.PLAN,
                EventKind.PERMISSION_DENIED,
                f"Permission denied for target files: {', '.join(denied_files)}.",
                None,
            ),
        )
    filtered_event_specs = tuple(
        item
        for item in event_specs
        if not (item[1] == EventKind.RESUMED and not resume_checkpoint_id)
    )
    for index, (phase, kind, summary, payload_ref) in enumerate(filtered_event_specs, start=1):
        timeline = timeline.append(RunEvent(f"event-{index}", run.id, index, phase, kind, summary, payload_ref))

    patch_content = effective_mutation_result.diff_artifact or "\n".join((f"provider={effective_mutation_result.provider}", f"applied={','.join(effective_mutation_result.applied_files)}"))
    checkpoint_artifacts = tuple(
        Artifact.from_content(f"artifact-checkpoint-{index}", run.id, ArtifactType.CHECKPOINT, checkpoint_path, "Unattended checkpoint", checkpoint_content)
        for index, (checkpoint_path, checkpoint_content) in enumerate(checkpoint_contents.items(), start=1)
    )
    prd_artifacts = (
        (Artifact.from_content("artifact-prd", run.id, ArtifactType.SPEC, "source-prd.md", "Source PRD", request.linked_prd),)
        if request.linked_prd
        else ()
    )
    resume_artifacts = (
        (Artifact.from_content("artifact-resume-restore", run.id, ArtifactType.SUPERVISOR, "resume-restore.md", "Atomic resume restore metadata", resume_restore_content),)
        if resume_restore_content
        else ()
    )
    artifacts = prd_artifacts + resume_artifacts + (
        Artifact.from_content("artifact-spec", run.id, ArtifactType.SPEC, "generated-spec.md", "Generated specs", spec_content),
        Artifact.from_content("artifact-test", run.id, ArtifactType.TEST, "generated-test.txt", "Generated tests", "\n".join(request.acceptance_criteria)),
        Artifact.from_content("artifact-patch", run.id, ArtifactType.PATCH, "patch.diff", f"Applied provider mutation files={len(effective_mutation_result.changed_files)}", patch_content),
        Artifact.from_content("artifact-engine-output", run.id, ArtifactType.ENGINE_OUTPUT, "engine-output.txt", f"Engine output returncode={mutation_result.returncode}", engine_output),
        Artifact.from_content("artifact-validation", run.id, ArtifactType.VALIDATION, "validation.txt", "Validation summary", format_validation_report(validation)),
        Artifact.from_content("artifact-memory", run.id, ArtifactType.MEMORY_SUMMARY, "memory.md", "Memory writeback", f"NEMO writeback prepared. runtime_files={','.join(runtime_files)}"),
    ) + checkpoint_artifacts
    review = build_review_package(
        task,
        run,
        f"Mutation prepared through {mutation_result.provider} and the NEMO CODE quality engine.",
        validation,
        memory_traces,
        mutation_result=effective_mutation_result,
        repair_attempts=len(repair_plan.attempts),
    )
    write_runtime_file(runtime, "validation.txt", format_validation_report(validation))
    write_runtime_file(runtime, "review-package.md", review.to_markdown())
    write_runtime_file(runtime, "memory.md", "\n".join(trace.summary for trace in memory_traces))
    adapter, validation_evidence = adapter.call(
        NemoLifecyclePhase.REVIEW,
        "compress_context_artifact",
        content=format_validation_report(validation),
        title=f"Validation report for {run.id}",
        source_id=run.id,
    )
    nemo_results.append(validation_evidence)
    adapter, result = adapter.call(
        NemoLifecyclePhase.REVIEW,
        "store_conversation",
        summary=(
            f"Headless run review package created task={task.id} run={run.id} "
            f"validation_passed={validation.passed} changed_files={','.join(effective_mutation_result.changed_files or effective_mutation_result.applied_files)}"
        ),
        topic=task.title,
        tags=(task.id, run.id, "review", "validation_passed" if validation.passed else "validation_failed"),
        atom_type=MemoryAtomType.SESSION_SUMMARY.value,
        source_scope="handoff_review",
        evidence_handle=validation_evidence.payload.get("handle"),
        importance=8 if validation.passed else 9,
    )
    nemo_results.append(result)
    artifacts = artifacts + (review.to_artifact("artifact-review"),)
    HandoffCompletionGuard().validate(run, timeline, artifacts)
    runtime_files = snapshot_runtime_files(runtime)
    return HeadlessRunResult(
        task=task,
        run=run,
        timeline=timeline,
        artifacts=artifacts,
        memory_traces=memory_traces,
        validation=validation,
        review_package=review,
        nemo_results=tuple(nemo_results),
        repair_plan=repair_plan,
        execution_snapshots=execution_snapshots,
        platform_info=platform,
        portfolio=dict(portfolio_result.payload),
        mutation_result=mutation_result,
        repair_result=repair_result,
        runtime_files=runtime_files,
    )