from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, median
import sys
from time import perf_counter
from typing import Any

from nemo_coding_platform.core.headless_handoff import HandoffRequest
from nemo_coding_platform.core.headless_runner import execute_headless_handoff
from nemo_coding_platform.core.model_config import ModelProfile, default_model_profile
from nemo_coding_platform.core.nemo_adapter import InMemoryNemoAdapter
from nemo_coding_platform.core.validation import ValidationSuiteResult


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    case_id: str
    objective: str
    acceptance_criteria: tuple[str, ...]
    target_files: tuple[str, ...]
    validation_python_scripts: tuple[str, ...] = ()
    repair_budget: int = 1
    setup_files: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class BenchmarkIteration:
    case_id: str
    iteration: int
    wall_time_ms: int
    mutation_duration_ms: int
    changed_files_count: int
    output_chars: int
    validation_passed: bool
    repair_attempts: int
    portfolio_tokens: int
    token_usage_total: int
    token_usage_prompt: int
    token_usage_completion: int
    token_efficiency_per_file: float | None
    first_pass: bool
    repair_used: bool
    repair_success: bool
    memory_calls: int
    timeline_events: int
    artifacts_count: int
    success: bool
    validation_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "iteration": self.iteration,
            "wall_time_ms": self.wall_time_ms,
            "mutation_duration_ms": self.mutation_duration_ms,
            "changed_files_count": self.changed_files_count,
            "output_chars": self.output_chars,
            "validation_passed": self.validation_passed,
            "validation_summary": self.validation_summary,
            "repair_attempts": self.repair_attempts,
            "portfolio_tokens": self.portfolio_tokens,
            "token_usage_total": self.token_usage_total,
            "token_usage_prompt": self.token_usage_prompt,
            "token_usage_completion": self.token_usage_completion,
            "token_efficiency_per_file": self.token_efficiency_per_file,
            "first_pass": self.first_pass,
            "repair_used": self.repair_used,
            "repair_success": self.repair_success,
            "memory_calls": self.memory_calls,
            "timeline_events": self.timeline_events,
            "artifacts_count": self.artifacts_count,
            "success": self.success,
            "tokens_per_second": _per_second(self.token_usage_total, self.wall_time_ms),
            "chars_per_second": _per_second(self.output_chars, self.wall_time_ms),
        }


@dataclass(frozen=True, slots=True)
class LLMBenchmarkReport:
    model: str
    base_url: str
    provider: str
    repo_path: str
    repeats: int
    warmup: bool
    started_at: str
    completed_at: str
    cases: tuple[BenchmarkCase, ...]
    iterations: tuple[BenchmarkIteration, ...]

    def to_dict(self) -> dict[str, Any]:
        items = [item.to_dict() for item in self.iterations]
        return {
            "schema_version": 1,
            "model": self.model,
            "base_url": self.base_url,
            "provider": self.provider,
            "repo_path": self.repo_path,
            "repeats": self.repeats,
            "warmup": self.warmup,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "cases": [
                {
                    "case_id": case.case_id,
                    "objective": case.objective,
                    "acceptance_criteria": list(case.acceptance_criteria),
                    "target_files": list(case.target_files),
                }
                for case in self.cases
            ],
            "iterations": items,
            "summary": summarize_iterations(items),
            "case_summaries": summarize_iterations_by_case(items),
        }


