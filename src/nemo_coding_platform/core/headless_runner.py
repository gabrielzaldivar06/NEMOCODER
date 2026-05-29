from __future__ import annotations
import json
import logging
import re
import urllib.request as _urllib_request
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from nemo_coding_platform.core.engine_interface import EngineProvider, MutationRequest, MutationResult, apply_mutation_request, create_engine_provider
from nemo_coding_platform.core.checkpoint import build_checkpoint_markdown, load_execution_snapshot, restore_file_snapshot, save_execution_snapshot, snapshot_changed_files
from nemo_coding_platform.core.contracts import ExecutionPhase, RuntimeState
from nemo_coding_platform.core.headless_handoff import HandoffRequest, build_handoff_plan, validate_handoff_request
from nemo_coding_platform.core.memory import MemoryAtomType
from nemo_coding_platform.core.model_config import ModelProfile, ModelRoleProfile, default_model_profile
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
    NemoMemoryEvent,
    Run,
    RunModelProfileLink,
    RunEvent,
    Task,
    WorkflowRecipe,
)
from nemo_coding_platform.core.todo_guard import build_todo_reminder, extract_todos_from_plan
from nemo_coding_platform.core.validation import VALIDATION_SKIPPED_COMMAND, ValidationCommand, ValidationResult, ValidationStatus, ValidationSuiteResult, format_validation_report, run_validation_suite, simulate_validation, write_python_validation_script
from nemo_coding_platform.core.workspace import Workspace
from nemo_coding_platform.core.event_emitter import emit_event
from nemo_coding_platform.core.repo_map import build_repo_map, read_anchor_docs
from nemo_coding_platform.core.worktree_runtime import (
    initialize_git_worktree_runtime,
    snapshot_runtime_files,
    worktree_branch_name,
    write_runtime_file,
)
from nemo_coding_platform.core.nemo_learning import (
    build_project_context,
    ingest_project_architecture,
    ingest_task_outcome,
)


_logger = logging.getLogger(__name__)
_THINK_RE = re.compile(r"<think>[\s\S]*?</think>", re.S)

_REPAIR_BUDGET_BY_AUTONOMY: dict[str, int] = {
    "manual": 1,
    "assisted": 2,
    "autonomous_sandbox": 3,
    "background_agent": 10,
    "full_handoff": 50,   # time-based limit (repair_time_limit_seconds) is the real constraint
    "team_ci": 100,
}


def _repair_budget_from_autonomy(level: "AutonomyLevel") -> int:
    return _REPAIR_BUDGET_BY_AUTONOMY.get(str(level), 3)


def _make_repair_critique_fn(profile: "ModelProfile") -> "Callable[[str, str, str], float] | None":
    if not profile or not profile.base_url:
        return None

    def _critique(objective: str, diff: str, validation_summary: str) -> float:
        sys_prompt = (
            "You are a code quality evaluator for multi-file software repair tasks. "
            "Reply ONLY with a JSON object — no markdown, no prose. "
            'Format: {"score":<int 1-10>,"summary":"<str>"}\n'
            "Scoring: 10=perfect, 7-9=good (tests pass, clean code), 5-6=partial, 1-4=broken. "
            "Primary signal: validation passed and objective is fully met."
        )
        user_msg = (
            f"Objective: {objective}\n\n"
            f"Diff (truncated):\n{diff[:1200]}\n\n"
            f"Validation: {validation_summary}"
        )
        payload = {
            "model": profile.model or "local",
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_msg},
            ],
            "max_tokens": 128,
            "temperature": 0.0,
        }
        headers = {"Content-Type": "application/json"}
        _key = profile.api_key
        if _key:
            headers["Authorization"] = f"Bearer {_key}"
        url = profile.base_url.rstrip("/") + "/chat/completions"
        req = _urllib_request.Request(url, json.dumps(payload).encode(), headers)
        with _urllib_request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        text = data["choices"][0]["message"]["content"]
        return float(json.loads(text)["score"])

    return _critique


_CONTEXT_OVERFLOW_MARKERS = (
    "context size has been exceeded",
    "maximum context length",
    "context window",
    "prompt is too long",
    "token limit",
    "midstreamfallbackerror",
)
_MAX_NEMO_CONTEXT_CHARS = 10000


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


