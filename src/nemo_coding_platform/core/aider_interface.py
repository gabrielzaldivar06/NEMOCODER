from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from os import environ
from pathlib import Path
from time import perf_counter
from typing import Protocol

from nemo_coding_platform.core.model_config import ModelProfile, default_model_profile
from nemo_coding_platform.core.mutations import FileWrite, MutationPlan, QualityMutationEngine
from nemo_coding_platform.core.runtime_diff import diff_snapshots, snapshot_path


AIDER_MESSAGE_FILE = ".nemo-aider-message.md"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _aider_model_name(profile: ModelProfile) -> str:
    return profile.model if "/" in profile.model else f"openai/{profile.model}"


def render_aider_message(request: MutationRequest) -> str:
    acceptance = "\n".join(f"- {item}" for item in request.acceptance_criteria) or "- Pass configured validation."
    targets = "\n".join(f"- {item}" for item in request.target_files) or "- Create or update the smallest necessary files inside this runtime."
    validation = request.validation_output.strip() or "No validation output yet."
    context = request.context.strip() or "No NEMO context supplied."
    repair = f"Repair attempt: {request.repair_attempt}" if request.repair_attempt else "Initial implementation."
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
            "",
            "# NEMO Context",
            context,
            "",
            "# Previous Validation Output",
            validation,
            "",
            "Produce the implementation now. Keep changes focused and validation-friendly.",
        )
    )


def write_aider_message(runtime_path: str | Path, request: MutationRequest) -> Path:
    target = Path(runtime_path).resolve() / AIDER_MESSAGE_FILE
    target.write_text(render_aider_message(request), encoding="utf-8")
    return target


def build_default_aider_command(profile: ModelProfile, message_file: str | Path) -> tuple[str, ...]:
    return (
        sys.executable,
        "-m",
        "aider",
        "--model",
        _aider_model_name(profile),
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
        "--no-analytics",
    )


@dataclass(frozen=True, slots=True)
class MutationRequest:
    objective: str
    spec_path: str
    acceptance_criteria: tuple[str, ...]
    context: str
    provider_mode: str = "fake"
    repo_path: str = "."
    runtime_path: str = "."
    target_files: tuple[str, ...] = ()
    model_profile: ModelProfile | None = None
    validation_output: str = ""
    timeout_seconds: float = 30.0
    repair_attempt: int = 0
    previous_diff: str = ""


@dataclass(frozen=True, slots=True)
class MutationResult:
    provider: str
    plan: MutationPlan
    dry_run_files: tuple[str, ...]
    applied_files: tuple[str, ...]
    summary: str
    provider_mode: str = "fake"
    model_profile: ModelProfile | None = None
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    changed_files: tuple[str, ...] = ()
    diff_artifact: str = ""
    duration_ms: int = 0


class AiderProvider(Protocol):
    name: str

    def create_plan(self, request: MutationRequest) -> MutationPlan:
        ...


class FakeAiderProvider:
    name = "fake-aider"

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


class SubprocessAiderProvider:
    name = "subprocess-aider"

    def __init__(self, command: tuple[str, ...] | None = None, cwd: str | Path = ".") -> None:
        self.command = command
        self.cwd = Path(cwd)
        self.last_returncode: int | None = None
        self.last_stdout = ""
        self.last_stderr = ""
        self.last_command: tuple[str, ...] = ()
        self.last_message_file = ""

    def create_plan(self, request: MutationRequest) -> MutationPlan:
        profile = request.model_profile or default_model_profile()
        cwd = Path(request.runtime_path).resolve() if request.runtime_path else self.cwd
        message_file = write_aider_message(cwd, request)
        command = self.command or build_default_aider_command(profile, message_file)
        product_aider = str(_repo_root() / "product" / "aider")
        existing_pythonpath = environ.get("PYTHONPATH", "")
        pythonpath = product_aider if not existing_pythonpath else f"{product_aider};{existing_pythonpath}"
        self.last_command = tuple(command)
        self.last_message_file = str(message_file)
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                shell=False,
                capture_output=True,
                text=True,
                timeout=request.timeout_seconds,
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
                    "NEMO_AIDER_MESSAGE_FILE": str(message_file),
                    "PYTHONPATH": pythonpath,
                },
            )
        except subprocess.TimeoutExpired as error:
            self.last_returncode = None
            self.last_stdout = (error.stdout or "").strip() if isinstance(error.stdout, str) else ""
            self.last_stderr = f"subprocess timed out after {request.timeout_seconds} seconds"
            return MutationPlan(writes=())
        self.last_returncode = completed.returncode
        self.last_stdout = completed.stdout.strip()
        self.last_stderr = completed.stderr.strip()
        return MutationPlan(writes=())


def apply_mutation_request(engine: QualityMutationEngine, provider: AiderProvider, request: MutationRequest) -> MutationResult:
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
    return MutationResult(
        provider.name,
        approved_plan,
        dry_run.files,
        applied,
        f"changed {len(runtime_diff.changed_files)} file(s) through {provider.name}",
        request.provider_mode,
        profile,
        getattr(provider, "last_returncode", None),
        getattr(provider, "last_stdout", ""),
        getattr(provider, "last_stderr", ""),
        runtime_diff.changed_files,
        runtime_diff.unified_diff,
        elapsed,
    )
