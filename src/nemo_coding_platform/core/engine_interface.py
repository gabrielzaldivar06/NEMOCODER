from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from os import environ
from pathlib import Path
from time import perf_counter
from typing import Protocol

from nemo_coding_platform.core.model_config import ModelProfile, default_model_profile
from nemo_coding_platform.core.mutations import FileWrite, MutationPlan, QualityMutationEngine
from nemo_coding_platform.core.role_execution_profile import RoleExecutionProfile
from nemo_coding_platform.core.runtime_diff import diff_snapshots, snapshot_path
from nemo_coding_platform.core.workspace import mask_host_paths


ENGINE_MESSAGE_FILE = ".nemo-engine-message.md"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _runtime_model_name(profile: ModelProfile) -> str:
    return profile.model if "/" in profile.model else f"openai/{profile.model}"


def render_engine_message(request: MutationRequest) -> str:
    acceptance = "\n".join(f"- {item}" for item in request.acceptance_criteria) or "- Pass configured validation."
    targets = "\n".join(f"- {item}" for item in request.target_files) or "- Create or update the smallest necessary files inside this runtime."
    context = request.context.strip() or "No NEMO context supplied."
    repair = f"Repair attempt: {request.repair_attempt}" if request.repair_attempt else "Initial implementation."
    skill_section = ("\n", "# Skill Guidance", request.skill_prompt) if request.skill_prompt.strip() else ()
    design_section = ("\n", "# Design Reference", f"An image has been provided for this task: {request.image_path}\nUse your vision capabilities to analyze it and implement the UI accordingly.") if request.image_path else ()
    # On repair attempts the context already contains a '# Repair Evidence' block
    # with the same information, so skip the separate section to avoid duplication.
    validation_section: tuple[str, ...]
    if request.repair_attempt:
        validation_section = ()
    else:
        raw = request.validation_output.strip()
        validation_section = ("\n", "# Previous Validation Output", raw) if raw else ()
    return "\n".join(
        (
            "You are implementing a NEMO Full Handoff task inside an isolated runtime worktree.",
            "Only modify files inside the current working directory. Do not ask follow-up questions.",
            repair,
            "",
            "# Objective",
            request.objective,
            "",
            "# Acceptance Criteria",
            acceptance,
            "",
            "# Target Files",
            targets,
            *skill_section,
            *design_section,
            "",
            "# NEMO Context",
            context,
            *validation_section,
            "",
            "# Memory Tools",
            "Use `!python -m nemo_code_runtime.nemo_platform call search_memories --args '{\"query\": \"...\"}' only for missing APIs. If context includes `evidence_handle=...`, expand with `... call expand_context_evidence --args '{\"handle\": \"...\"}'`.",
            "",
            "Produce the implementation now. Keep changes focused and validation-friendly.",
        )
    )


def write_engine_message(runtime_path: str | Path, request: MutationRequest) -> Path:
    target = Path(runtime_path).resolve() / ENGINE_MESSAGE_FILE
    target.write_text(render_engine_message(request), encoding="utf-8")
    return target


def build_default_engine_command(profile: ModelProfile, message_file: str | Path) -> tuple[str, ...]:
    return (
        sys.executable,
        "-m",
        "nemo_code_runtime",
        "--model",
        _runtime_model_name(profile),
        "--openai-api-base",
        profile.base_url,
        "--openai-api-key",
        profile.api_key,
        "--message-file",
        str(message_file),
        "--yes-always",
        "--no-git",
        "--no-auto-commits",
        "--no-dirty-commits",
        "--no-gitignore",
        "--no-show-model-warnings",
        "--no-analytics",
    )


def _is_recursive_platform_command(command: tuple[str, ...]) -> bool:
    lowered = tuple(item.lower() for item in command)
    if "nemo_coding_platform" not in lowered:
        return False
    recursive_entrypoints = {"long-handoff-run", "headless-run", "self-modify", "mission-control-server"}
    return any(item in recursive_entrypoints for item in lowered)


