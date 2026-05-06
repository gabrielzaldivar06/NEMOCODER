from __future__ import annotations

import shlex
import subprocess
import sys
from dataclasses import dataclass
from enum import StrEnum
from os import name as os_name
from pathlib import Path


class ValidationStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


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


def run_validation_suite(
    commands: tuple[str, ...],
    cwd: str | Path = ".",
    timeout_seconds: float = 30.0,
    allow_shell: bool = False,
) -> ValidationSuiteResult:
    if allow_shell:
        raise ValueError("shell validation is disabled for this safe runner")
    results: list[ValidationResult] = []
    for command in commands:
        validation_command = ValidationCommand(command)
        try:
            completed = subprocess.run(
                shlex.split(command, posix=os_name != "nt"),
                cwd=Path(cwd),
                shell=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
            output = (completed.stdout + completed.stderr).strip()
            status = ValidationStatus.PASSED if completed.returncode == 0 else ValidationStatus.FAILED
            results.append(ValidationResult(validation_command, status, output, completed.returncode))
        except (FileNotFoundError, subprocess.SubprocessError, ValueError) as error:
            results.append(ValidationResult(validation_command, ValidationStatus.FAILED, str(error), None))
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