def standard_benchmark_cases() -> tuple[BenchmarkCase, ...]:
    return (
        BenchmarkCase(
            case_id="mbpp_like_codegen",
            objective=(
                "Create bench-output/algorithms.py with functions is_palindrome(text: str) -> bool and "
                "chunk_sum(values: list[int], chunk_size: int) -> list[int]."
            ),
            acceptance_criteria=(
                "bench-output/algorithms.py exists",
                "is_palindrome and chunk_sum pass validation tests",
            ),
            target_files=("bench-output/algorithms.py",),
            validation_python_scripts=(
                """
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

path = Path("bench-output/algorithms.py")
assert path.exists(), "algorithms.py missing"
spec = spec_from_file_location("algorithms", path)
assert spec and spec.loader
mod = module_from_spec(spec)
spec.loader.exec_module(mod)

assert mod.is_palindrome("ana") is True
assert mod.is_palindrome("python") is False
assert mod.is_palindrome("A man a plan a canal Panama") is True
assert mod.chunk_sum([1, 2, 3, 4, 5], 2) == [3, 7, 5]
assert mod.chunk_sum([10, 20, 30], 4) == [60]
""".strip(),
            ),
        ),
        BenchmarkCase(
            case_id="quixbugs_like_repair",
            objective=(
                "Fix bench-output/buggy_stats.py: mean(values) must return the arithmetic mean and "
                "raise ValueError on empty input. Keep API unchanged."
            ),
            acceptance_criteria=(
                "bench-output/buggy_stats.py fixed",
                "mean passes functional tests",
            ),
            target_files=("bench-output/buggy_stats.py",),
            setup_files=(
                (
                    "bench-output/buggy_stats.py",
                    """
def mean(values: list[float]) -> float:
    if not values:
        return 0.0
    total = sum(values)
    # BUG: wrong denominator
    return total / (len(values) - 1)
""".strip()
                    + "\n",
                ),
            ),
            validation_python_scripts=(
                """
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

path = Path("bench-output/buggy_stats.py")
assert path.exists(), "buggy_stats.py missing"
spec = spec_from_file_location("buggy_stats", path)
assert spec and spec.loader
mod = module_from_spec(spec)
spec.loader.exec_module(mod)

assert abs(mod.mean([2, 4, 6]) - 4.0) < 1e-9
assert abs(mod.mean([1.5, 2.5]) - 2.0) < 1e-9
try:
    mod.mean([])
except ValueError:
    pass
else:
    raise AssertionError("mean([]) must raise ValueError")
""".strip(),
            ),
        ),
        BenchmarkCase(
            case_id="agentic_iterative_delivery",
            objective=(
                "Create bench-output/records.json with exactly 30 objects {id:int, name:str, score:int} "
                "and bench-output/summary.py with function top_scores(path: str, limit: int) -> list[int]."
            ),
            acceptance_criteria=(
                "records.json has 30 objects",
                "top_scores returns sorted descending scores with requested limit",
            ),
            target_files=("bench-output/records.json", "bench-output/summary.py"),
            validation_python_scripts=(
                """
import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

records_path = Path("bench-output/records.json")
summary_path = Path("bench-output/summary.py")
assert records_path.exists(), "records.json missing"
assert summary_path.exists(), "summary.py missing"
records = json.loads(records_path.read_text(encoding="utf-8"))
assert isinstance(records, list)
assert len(records) == 30
assert all(set(item.keys()) == {"id", "name", "score"} for item in records)

spec = spec_from_file_location("summary", summary_path)
assert spec and spec.loader
mod = module_from_spec(spec)
spec.loader.exec_module(mod)

scores = mod.top_scores(str(records_path), 5)
assert isinstance(scores, list)
assert len(scores) == 5
assert scores == sorted(scores, reverse=True)
""".strip(),
            ),
        ),
    )


