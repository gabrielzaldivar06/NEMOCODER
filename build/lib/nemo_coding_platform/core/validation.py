from __future__ import annotations

import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from enum import StrEnum
from os import name as os_name
from pathlib import Path


class ValidationStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ValidationPolicy(StrEnum):
    NONE = "none"
    SMOKE = "smoke"
    TARGETED = "targeted"
    FULL = "full"


VALIDATION_SKIPPED_COMMAND = "validation skipped by policy:none"


@dataclass(frozen=True, slots=True)
class ValidationCommand:
    command: str
    required: bool = True


@dataclass(frozen=True, slots=True)
class ValidationResult:
    command: ValidationCommand
    status: ValidationStatus
    output: str = ""
    returncode: int | None = None

    @property
    def passed(self) -> bool:
        return self.status == ValidationStatus.PASSED


@dataclass(frozen=True, slots=True)
class ValidationSuiteResult:
    results: tuple[ValidationResult, ...]

    @property
    def passed(self) -> bool:
        return all(result.passed or not result.command.required for result in self.results)

    def summary(self) -> str:
        passed = sum(1 for result in self.results if result.passed)
        return f"validation passed={passed}/{len(self.results)} required_ok={self.passed}"


def format_validation_report(validation: ValidationSuiteResult) -> str:
    lines = [validation.summary()]
    for result in validation.results:
        lines.extend(
            (
                f"command={result.command.command}",
                f"status={result.status.value}",
                f"returncode={result.returncode}",
                "output:",
                result.output or "<empty>",
                "",
            )
        )
    return "\n".join(lines).rstrip()


def simulate_validation(commands: tuple[str, ...], fail_commands: tuple[str, ...] = ()) -> ValidationSuiteResult:
    failures = set(fail_commands)
    results = tuple(
        ValidationResult(
            ValidationCommand(command),
            ValidationStatus.FAILED if command in failures else ValidationStatus.PASSED,
            "simulated failure" if command in failures else "simulated pass",
        )
        for command in commands
    )
    return ValidationSuiteResult(results)


def validation_commands_for_policy(policy: str, explicit_commands: tuple[str, ...] = ()) -> tuple[str, ...]:
    active_policy = ValidationPolicy(policy)
    if explicit_commands:
        return explicit_commands
    if active_policy == ValidationPolicy.NONE:
        return (VALIDATION_SKIPPED_COMMAND,)
    if active_policy == ValidationPolicy.SMOKE:
        return (f"{sys.executable} --version",)
    return ("python -m unittest",)


def run_validation_suite(
    commands: tuple[str, ...],
    cwd: str | Path = ".",
    timeout_seconds: float = 30.0,
    allow_shell: bool = False,
    time_budget_seconds: float | None = None,
    escalation_mode: bool = False,
    slow_command_timeout_seconds: float = 30.0,
) -> ValidationSuiteResult:
    if allow_shell:
        raise ValueError("shell validation is disabled for this safe runner")
    results: list[ValidationResult] = []
    suite_start = time.time()
    budget_exhausted = False
    for command in commands:
        validation_command = ValidationCommand(command)
        elapsed = time.time() - suite_start
        if time_budget_seconds is not None and elapsed >= time_budget_seconds:
            budget_exhausted = True
            results.append(
                ValidationResult(
                    validation_command,
                    ValidationStatus.FAILED,
                    "validation time budget exhausted before command execution",
                    None,
                )
            )
            continue

        if escalation_mode and timeout_seconds > slow_command_timeout_seconds:
            validation_command = ValidationCommand(command, required=False)
            results.append(
                ValidationResult(
                    validation_command,
                    ValidationStatus.SKIPPED,
                    "skipped in escalation mode (slow validation command)",
                    None,
                )
            )
            continue

        command_timeout = timeout_seconds
        if time_budget_seconds is not None:
            remaining = time_budget_seconds - elapsed
            command_timeout = min(timeout_seconds, max(1.0, remaining))

        try:
            completed = subprocess.run(
                shlex.split(command, posix=os_name != "nt"),
                cwd=Path(cwd),
                shell=False,
                capture_output=True,
                text=True,
                timeout=command_timeout,
                check=False,
            )
            output = (completed.stdout + completed.stderr).strip()
            status = ValidationStatus.PASSED if completed.returncode == 0 else ValidationStatus.FAILED
            results.append(ValidationResult(validation_command, status, output, completed.returncode))
        except subprocess.TimeoutExpired as error:
            results.append(
                ValidationResult(
                    validation_command,
                    ValidationStatus.FAILED,
                    f"validation command timed out after {command_timeout:.1f}s: {error}",
                    None,
                )
            )
        except (FileNotFoundError, subprocess.SubprocessError, ValueError) as error:
            results.append(ValidationResult(validation_command, ValidationStatus.FAILED, str(error), None))
    if budget_exhausted and not any(result.command.command == "validation budget exhausted" for result in results):
        results.append(
            ValidationResult(
                ValidationCommand("validation budget exhausted"),
                ValidationStatus.FAILED,
                "validation time budget exhausted during suite",
                None,
            )
        )
    return ValidationSuiteResult(tuple(results))


def write_python_validation_script(
    cwd: str | Path,
    script_name: str,
    source: str,
    python_executable: str | Path | None = None,
) -> str:
    if Path(script_name).name != script_name:
        raise ValueError("validation script name must not contain directories")
    target = Path(cwd).resolve() / script_name
    target.write_text(source, encoding="utf-8")
    executable = str(python_executable or sys.executable)
    parts = (executable, str(target))
    return subprocess.list2cmdline(parts) if os_name == "nt" else shlex.join(parts)