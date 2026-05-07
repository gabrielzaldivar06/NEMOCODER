from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nemo_coding_platform.core.aider_interface import AiderProvider, MutationRequest, MutationResult, apply_mutation_request, create_aider_provider
from nemo_coding_platform.core.checkpoint import build_checkpoint_markdown
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
from nemo_coding_platform.core.repair import RepairBudget, RepairPlan
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


def execute_headless_handoff(
    request: HandoffRequest,
    fail_validation: tuple[str, ...] = (),
    task_id: str = "task-1",
    run_id: str = "run-1",
    real_validation: bool = False,
    validation_cwd: str | Path = ".",
    nemo_adapter: InMemoryNemoAdapter | PersistentNemoAdapter | None = None,
    mutation_provider: AiderProvider | None = None,
    provider_mode: str = "fake",
    model_profile: ModelProfile | None = None,
    aider_command: tuple[str, ...] | None = None,
    timeout_seconds: float = 30.0,
    target_files: tuple[str, ...] = (),
    validation_python_scripts: tuple[str, ...] = (),
    validation_policy: str = "smoke",
    bounded_simulation: bool = False,
    skill_prompt: str = "",
    permissions_file: str = "",
    image_path: str = "",
) -> HeadlessRunResult:
    validate_handoff_request(request)
    plan = build_handoff_plan(request)
    task = Task(task_id, request.repo_path, "Headless Handoff", request.prd, AutonomyLevel.FULL_HANDOFF)
    agent_runtime = AgentRuntime.create(task.id, run_id, ".nemo-runtimes")
    runtime = agent_runtime.worktree
    write_runtime_file(runtime, "generated-spec.md", request.prd)
    write_runtime_file(runtime, "generated-test.txt", "\n".join(request.acceptance_criteria))
    adapter = nemo_adapter or InMemoryNemoAdapter()
    nemo_results: list[NemoCallResult] = []
    adapter, result = adapter.call(NemoLifecyclePhase.START, "prime_context", topic=task.title)
    nemo_results.append(result)
    adapter, portfolio_result = adapter.call(
        NemoLifecyclePhase.BUILD,
        "build_context_portfolio",
        task=request.prd,
        topic=task.title,
        portfolio_phase=ExecutionPhase.EXECUTE.value,
        token_budget=600,
    )
    nemo_results.append(portfolio_result)
    platform = get_platform_info("aider")
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
    provider = mutation_provider or create_aider_provider(provider_mode, aider_command, cwd=Path("product/aider"))
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
        str(portfolio_result.payload.get("context", "")),
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
    validation = (
        ValidationSuiteResult((ValidationResult(ValidationCommand(VALIDATION_SKIPPED_COMMAND, required=False), ValidationStatus.SKIPPED, "validation skipped by policy:none"),))
        if validation_policy == "none"
        else
        ValidationSuiteResult(
            (
                ValidationResult(
                    ValidationCommand("mutation changed files"),
                    ValidationStatus.FAILED,
                    "mutation produced no changed files; validation skipped to avoid stale runtime pass",
                    mutation_result.returncode,
                ),
            )
        )
        if not mutation_changed
        else run_validation_suite(validation_commands, cwd=resolved_validation_cwd)
        if real_validation
        else simulate_validation(validation_commands, fail_validation)
    )
    repair_result: RepairRunResult | None = None
    repair_plan = RepairPlan(RepairBudget(request.repair_budget))
    todo_reminder_injected = False
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
            validator = lambda: run_validation_suite(validation_commands, cwd=resolved_validation_cwd)
        else:
            validator = lambda: simulate_validation(validation_commands, fail_validation)
        repair_result = run_repair_loop(
            validation,
            validation_commands,
            fail_validation,
            RepairBudget(request.repair_budget),
            engine,
            provider,
            mutation_request,
            validator=validator,
            todo_reminder=todo_reminder,
        )
        repair_plan = repair_result.plan
        validation = repair_result.validation
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
                "no_changed_files" if not effective_mutation_result.changed_files and not effective_mutation_result.applied_files else "",
                *permission_risks,
            ),
        )
    )
    aider_output = "\n".join(
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
    write_runtime_file(runtime, "aider-output.txt", aider_output)

    checkpoint_inputs = (
        (
            "checkpoint-plan.md",
            ExecutionPhase.PLAN.value,
            (),
            "Execute Aider mutation in isolated runtime.",
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
        if bounded_simulation:
            adapter, checkpoint_result = adapter.call(
                NemoLifecyclePhase.BUILD,
                "store_conversation",
                summary=f"Checkpoint writeback prepared: {checkpoint_path} for run {run.id}",
                topic=task.title,
                tags=(task.id, run.id, checkpoint_phase, "checkpoint"),
                atom_type=MemoryAtomType.ARTIFACT_STATE.value,
                source_scope="handoff_runtime",
                importance=7,
            )
            nemo_results.append(checkpoint_result)

    timeline = AppendOnlyTimeline()
    if bounded_simulation:
        event_specs = (
            (ExecutionPhase.PLAN, EventKind.CONTEXT_BOOTSTRAPPED, "NEMO context portfolio bootstrapped.", None),
            (ExecutionPhase.PLAN, EventKind.PLAN_CREATED, f"Created handoff plan with {len(plan.steps)} steps.", None),
            (ExecutionPhase.PLAN, EventKind.CHECKPOINT, "Created plan checkpoint.", "checkpoint-plan.md"),
            (ExecutionPhase.PLAN, EventKind.PERMISSION_DECIDED, "Full Handoff permissions preapproved in sandbox.", None),
            (ExecutionPhase.EXECUTE, EventKind.MUTATION_CREATED, mutation_result.summary, "aider-output.txt"),
            (ExecutionPhase.EXECUTE, EventKind.VALIDATION_RUN, validation.summary(), None),
            (ExecutionPhase.EXECUTE, EventKind.CHECKPOINT, "Created execute checkpoint.", "checkpoint-execute.md"),
            (ExecutionPhase.REVIEW, EventKind.REVIEW_PACKAGE_CREATED, "Created review package.", None),
            (ExecutionPhase.REVIEW, EventKind.CHECKPOINT, "Created review checkpoint.", "checkpoint-review.md"),
            (ExecutionPhase.REVIEW, EventKind.MEMORY_WRITTEN, "Prepared NEMO writeback summary.", None),
        )
    else:
        event_specs = (
            (ExecutionPhase.PLAN, EventKind.CONTEXT_BOOTSTRAPPED, "NEMO context portfolio bootstrapped.", None),
            (ExecutionPhase.PLAN, EventKind.PLAN_CREATED, f"Created handoff plan with {len(plan.steps)} steps.", None),
            (ExecutionPhase.PLAN, EventKind.PERMISSION_DECIDED, "Full Handoff permissions preapproved in sandbox.", None),
            (ExecutionPhase.EXECUTE, EventKind.MUTATION_CREATED, mutation_result.summary, "aider-output.txt"),
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
    for index, (phase, kind, summary, payload_ref) in enumerate(event_specs, start=1):
        timeline = timeline.append(RunEvent(f"event-{index}", run.id, index, phase, kind, summary, payload_ref))

    patch_content = effective_mutation_result.diff_artifact or "\n".join((f"provider={effective_mutation_result.provider}", f"applied={','.join(effective_mutation_result.applied_files)}"))
    checkpoint_artifacts = tuple(
        Artifact.from_content(f"artifact-checkpoint-{index}", run.id, ArtifactType.CHECKPOINT, checkpoint_path, "Unattended checkpoint", checkpoint_content)
        for index, (checkpoint_path, checkpoint_content) in enumerate(checkpoint_contents.items(), start=1)
    )
    artifacts = (
        Artifact.from_content("artifact-spec", run.id, ArtifactType.SPEC, "generated-spec.md", "Generated specs", request.prd),
        Artifact.from_content("artifact-test", run.id, ArtifactType.TEST, "generated-test.txt", "Generated tests", "\n".join(request.acceptance_criteria)),
        Artifact.from_content("artifact-patch", run.id, ArtifactType.PATCH, "patch.diff", f"Applied provider mutation files={len(effective_mutation_result.changed_files)}", patch_content),
        Artifact.from_content("artifact-aider-output", run.id, ArtifactType.AIDER_OUTPUT, "aider-output.txt", f"Aider output returncode={mutation_result.returncode}", aider_output),
        Artifact.from_content("artifact-validation", run.id, ArtifactType.VALIDATION, "validation.txt", "Validation summary", format_validation_report(validation)),
        Artifact.from_content("artifact-memory", run.id, ArtifactType.MEMORY_SUMMARY, "memory.md", "Memory writeback", f"NEMO writeback prepared. runtime_files={','.join(runtime_files)}"),
    ) + checkpoint_artifacts
    review = build_review_package(
        task,
        run,
        f"Mutation prepared through {mutation_result.provider} and Aider Quality Core.",
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
        task,
        run,
        timeline,
        artifacts,
        memory_traces,
        validation,
        review,
        tuple(nemo_results),
        repair_plan,
        platform,
        dict(portfolio_result.payload),
        mutation_result,
        repair_result,
        runtime_files,
    )