@dataclass(frozen=True, slots=True)
class MutationRequest:
    objective: str
    spec_path: str
    acceptance_criteria: tuple[str, ...]
    context: str
    provider_mode: str = "subprocess"
    repo_path: str = "."
    runtime_path: str = "."
    target_files: tuple[str, ...] = ()
    model_profile: ModelProfile | None = None
    validation_output: str = ""
    timeout_seconds: float = 30.0
    repair_attempt: int = 0
    previous_diff: str = ""
    skill_prompt: str = ""
    image_path: str = ""
    role: str = ""  # Optional role (planner, editor, reviewer, summarizer) for role-specific timeout/error handling


@dataclass(frozen=True, slots=True)
class TokenUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    source: str = "real"
    model_name: str = ""


@dataclass(frozen=True, slots=True)
class MutationResult:
    provider: str
    plan: MutationPlan
    dry_run_files: tuple[str, ...]
    applied_files: tuple[str, ...]
    summary: str
    provider_mode: str = "subprocess"
    model_profile: ModelProfile | None = None
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    changed_files: tuple[str, ...] = ()
    diff_artifact: str = ""
    duration_ms: int = 0
    token_usage: TokenUsage | None = None
    role: str = ""  # Role (planner, editor, reviewer, summarizer) that performed this mutation


class EngineProvider(Protocol):
    name: str

    def create_plan(self, request: MutationRequest) -> MutationPlan:
        ...


class FakeEngineProvider:
    name = "fake-space-code"

    def create_plan(self, request: MutationRequest) -> MutationPlan:
        content = "\n".join(
            (
                "# Generated Implementation",
                "",
                f"Objective: {request.objective}",
                f"Spec: {request.spec_path}",
                "",
                "## Acceptance Criteria",
                *(f"- {item}" for item in request.acceptance_criteria),
                "",
                "## NEMO Context",
                request.context or "No context supplied.",
            )
        )
        return MutationPlan(writes=(FileWrite("generated-implementation.md", content),))


class SubprocessEngineProvider:
    name = "subprocess-space-code"

    def __init__(self, command: tuple[str, ...] | None = None, cwd: str | Path = ".") -> None:
        self.command = command
        self.cwd = Path(cwd)
        self.last_returncode: int | None = None
        self.last_stdout = ""
        self.last_stderr = ""
        self.last_command: tuple[str, ...] = ()
        self.last_message_file = ""
        self.last_usage: TokenUsage | None = None

    @staticmethod
    def _parse_token_usage_payload(payload: dict[str, object], profile: ModelProfile) -> TokenUsage | None:
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            return None
        prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        completion = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        total = int(usage.get("total_tokens") or (prompt + completion))
        if total <= 0:
            return None
        model_name = str(payload.get("model") or profile.model)
        return TokenUsage(
            prompt_tokens=max(0, prompt),
            completion_tokens=max(0, completion),
            total_tokens=max(0, total),
            source="real",
            model_name=model_name,
        )

    def _extract_token_usage(self, stdout: str, runtime_path: Path, profile: ModelProfile) -> TokenUsage | None:
        sidecar = runtime_path / ".nemo-token-usage.json"
        if sidecar.exists():
            try:
                payload = json.loads(sidecar.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    usage = self._parse_token_usage_payload(payload, profile)
                    if usage:
                        return usage
            except (json.JSONDecodeError, OSError, ValueError):
                pass

        marker = "NEMO_TOKEN_USAGE_JSON="
        for line in stdout.splitlines():
            if not line.startswith(marker):
                continue
            try:
                payload = json.loads(line[len(marker):].strip())
                if isinstance(payload, dict):
                    usage = self._parse_token_usage_payload(payload, profile)
                    if usage:
                        return usage
            except (json.JSONDecodeError, ValueError):
                continue
        return None

    def create_plan(self, request: MutationRequest) -> MutationPlan:
        profile = request.model_profile or default_model_profile()
        cwd = Path(request.runtime_path).resolve() if request.runtime_path else self.cwd
        message_file = write_engine_message(cwd, request)
        using_default_command = self.command is None
        command = self.command or build_default_engine_command(profile, message_file)
        if _is_recursive_platform_command(tuple(command)):
            using_default_command = True
            command = build_default_engine_command(profile, message_file)
            self.last_stderr = (
                "recursive engine command detected; "
                "falling back to default nemo_code_runtime invocation"
            )
        if using_default_command and request.target_files:
            command = (*command, *request.target_files)

        # Determine effective timeout: apply role-specific constraints if role is specified
        effective_timeout = request.timeout_seconds
        if request.role:
            role_profile = RoleExecutionProfile(request.role, "subprocess", request.timeout_seconds)
            effective_timeout = role_profile.effective_timeout_seconds

        root = _repo_root()
        embedded_runtime = str(root / "product" / "nemo_code_runtime")
        project_src = str(root / "src")

        existing_pythonpath = environ.get("PYTHONPATH", "")
        pythonpath = ";".join(filter(None, [embedded_runtime, project_src, existing_pythonpath]))
        db_path = environ.get("NEMO_DB_PATH", str(root / ".nemo-memory.db"))

        self.last_command = tuple(command)
        self.last_message_file = str(message_file)
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                shell=False,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                check=False,
                env={
                    **environ,
                    "LMSTUDIO_BASE_URL": profile.base_url,
                    "LMSTUDIO_MODEL": profile.model,
                    "OPENAI_API_BASE": profile.base_url,
                    "OPENAI_API_KEY": profile.api_key,
                    "NEMO_RUNTIME_PATH": str(cwd),
                    "NEMO_REPO_PATH": request.repo_path,
                    "NEMO_MUTATION_OBJECTIVE": request.objective,
                    "NEMO_MUTATION_SPEC_PATH": request.spec_path,
                    "NEMO_ENGINE_MESSAGE_FILE": str(message_file),
                    "NEMO_DB_PATH": db_path,
                    "PYTHONPATH": pythonpath,
                },
            )
        except subprocess.TimeoutExpired as error:
            self.last_returncode = None
            self.last_stdout = (error.stdout or "").strip() if isinstance(error.stdout, str) else ""
            self.last_stderr = f"subprocess timed out after {effective_timeout} seconds (role={request.role or 'none'})"
            return MutationPlan(writes=())
        self.last_returncode = completed.returncode
        self.last_stdout = completed.stdout.strip()
        completed_stderr = completed.stderr.strip()
        if self.last_stderr and completed_stderr:
            self.last_stderr = f"{self.last_stderr}; {completed_stderr}"
        elif completed_stderr:
            self.last_stderr = completed_stderr
        self.last_usage = self._extract_token_usage(self.last_stdout, cwd, profile)
        return MutationPlan(writes=())