def quick_benchmark_cases() -> tuple[BenchmarkCase, ...]:
    return (
        BenchmarkCase(
            case_id="quick_smoke_codegen",
            objective="Create bench-output/quick_math.py with function add(a: int, b: int) -> int returning the arithmetic sum.",
            acceptance_criteria=(
                "bench-output/quick_math.py exists",
                "add returns expected sums",
            ),
            target_files=("bench-output/quick_math.py",),
            validation_python_scripts=(
                """
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

path = Path("bench-output/quick_math.py")
assert path.exists(), "quick_math.py missing"
spec = spec_from_file_location("quick_math", path)
assert spec and spec.loader
mod = module_from_spec(spec)
spec.loader.exec_module(mod)

assert mod.add(2, 3) == 5
assert mod.add(-1, 1) == 0
""".strip(),
            ),
            repair_budget=0,
        ),
        BenchmarkCase(
            case_id="quick_smoke_repair",
            objective="Fix bench-output/quick_bug.py so increment(n: int) returns n + 1.",
            acceptance_criteria=(
                "bench-output/quick_bug.py fixed",
                "increment returns n + 1",
            ),
            target_files=("bench-output/quick_bug.py",),
            setup_files=(
                (
                    "bench-output/quick_bug.py",
                    """
def increment(n: int) -> int:
    return n - 1
""".strip()
                    + "\n",
                ),
            ),
            validation_python_scripts=(
                """
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

path = Path("bench-output/quick_bug.py")
assert path.exists(), "quick_bug.py missing"
spec = spec_from_file_location("quick_bug", path)
assert spec and spec.loader
mod = module_from_spec(spec)
spec.loader.exec_module(mod)

assert mod.increment(0) == 1
assert mod.increment(9) == 10
""".strip(),
            ),
            repair_budget=0,
        ),
    )


def quick_quality_benchmark_cases() -> tuple[BenchmarkCase, ...]:
    quick_cases = quick_benchmark_cases()
    # Quality profile: enable one bounded repair step for each quick case so
    # codegen failures can recover without changing the suite shape.
    return tuple(replace(case, repair_budget=1) for case in quick_cases)


def benchmark_cases_for_suite(suite: str) -> tuple[BenchmarkCase, ...]:
    normalized = str(suite).strip().lower()
    if normalized == "quick":
        return quick_benchmark_cases()
    if normalized in {"quick-quality", "quick_quality"}:
        return quick_quality_benchmark_cases()
    if normalized == "standard":
        return standard_benchmark_cases()
    raise ValueError(f"unknown benchmark suite: {suite}")


def default_benchmark_cases() -> tuple[BenchmarkCase, ...]:
    return quick_benchmark_cases()


def run_llm_benchmark(
    *,
    repo_path: str,
    model_profile: ModelProfile | None = None,
    provider_mode: str = "subprocess",
    engine_command: tuple[str, ...] | None = None,
    timeout_seconds: float = 300.0,
    repeats: int = 3,
    warmup: bool = True,
    cases: tuple[BenchmarkCase, ...] | None = None,
    suite: str = "quick",
    nemo_adapter: Any | None = None,
) -> LLMBenchmarkReport:
    if repeats <= 0:
        raise ValueError("repeats must be > 0")

    profile = model_profile or default_model_profile()
    selected_cases = cases or benchmark_cases_for_suite(suite)
    started_at = _utc_now()
    results: list[BenchmarkIteration] = []

    if warmup:
        _run_case_once(
            case=selected_cases[0],
            iteration=0,
            repo_path=repo_path,
            model_profile=profile,
            provider_mode=provider_mode,
            engine_command=engine_command,
            timeout_seconds=timeout_seconds,
            nemo_adapter=nemo_adapter,
        )

    for iteration in range(1, repeats + 1):
        for case in selected_cases:
            results.append(
                _run_case_once(
                    case=case,
                    iteration=iteration,
                    repo_path=repo_path,
                    model_profile=profile,
                    provider_mode=provider_mode,
                    engine_command=engine_command,
                    timeout_seconds=timeout_seconds,
                    nemo_adapter=nemo_adapter,
                )
            )

    completed_at = _utc_now()
    return LLMBenchmarkReport(
        model=profile.model,
        base_url=profile.base_url,
        provider=provider_mode,
        repo_path=repo_path,
        repeats=repeats,
        warmup=warmup,
        started_at=started_at,
        completed_at=completed_at,
        cases=selected_cases,
        iterations=tuple(results),
    )


def save_benchmark_report(report: LLMBenchmarkReport, path: str | Path) -> Path:
    import json

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return target