def _render_nemo_payload_as_context(payload: dict[str, Any] | None) -> str:
    """Render an arbitrary NEMO MCP response payload as a context string.

    The NEMO MCP tools (build_context_portfolio, prime_context, search_memories,
    context_bootstrap) return data under different keys depending on the tool and
    server version. This helper tries each known shape and produces a single
    text block the LLM can consume. Returns "" only when no recognizable content
    is present.

    Recognized shapes (checked in order):
      - {"context": "..."} — legacy direct text
      - {"context_portfolio": {"atoms": [...]}} — context_bootstrap envelope
      - {"atoms": [{"content": "...", "importance_level": N, "tags": [...]}, ...]}
      - {"prime_context": {"memories": ["...", ...]}}
      - {"memories": ["...", ...]}
      - {"results": "..." | [...]} — search_memories
      - {"hint": "..."} — instructional response
    """
    if not isinstance(payload, dict) or not payload:
        return ""

    direct_context = payload.get("context")
    if isinstance(direct_context, str) and direct_context.strip():
        return direct_context.strip()

    portfolio = payload.get("context_portfolio")
    if isinstance(portfolio, dict):
        rendered = _render_atoms(portfolio.get("atoms"))
        if rendered:
            return rendered

    atoms_top = payload.get("atoms")
    if atoms_top:
        rendered = _render_atoms(atoms_top)
        if rendered:
            return rendered

    prime = payload.get("prime_context")
    if isinstance(prime, dict):
        memories = prime.get("memories")
        joined = _join_memory_strings(memories)
        if joined:
            return joined

    memories_top = payload.get("memories")
    joined = _join_memory_strings(memories_top)
    if joined:
        return joined

    results = payload.get("results")
    if isinstance(results, str) and results.strip():
        return results.strip()
    if isinstance(results, list):
        joined = _join_memory_strings(results)
        if joined:
            return joined

    hint = payload.get("hint")
    if isinstance(hint, str) and hint.strip():
        return hint.strip()

    return ""


def _render_atoms(atoms: object) -> str:
    if not isinstance(atoms, list) or not atoms:
        return ""
    lines: list[str] = []
    for atom in atoms:
        if not isinstance(atom, dict):
            continue
        content = str(atom.get("content") or "").strip()
        if not content:
            continue
        importance = atom.get("importance_level")
        tags = atom.get("tags") or []
        tag_str = ",".join(str(t) for t in tags[:5]) if isinstance(tags, list) else ""
        prefix_parts: list[str] = []
        if importance is not None:
            prefix_parts.append(f"imp={importance}")
        if tag_str:
            prefix_parts.append(f"tags={tag_str}")
        prefix = f"[{' '.join(prefix_parts)}] " if prefix_parts else ""
        lines.append(f"- {prefix}{content}")
    return "\n".join(lines)


def _join_memory_strings(memories: object) -> str:
    if not isinstance(memories, list) or not memories:
        return ""
    items: list[str] = []
    for entry in memories:
        if isinstance(entry, str) and entry.strip():
            items.append(f"- {entry.strip()}")
        elif isinstance(entry, dict):
            content = str(entry.get("content") or entry.get("text") or "").strip()
            if content:
                items.append(f"- {content}")
    return "\n".join(items)


@dataclass(frozen=True, slots=True)
class HeadlessRunResult:
    task: Task
    run: Run
    timeline: AppendOnlyTimeline
    artifacts: tuple[Artifact, ...]
    memory_traces: tuple[MemoryTrace | NemoMemoryEvent, ...]
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
    workflow_recipe: WorkflowRecipe | None = None

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


def _generate_tests_content(
    profile: ModelProfile,
    prd: str,
    spec_content: str,
    acceptance_criteria: tuple[str, ...],
) -> str:
    """Ask the LLM to generate pytest test stubs for the spec. Returns empty string on LLM failure."""
    import json
    import urllib.request

    criteria_str = "\n".join(f"- {c}" for c in acceptance_criteria[:10])
    system = (
        "You are a TDD test generator. Given a spec and acceptance criteria, "
        "write a Python pytest test file. Output ONLY raw Python code — no markdown fences, "
        "no explanations. Tests must cover the acceptance criteria. "
        "Use 'def test_' prefix for all test functions. No unittest.TestCase."
    )
    user = (
        f"## Objective\n{prd[:400]}\n\n"
        f"## Acceptance Criteria\n{criteria_str}\n\n"
        f"## Spec\n{spec_content[:600]}\n\n"
        "Write test_generated.py with pytest tests that verify the acceptance criteria. "
        "Output raw Python only."
    )
    base_url = profile.base_url.rstrip("/")
    payload = {
        "model": profile.model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": 512,
        "temperature": 0.2,
    }
    try:
        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as f:
            data = json.loads(f.read().decode("utf-8"))
        return str(data["choices"][0]["message"]["content"]).strip()
    except Exception:
        return ""