def create_engine_provider(
    provider_mode: str,
    command: tuple[str, ...] | None = None,
    cwd: str | Path = ".",
) -> EngineProvider:
    if provider_mode == "fake":
        return FakeEngineProvider()
    if provider_mode == "subprocess":
        return SubprocessEngineProvider(command, cwd=cwd)
    raise ValueError(f"unsupported Space Code provider mode: {provider_mode}")


def apply_mutation_request(engine: QualityMutationEngine, provider: EngineProvider, request: MutationRequest) -> MutationResult:
    before = snapshot_path(engine.workspace.root)
    started = perf_counter()
    plan = provider.create_plan(request)
    dry_run = engine.dry_run(plan)
    approved_plan = MutationPlan(writes=plan.writes, approved=True, dry_run_completed=True)
    applied = engine.apply(approved_plan)
    elapsed = int((perf_counter() - started) * 1000)
    after = snapshot_path(engine.workspace.root)
    runtime_diff = diff_snapshots(before, after)
    profile = request.model_profile or default_model_profile()
    raw_stdout = getattr(provider, "last_stdout", "")
    raw_stderr = getattr(provider, "last_stderr", "")
    safe_stdout = mask_host_paths(raw_stdout, engine.workspace)
    safe_stderr = mask_host_paths(raw_stderr, engine.workspace)
    token_usage = getattr(provider, "last_usage", None)
    return MutationResult(
        provider.name,
        approved_plan,
        dry_run.files,
        applied,
        f"changed {len(runtime_diff.changed_files)} file(s) through {provider.name}",
        request.provider_mode,
        profile,
        getattr(provider, "last_returncode", None),
        safe_stdout,
        safe_stderr,
        runtime_diff.changed_files,
        runtime_diff.unified_diff,
        elapsed,
        token_usage,
        request.role,
    )