def summarize_iterations(items: list[dict[str, Any]]) -> dict[str, Any]:
    if not items:
        return {
            "count": 0,
            "success_rate": 0.0,
            "avg_wall_time_ms": 0.0,
            "median_wall_time_ms": 0.0,
            "avg_mutation_duration_ms": 0.0,
            "avg_tokens_per_second": 0.0,
            "avg_chars_per_second": 0.0,
            "avg_portfolio_tokens": 0.0,
            "avg_changed_files": 0.0,
            "noop_rate": 0.0,
            "validation_pass_rate": 0.0,
            "first_pass_rate": 0.0,
            "repair_success_rate": 0.0,
            "avg_repair_attempts": 0.0,
            "avg_memory_calls": 0.0,
            "avg_timeline_events": 0.0,
            "avg_artifacts": 0.0,
            "avg_token_efficiency_per_file": 0.0,
        }

    success_count = sum(1 for item in items if item["success"])
    validation_count = sum(1 for item in items if item["validation_passed"])
    wall_times = [int(item["wall_time_ms"]) for item in items]
    mutation_times = [int(item["mutation_duration_ms"]) for item in items]
    tokens_per_second = [float(item["tokens_per_second"]) for item in items if item["tokens_per_second"] is not None]
    chars_per_second = [float(item["chars_per_second"]) for item in items if item["chars_per_second"] is not None]
    portfolio_tokens = [int(item["portfolio_tokens"]) for item in items]
    changed_files = [int(item["changed_files_count"]) for item in items]
    repair_attempts = [int(item["repair_attempts"]) for item in items]
    memory_calls = [int(item["memory_calls"]) for item in items]
    timeline_events = [int(item["timeline_events"]) for item in items]
    artifacts_count = [int(item["artifacts_count"]) for item in items]
    token_efficiency = [float(item["token_efficiency_per_file"]) for item in items if item["token_efficiency_per_file"] is not None]
    first_pass_count = sum(1 for item in items if item["first_pass"])
    repair_used_count = sum(1 for item in items if item["repair_used"])
    repair_success_count = sum(1 for item in items if item["repair_success"])
    noops = sum(1 for item in items if item["changed_files_count"] == 0)

    return {
        "count": len(items),
        "success_rate": round(success_count / len(items), 4),
        "avg_wall_time_ms": round(mean(wall_times), 2),
        "median_wall_time_ms": round(float(median(wall_times)), 2),
        "avg_mutation_duration_ms": round(mean(mutation_times), 2),
        "avg_tokens_per_second": round(mean(tokens_per_second), 2) if tokens_per_second else 0.0,
        "avg_chars_per_second": round(mean(chars_per_second), 2) if chars_per_second else 0.0,
        "avg_portfolio_tokens": round(mean(portfolio_tokens), 2),
        "avg_changed_files": round(mean(changed_files), 2),
        "noop_rate": round(noops / len(items), 4),
        "validation_pass_rate": round(validation_count / len(items), 4),
        "first_pass_rate": round(first_pass_count / len(items), 4),
        "repair_success_rate": round(repair_success_count / repair_used_count, 4) if repair_used_count else 0.0,
        "avg_repair_attempts": round(mean(repair_attempts), 2),
        "avg_memory_calls": round(mean(memory_calls), 2),
        "avg_timeline_events": round(mean(timeline_events), 2),
        "avg_artifacts": round(mean(artifacts_count), 2),
        "avg_token_efficiency_per_file": round(mean(token_efficiency), 2) if token_efficiency else 0.0,
    }