def _plan_target_files(
    profile: "ModelProfile",
    objective: str,
    repo_map: str,
    anchor_docs: str,
) -> list[str]:
    """Lightweight planner call: given repo map + objective, return target file paths.

    Uses a fast, low-temperature call with a small token budget (256 tokens).
    Returns empty list on any failure — caller treats it as no-op.
    """
    import json as _json
    import urllib.request as _urllib

    context_parts: list[str] = []
    if anchor_docs:
        context_parts.append(anchor_docs)
    if repo_map:
        context_parts.append(f"## Repo Map\n{repo_map[:4000]}")

    system = (
        "You are a software planning assistant. Given a repo structure and an objective, "
        "identify which files need to be modified. "
        "Reply ONLY with a JSON object — no markdown, no prose. "
        'Format: {"target_files": ["relative/path/to/file.ext"], "approach": "<one sentence>"}'
    )
    user = f"## Objective\n{objective[:500]}\n\n" + "\n\n".join(context_parts)

    payload = {
        "model": profile.model or "local",
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": 256,
        "temperature": 0.0,
    }
    url = profile.base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if profile.api_key:
        headers["Authorization"] = f"Bearer {profile.api_key}"
    try:
        req = _urllib.Request(url, _json.dumps(payload).encode(), headers)
        with _urllib.urlopen(req, timeout=30) as resp:
            data = _json.loads(resp.read())
        text = str(data["choices"][0]["message"]["content"]).strip()
        # Strip optional markdown fences
        if text.startswith("```"):
            text = "\n".join(text.splitlines()[1:])
            if text.endswith("```"):
                text = text[:-3].rstrip()
        parsed = _json.loads(text)
        files = [str(f) for f in parsed.get("target_files", []) if isinstance(f, str) and f]
        return files[:12]  # hard cap — planner should not return huge lists
    except Exception:  # noqa: BLE001
        return []


@dataclass(frozen=True, slots=True)
class _SubtaskSpec:
    title: str
    objective: str
    target_files: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]