def summarize_iterations_by_case(items: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        grouped.setdefault(str(item["case_id"]), []).append(item)
    return {case_id: summarize_iterations(entries) for case_id, entries in grouped.items()}


def _extract_validation_summary(validation: ValidationSuiteResult) -> dict[str, Any]:
    """Extract failed command details from validation results for diagnostics."""
    failed_commands = []
    first_error_returncode = None
    first_error_output_preview = ""

    # Handle both real ValidationSuiteResult and mock/SimpleNamespace objects
    results = getattr(validation, "results", ())
    if not results:
        return {}

    for result in results:
        if not result.passed and result.command.required:
            failed_commands.append(result.command.command)
            if first_error_returncode is None:
                first_error_returncode = result.returncode
                # Capture first 300 chars of output for quick diagnosis
                first_error_output_preview = (result.output or "")[:300]

    if not failed_commands:
        return {}

    return {
        "failed_commands": failed_commands,
        "first_error_returncode": first_error_returncode,
        "first_error_output_preview": first_error_output_preview,
    }


def _run_case_once(
    *,
    case: BenchmarkCase,
    iteration: int,
    repo_path: str,
    model_profile: ModelProfile,
    provider_mode: str,
    engine_command: tuple[str, ...] | None,
    timeout_seconds: float,
    nemo_adapter: Any | None,
) -> BenchmarkIteration:
    repo_root = Path(repo_path).resolve()
    for relative_path, content in case.setup_files:
        target = repo_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    request = HandoffRequest(
        prd=case.objective,
        repo_path=repo_path,
        acceptance_criteria=case.acceptance_criteria,
        validation_commands=(f"{sys.executable} --version",),
        repair_budget=max(0, int(case.repair_budget)),
        objective_summary=case.objective,
        spec_mode="auto",
    )

    started = perf_counter()
    result = execute_headless_handoff(
        request,
        real_validation=provider_mode != "fake",
        provider_mode=provider_mode,
        model_profile=model_profile,
        engine_command=engine_command,
        timeout_seconds=timeout_seconds,
        target_files=case.target_files,
        validation_python_scripts=case.validation_python_scripts,
        validation_policy="none" if provider_mode == "fake" else "targeted",
        validation_cwd="runtime",
        nemo_adapter=nemo_adapter or InMemoryNemoAdapter(),
    )
    wall_time_ms = max(1, int((perf_counter() - started) * 1000))

    mutation = result.effective_mutation_result or result.mutation_result
    mutation_duration_ms = int(mutation.duration_ms) if mutation else 0
    output_chars = len(mutation.stdout) + len(mutation.stderr) if mutation else 0
    changed_files_count = len(result.effective_changed_files)
    portfolio_tokens = 0
    if isinstance(result.portfolio, dict):
        portfolio_tokens = int(result.portfolio.get("estimated_tokens", 0) or 0)

    token_total = 0
    token_prompt = 0
    token_completion = 0
    if mutation and mutation.token_usage:
        token_total = int(mutation.token_usage.total_tokens)
        token_prompt = int(mutation.token_usage.prompt_tokens)
        token_completion = int(mutation.token_usage.completion_tokens)

    repair_attempts = len(result.repair_plan.attempts) if result.repair_plan else 0
    first_pass = result.validation.passed and repair_attempts == 0
    repair_used = repair_attempts > 0
    repair_success = result.validation.passed and repair_used
    token_efficiency_per_file = None
    if token_total > 0 and changed_files_count > 0:
        token_efficiency_per_file = round(token_total / changed_files_count, 4)

    validation_summary = _extract_validation_summary(result.validation)

    return BenchmarkIteration(
        case_id=case.case_id,
        iteration=iteration,
        wall_time_ms=wall_time_ms,
        mutation_duration_ms=mutation_duration_ms,
        changed_files_count=changed_files_count,
        output_chars=output_chars,
        validation_passed=result.validation.passed,
        repair_attempts=repair_attempts,
        portfolio_tokens=portfolio_tokens,
        token_usage_total=token_total,
        token_usage_prompt=token_prompt,
        token_usage_completion=token_completion,
        token_efficiency_per_file=token_efficiency_per_file,
        first_pass=first_pass,
        repair_used=repair_used,
        repair_success=repair_success,
        memory_calls=len(result.nemo_results),
        timeline_events=len(result.timeline.events),
        artifacts_count=len(result.artifacts),
        success=changed_files_count > 0,
        validation_summary=validation_summary,
    )


def _per_second(value: int, ms: int) -> float | None:
    if value <= 0 or ms <= 0:
        return None
    return round(value / (ms / 1000.0), 4)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