def _decompose_prd(
    profile: "ModelProfile",
    prd: str,
    repo_map: str,
    hint_targets: tuple[str, ...] = (),
    max_subtasks: int = 4,
) -> list[_SubtaskSpec]:
    """Split a PRD into sequential subtasks via an LLM call.

    Returns [] when decomposition fails, PRD is too small, or LLM returns 1 item —
    caller treats empty list as "run as single task" (existing behaviour).
    """
    import json as _json
    import urllib.request as _urllib

    if len(prd) < 400 or not profile or not profile.base_url:
        return []

    hint = f"Files likely affected: {', '.join(hint_targets[:6])}" if hint_targets else ""
    repo_ctx = repo_map[:3000] if repo_map else ""

    system = (
        "You are a software architect. Break the given task into 2-4 sequential subtasks "
        "that each create or edit 1-3 focused files and build on the previous subtask. "
        "Reply ONLY with a JSON array — no markdown, no extra text. "
        'Each item: {"title":"<short>","objective":"<what to implement>","target_files":["path"],"acceptance_criteria":["criterion"]}'
    )
    user = "\n\n".join(filter(None, [f"## Task\n{prd[:800]}", hint, f"## Repo Map\n{repo_ctx}"]))

    payload = {
        "model": profile.model or "local",
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": 600,
        "temperature": 0.0,
    }
    url = profile.base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if profile.api_key:
        headers["Authorization"] = f"Bearer {profile.api_key}"
    try:
        req = _urllib.Request(url, _json.dumps(payload).encode(), headers)
        with _urllib.urlopen(req, timeout=30) as resp:
            text = str(_json.loads(resp.read())["choices"][0]["message"]["content"]).strip()
        text = _THINK_RE.sub("", text).strip()  # drop <think>…</think> from thinking models
        # Strip optional markdown fences
        if text.startswith("```"):
            lines = text.splitlines()[1:]
            text = "\n".join(lines[:-1] if lines and lines[-1].strip() == "```" else lines)
        items = _json.loads(text)
        if not isinstance(items, list) or len(items) <= 1:
            return []
        subtasks: list[_SubtaskSpec] = []
        for item in items[:max_subtasks]:
            if not isinstance(item, dict):
                continue
            subtasks.append(_SubtaskSpec(
                title=str(item.get("title") or f"Subtask {len(subtasks) + 1}")[:80],
                objective=str(item.get("objective") or "")[:1000],
                target_files=tuple(str(f) for f in item.get("target_files", []) if isinstance(f, str))[:6],
                acceptance_criteria=tuple(str(c) for c in item.get("acceptance_criteria", []) if isinstance(c, str))[:5],
            ))
        return subtasks if len(subtasks) >= 2 else []
    except Exception:  # noqa: BLE001
        return []


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
    model_role_profile: ModelRoleProfile | None = None,
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
    use_git_worktree: bool = True,
    autonomy_level: AutonomyLevel = AutonomyLevel.FULL_HANDOFF,
) -> HeadlessRunResult:
    validate_handoff_request(request)
    plan = build_handoff_plan(request)
    emit_event("plan_created", f"Plan: {len(plan.steps)} steps — {request.objective_summary or ''}", "plan", {"steps": [s.kind.value for s in plan.steps]})
    spec_content = _build_generated_spec(request)
    task = Task(
        task_id,
        request.repo_path,
        "Headless Handoff",
        request.objective_summary or request.prd,
        autonomy_level,
        linked_prd=request.linked_prd,
        linked_specs=("generated-spec.md",),
    )
    agent_runtime = AgentRuntime.create(task.id, run_id, ".nemo-runtimes")
    runtime = agent_runtime.worktree
    if use_git_worktree:
        repo_root = Path(request.repo_path).resolve()
        wt_path = repo_root / ".worktrees" / runtime.runtime_id
        runtime = replace(runtime, worktree_path=wt_path)
        branch = worktree_branch_name(runtime.runtime_id)
        try:
            initialize_git_worktree_runtime(runtime, repo_root, branch)
        except Exception:  # noqa: BLE001
            # Non-git repos or dirty state — fall back to isolated worktree
            use_git_worktree = False
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
        payload = load_execution_snapshot(checkpoint_path)
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
    if nemo_adapter is None:
        _logger.warning("nemo_adapter not provided — falling back to ephemeral InMemoryNemoAdapter; cross-session learning disabled")
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
    # NOTE: NEMO MCP's build_context_portfolio only accepts {task, topic, tags_include,
    # token_budget, mode, risk_tolerance, include_evidence_handles, limit} — the schema
    # is closed (additionalProperties: false). Do NOT pass portfolio_phase/compact here.
    adapter, portfolio_result = adapter.call(
        NemoLifecyclePhase.BUILD,
        "build_context_portfolio",
        task=_portfolio_query,
        topic=task.title,
        token_budget=_portfolio_budget,
    )
    nemo_results.append(portfolio_result)
    nemo_context = _bounded_nemo_context(_render_nemo_payload_as_context(portfolio_result.payload))
    emit_event("context_bootstrapped", f"NEMO context loaded ({len(nemo_context)} chars)", "plan")
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
        nemo_context = _bounded_nemo_context(_render_nemo_payload_as_context(search_result.payload))
    platform = get_platform_info("space-code")
    profile = model_profile or default_model_profile()
    if model_role_profile:
        editor_model = model_role_profile.model_for_role("editor")
        if editor_model:
            profile = replace(profile, model=editor_model)
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
        model_profile_link=RunModelProfileLink(
            model=profile.model,
            base_url=profile.base_url,
            provider_mode=provider_mode,
        ),
    )

    provider = mutation_provider or create_engine_provider(
        provider_mode, engine_command, cwd=Path("product/nemo_code_runtime"), use_git=use_git_worktree
    )
    engine = QualityMutationEngine(Workspace.from_path(runtime.worktree_path))

    # --- Permission System (inspired by opencode) ---
    repo_root = Path(request.repo_path).resolve()
    perm_path = Path(permissions_file) if permissions_file else repo_root / ".spacecode-permissions.json"
    ruleset = load_ruleset_from_file(perm_path) if perm_path.exists() else default_ruleset(autonomy_level)

    # --- Repo map + anchor docs (README/CLAUDE.md) ---
    _repo_map = build_repo_map(
        request.repo_path,
        cache_path=Path(request.repo_path) / ".nemo-runtimes" / "repo-map-cache.json",
        max_chars=12000,
    )
    _anchor_docs = read_anchor_docs(request.repo_path)

    # --- NEMO Learning: one-time project architecture ingestion ---
    # ingest_project_architecture checks NEMO before calling the LLM — safe every run.
    try:
        _arch_key_files: dict[str, str] = {}
        for _anchor_name in ("README.md", "CLAUDE.md"):
            _anchor_path = Path(request.repo_path).resolve() / _anchor_name
            if _anchor_path.is_file():
                _arch_key_files[_anchor_name] = _anchor_path.read_text(encoding="utf-8", errors="replace")[:2000]
        if _arch_key_files:
            ingest_project_architecture(
                adapter,
                repo_path=str(request.repo_path),
                key_files=_arch_key_files,
                base_url=profile.base_url,
                model=profile.model or "",
            )
    except Exception:  # noqa: BLE001
        pass

    # --- Planner phase: infer target files when none specified ---
    # A lightweight LLM call (30s timeout, 256 tokens) that reads the repo map
    # and returns which files need to be edited for this objective.
    # Only runs in subprocess mode and when the user didn't specify targets.
    _planner_targets: tuple[str, ...] = ()
    if not target_files and provider_mode != "fake":
        emit_event("plan_created", "Planner: inferring target files from repo map", "plan", {"step": "planner"})
        _planned = _plan_target_files(profile, request.prd, _repo_map, _anchor_docs)
        if _planned:
            _planner_targets = tuple(_planned)
            emit_event("plan_created", f"Planner: selected {len(_planner_targets)} target file(s)", "plan", {"target_files": list(_planner_targets)})

    # Honor permissions, then merge explicit targets with planner suggestions.
    _candidate_targets = list(target_files) or list(_planner_targets)
    allowed_files = []
    denied_files = []
    for f in _candidate_targets:
        if ruleset.evaluate("write_file", f) == PermissionAction.ALLOW:
            allowed_files.append(f)
        else:
            denied_files.append(f)

    if _candidate_targets and denied_files and not allowed_files:
        raise PermissionError(f"all requested target files denied by permissions policy: {', '.join(denied_files)}")

    effective_targets = tuple(allowed_files) if _candidate_targets else ()

    # Copy target files into the worktree only when NOT using a git worktree
    # (git worktrees already have the full repo accessible).
    if not use_git_worktree:
        for relative_target in effective_targets:
            source_path = repo_root / relative_target
            if not source_path.exists() or not source_path.is_file():
                continue
            runtime_target = runtime.resolve_inside(relative_target)
            runtime_target.parent.mkdir(parents=True, exist_ok=True)
            runtime_target.write_bytes(source_path.read_bytes())

    # --- NEMO workspace file structure awareness ---
    try:
        adapter, _ws_result = adapter.call(
            NemoLifecyclePhase.BUILD,
            "search_memories",
            query=f"workspace files changed {request.repo_path}",
            compact=True,
            limit=5,
            tags_include=["workspace_files"],
        )
        nemo_results.append(_ws_result)
        _ws_context = str(_ws_result.payload.get("results", "")).strip()
        if _ws_context and nemo_context:
            nemo_context = f"{nemo_context}\n\n# Recent Workspace File Changes\n{_ws_context}"
        elif _ws_context:
            nemo_context = f"# Recent Workspace File Changes\n{_ws_context}"
    except Exception:  # noqa: BLE001
        pass

    # --- NEMO Learning: inject cross-session project context ---
    try:
        _learning_ctx = build_project_context(
            adapter,
            repo_path=str(request.repo_path),
            task=task.title,
        )
        if _learning_ctx:
            nemo_context = (nemo_context + "\n\n" + _learning_ctx).strip()
    except Exception:  # noqa: BLE001
        pass

    # Prepend anchor docs (README/CLAUDE.md) to NEMO context so the agent
    # understands the repo purpose and conventions before any memories.
    # Skip in git-worktree mode: CLAUDE.md mentions file paths like
    # .aider.chat.history.md which check_for_file_mentions auto-adds
    # (--yes-always), causing 800K+ token context explosion.
    if _anchor_docs and not use_git_worktree:
        nemo_context = (f"# Project Context\n{_anchor_docs}\n\n{nemo_context}").strip()

    _generated_test_artifact: Artifact | None = None

    # --- Task Decomposition ---
    # For complex PRDs in git-worktree mode, split into sequential subtasks so each
    # coding session has a focused objective and fresh context, building incrementally.
    # Falls back to single-task flow when decomposition returns fewer than 2 subtasks.
    _subtasks: list[_SubtaskSpec] = []
    if use_git_worktree and provider_mode == "subprocess" and not resume_checkpoint_id:
        _subtasks = _decompose_prd(profile, request.prd, _repo_map, effective_targets)
        if len(_subtasks) > 1:
            emit_event(
                "plan_created",
                f"Decomposed into {len(_subtasks)} subtasks: {', '.join(s.title for s in _subtasks)}",
                "plan",
                {"subtasks": [s.title for s in _subtasks], "count": len(_subtasks)},
            )

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
        repo_map=_repo_map,
    )

    if len(_subtasks) > 1:
        # Sequential subtask execution — each builds on the previous in the same worktree.
        # Each subtask gets a focused objective, specific target files, and a mini repair budget.
        # The last subtask's result becomes mutation_result for the downstream repair pipeline.
        _prior_diff = ""
        _all_changed: list[str] = []
        _st_budget = max(2, _repair_budget_from_autonomy(autonomy_level) // len(_subtasks))
        _st_result = None
        for _st_idx, _subtask in enumerate(_subtasks):
            emit_event(
                "subtask_start",
                f"[{_st_idx + 1}/{len(_subtasks)}] {_subtask.title}",
                "execute",
                {"index": _st_idx + 1, "total": len(_subtasks), "title": _subtask.title},
            )
            _st_ctx = nemo_context
            if _prior_diff:
                _st_ctx = f"{nemo_context}\n\n# Previous Subtask Diff\n```diff\n{_prior_diff[:1500]}\n```"
            _st_req = MutationRequest(
                _subtask.objective or request.prd,
                "generated-spec.md",
                _subtask.acceptance_criteria or request.acceptance_criteria,
                _st_ctx,
                provider_mode,
                request.repo_path,
                str(runtime.worktree_path),
                _subtask.target_files or effective_targets,
                profile,
                "",
                timeout_seconds,
                repo_map=_repo_map,
            )
            _st_result = apply_mutation_request(engine, provider, _st_req)
            _changed_now = list(_st_result.changed_files or _st_result.applied_files)
            _all_changed.extend(_changed_now)
            emit_event(
                "mutation_created",
                f"[{_st_idx + 1}/{len(_subtasks)}] {_subtask.title}: {len(_changed_now)} files",
                "execute",
                {"files": _changed_now, "subtask": _subtask.title},
            )
            # Mini repair per subtask (real validation only; simulated validation skipped)
            if real_validation and _changed_now:
                _mini_cwd = runtime.worktree_path if validation_cwd == "runtime" else request.repo_path
                _mini_val = run_validation_suite(
                    request.validation_commands, cwd=_mini_cwd, timeout_seconds=timeout_seconds,
                )
                if not _mini_val.passed and _st_budget > 0:
                    _mini_repair = run_repair_loop(
                        _mini_val, request.validation_commands, fail_validation,
                        RepairBudget(_st_budget), engine, provider, _st_req,
                        nemo_adapter=adapter,
                        task_id=task.id,
                        autonomy_level=autonomy_level,
                    )
                    if _mini_repair.mutation_results:
                        _st_result = _mini_repair.mutation_results[-1]
            _prior_diff = _st_result.diff_artifact
        mutation_result = _st_result  # type: ignore[assignment]
        emit_event(
            "mutation_created",
            f"All {len(_subtasks)} subtasks complete: {len(list(dict.fromkeys(_all_changed)))} total files",
            "execute",
            {"files": list(dict.fromkeys(_all_changed))},
        )
    else:
        # Single-task flow (original path)
        mutation_result = apply_mutation_request(engine, provider, mutation_request)
        emit_event("mutation_created", f"Mutation applied: {len(mutation_result.changed_files or mutation_result.applied_files)} files", "execute", {"files": list(mutation_result.changed_files or mutation_result.applied_files)})

    # --- NEMO: persist changed files for future workspace awareness ---
    _changed = list(mutation_result.changed_files or mutation_result.applied_files)
    if _changed:
        try:
            adapter.call(
                NemoLifecyclePhase.REVIEW,
                "cognitive_ingest",
                content=(
                    f"workspace={request.repo_path} | task={task.title[:80]} | "
                    f"files_modified={_changed}"
                ),
                memory_type="workspace_files",
                tags=["workspace_files", "file_changes"],
                importance_level=6,
            )
        except Exception:  # noqa: BLE001
            pass  # Non-critical — workspace memory is additive

    if not _subtasks and _should_retry_chunked(request, mutation_result):
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
            repo_map=mutation_request.repo_map,
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
    emit_event("validation_run", f"Validation: {'passed' if validation.passed else 'failed'}", "execute", {"passed": validation.passed})
    repair_result: RepairRunResult | None = None
    seeded_repair_cursor = max(0, int(resume_repair_cursor or 0))
    seeded_attempts = tuple(
        RepairAttempt(index + 1, "resume_seed", f"restored from atomic checkpoint {resume_checkpoint_id or 'unknown'}")
        for index in range(seeded_repair_cursor)
    )
    _effective_repair_budget = request.repair_budget if request.repair_budget != 3 else _repair_budget_from_autonomy(autonomy_level)
    repair_plan = RepairPlan(RepairBudget(_effective_repair_budget), attempts=seeded_attempts)
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

        remaining_repair_attempts = max(0, _effective_repair_budget - seeded_repair_cursor)
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
                nemo_adapter=adapter,
                task_id=task.id,
                critique_fn=_make_repair_critique_fn(profile) if provider_mode != "fake" else None,
                quality_threshold=request.quality_threshold,
                autonomy_level=autonomy_level,
            )
        merged_attempts = seeded_attempts + tuple(
            RepairAttempt(seeded_repair_cursor + index + 1, item.reason, item.action)
            for index, item in enumerate(repair_result.plan.attempts)
        )
        repair_plan = RepairPlan(RepairBudget(_effective_repair_budget), attempts=merged_attempts)
        validation = repair_result.validation
        if repair_result.best_score > 0:
            emit_event(
                "repair_quality_score",
                f"Repair quality score: {repair_result.best_score:.1f}/10 ({repair_result.stop_reason or 'ok'})",
                "review",
                {"score": repair_result.best_score, "stop_reason": repair_result.stop_reason},
            )
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
                topic="Space Code self-modification",
                tags=("repair_failure", "self-mod-risk", "spacecode-repair"),
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
        # --- NEMO-Guided Repair Continuation ---
        # When the regular budget is exhausted and validation still fails,
        # call anticipate() to surface past repair patterns and grant ONE
        # extra attempt with that strategy injected into context.
        # Skipped when stop_reason is loop_detected or noop (structural, not budget).
        if (
            repair_result is not None
            and repair_result.stop_reason == "repair_budget_exhausted"
            and isinstance(adapter, PersistentNemoAdapter)
        ):
            _failed_cmds = ", ".join(r.command.command for r in validation.results if not r.passed) or "unknown"
            _task_desc = (request.objective_summary or request.prd)[:120]
            adapter, _anticipate_result = adapter.call(
                NemoLifecyclePhase.REVIEW,
                "anticipate",
                task=f"repair recovery: {_task_desc[:80]} failed=[{_failed_cmds[:100]}]",
                topic="repair_recovery",
                limit=5,
            )
            nemo_results.append(_anticipate_result)
            _memories = _anticipate_result.payload.get("memories") if isinstance(_anticipate_result.payload, dict) else []
            _strategy_lines = [
                f"- {str(mem.get('content', '')).strip()[:200]}"
                for mem in (_memories or [])
                if str(mem.get("content", "")).strip()
            ]
            if _strategy_lines:
                _strategy_text = (
                    f"NEMO repair guidance from {len(_strategy_lines)} past memory pattern(s):\n"
                    + "\n".join(_strategy_lines[:5])
                )
                emit_event(
                    "nemo_guided_repair_start",
                    f"Budget exhausted — NEMO found {len(_strategy_lines)} recovery pattern(s); attempting 1 guided repair",
                    "execute",
                    {
                        "strategy_preview": _strategy_text[:500],
                        "strategy_lines": _strategy_lines[:5],
                        "memories_found": len(_strategy_lines),
                    },
                )
                _guided_request = replace(mutation_request, context=f"{_strategy_text}\n\n{mutation_request.context}")
                _guided_result = run_repair_loop(
                    validation,
                    validation_commands,
                    fail_validation,
                    RepairBudget(1),
                    engine,
                    provider,
                    _guided_request,
                    validator=validator,
                    nemo_adapter=adapter,
                    task_id=task.id,
                    critique_fn=_make_repair_critique_fn(profile) if provider_mode != "fake" else None,
                )
                if _guided_result.validation.passed:
                    repair_result = _guided_result
                    validation = _guided_result.validation
                    emit_event(
                        "nemo_guided_repair_succeeded",
                        f"NEMO-guided repair succeeded (score={_guided_result.best_score:.1f})",
                        "execute",
                        {"score": _guided_result.best_score},
                    )
                else:
                    emit_event(
                        "nemo_guided_repair_failed",
                        "NEMO-guided repair also failed — accepting exhausted state",
                        "execute",
                        {},
                    )
            else:
                emit_event(
                    "nemo_guided_repair_no_strategy",
                    "Budget exhausted — no recovery patterns in NEMO for this failure type",
                    "execute",
                    {},
                )
    effective_mutation_result = mutation_result
    if repair_result and not (effective_mutation_result.changed_files or effective_mutation_result.applied_files):
        for repair_mutation in reversed(repair_result.mutation_results):
            if repair_mutation.changed_files or repair_mutation.applied_files:
                effective_mutation_result = repair_mutation
                break
    adapter, result = adapter.call(NemoLifecyclePhase.BUILD, "record_context_feedback", was_useful=validation.passed)
    nemo_results.append(result)

    # --- NEMO Learning: store final task outcome with correct success flag ---
    try:
        _final_files = list(effective_mutation_result.changed_files or effective_mutation_result.applied_files)
        _repair_attempts = len(repair_plan.attempts) if repair_plan else 0
        _stop = repair_result.stop_reason if repair_result else ""
        ingest_task_outcome(
            adapter,
            objective=request.objective_summary or request.prd[:120],
            repo_path=str(request.repo_path),
            files_changed=_final_files,
            result_summary=(
                f"validation={'passed' if validation.passed else 'failed'} "
                f"repair_attempts={_repair_attempts}"
                + (f" stop_reason={_stop}" if _stop else "")
            ),
            success=validation.passed,
            base_url=profile.base_url,
            model=profile.model or "",
        )
    except Exception:  # noqa: BLE001
        pass

    memory_traces = (
        NemoMemoryEvent("mem-1", run.id, "prime_context", "startup_context", "read_only", "Loaded startup context."),
        NemoMemoryEvent("mem-2", run.id, "build_context_portfolio", "context_economy", "read_only", f"Built context portfolio tokens={portfolio_result.payload.get('estimated_tokens', 0)}."),
        NemoMemoryEvent("mem-3", run.id, "store_conversation", "conversation", "memory_write", "Prepared final writeback."),
    )
    workflow_recipe = WorkflowRecipe(
        mode=request.spec_mode,
        step_kinds=tuple(step.kind.value for step in plan.steps),
        review_gate_required=plan.review_gate_required,
        can_run_unattended=plan.can_run_unattended,
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
            "Execute Space Code mutation in isolated runtime.",
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
    _generated_tests_tuple: tuple[Artifact, ...] = ((_generated_test_artifact,) if _generated_test_artifact is not None else ())
    artifacts = prd_artifacts + resume_artifacts + _generated_tests_tuple + (
        Artifact.from_content("artifact-spec", run.id, ArtifactType.SPEC, "generated-spec.md", "Generated specs", spec_content),
        Artifact.from_content("artifact-test", run.id, ArtifactType.TEST, "generated-test.txt", "Generated tests", "\n".join(request.acceptance_criteria)),
        Artifact.from_content("artifact-patch", run.id, ArtifactType.PATCH, "patch.diff", f"Applied provider mutation files={len(effective_mutation_result.changed_files)}", patch_content),
        Artifact.from_content("artifact-engine-output", run.id, ArtifactType.ENGINE_OUTPUT, "engine-output.txt", f"Engine output returncode={mutation_result.returncode}", engine_output),
        Artifact.from_content("artifact-validation", run.id, ArtifactType.VALIDATION, "validation.txt", "Validation summary", format_validation_report(validation)),
        Artifact.from_content("artifact-memory", run.id, ArtifactType.MEMORY_SUMMARY, "memory.md", "Memory writeback", f"NEMO writeback prepared. runtime_files={','.join(runtime_files)}"),
    ) + checkpoint_artifacts
    
    # Determine repair success reason
    repair_success_reason = ""
    if repair_plan and len(repair_plan.attempts) > 0:
        if validation.passed:
            repair_success_reason = "validation_passed"
        elif repair_plan.exhausted:
            repair_success_reason = "repair_budget_exhausted"
        else:
            repair_success_reason = "repair_incomplete"
    
    review = build_review_package(
        task,
        run,
        f"Mutation prepared through {mutation_result.provider} and the Space Code quality engine.",
        validation,
        memory_traces,
        mutation_result=effective_mutation_result,
        repair_attempts=len(repair_plan.attempts),
        repair_plan=repair_plan,
        repair_success_reason=repair_success_reason,
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
    emit_event("review_package_created", "Run completed — review package ready", "review")
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
        workflow_recipe=workflow_recipe,
    )