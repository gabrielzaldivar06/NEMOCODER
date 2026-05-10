from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nemo_coding_platform.core.task_run import ArtifactType, EventKind


@dataclass(frozen=True, slots=True)
class ReadinessScore:
    score: float
    validation_passed: bool
    has_checkpoint: bool
    has_review_package: bool
    memory_writeback_present: bool
    mutation_present: bool = True
    grade: str = "unknown"
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MetricsReport:
    task_id: str | None
    run_id: str | None
    success_rate: float
    validation_pass_rate: float
    override_rate: float
    stale_memory_incidents: int
    tool_failure_rate: float
    validation_checks_total: int
    validation_checks_failed: int
    permission_events_total: int
    permission_overrides: int
    tool_invocations_total: int
    tool_failures_total: int
    readiness_grade: str
    readiness_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "success_rate": self.success_rate,
            "validation_pass_rate": self.validation_pass_rate,
            "override_rate": self.override_rate,
            "stale_memory_incidents": self.stale_memory_incidents,
            "tool_failure_rate": self.tool_failure_rate,
            "validation_checks_total": self.validation_checks_total,
            "validation_checks_failed": self.validation_checks_failed,
            "permission_events_total": self.permission_events_total,
            "permission_overrides": self.permission_overrides,
            "tool_invocations_total": self.tool_invocations_total,
            "tool_failures_total": self.tool_failures_total,
            "readiness_grade": self.readiness_grade,
            "readiness_reasons": list(self.readiness_reasons),
        }


def score_headless_result(result: object) -> ReadinessScore:
    if isinstance(result, dict):
        return score_persisted_result(result)
    timeline = getattr(result, "timeline")
    artifacts = getattr(result, "artifacts")
    validation = getattr(result, "validation")
    has_checkpoint = timeline.has_event_kind(EventKind.CHECKPOINT)
    has_review = any(artifact.artifact_type == ArtifactType.REVIEW_PACKAGE for artifact in artifacts)
    has_memory = timeline.has_event_kind(EventKind.MEMORY_WRITTEN)
    mutation = getattr(result, "mutation_result", None)
    repair = getattr(result, "repair_result", None)
    repair_mutations = getattr(repair, "mutation_results", ()) if repair else ()
    has_repair_mutation = any(getattr(item, "changed_files", ()) for item in repair_mutations)
    has_mutation = (mutation is not None and bool(getattr(mutation, "changed_files", ()))) or has_repair_mutation or (mutation is None and timeline.has_event_kind(EventKind.MUTATION_CREATED))
    return _score_from_checks(validation.passed, has_checkpoint, has_review, has_memory, has_mutation)


def _score_from_checks(
    validation_passed: bool,
    has_checkpoint: bool,
    has_review_package: bool,
    memory_writeback_present: bool,
    mutation_present: bool = True,
) -> ReadinessScore:
    checks = (validation_passed, has_checkpoint, has_review_package, memory_writeback_present, mutation_present)
    score = sum(1 for check in checks if check) / len(checks)
    reasons: list[str] = []
    if not validation_passed:
        reasons.append("validation_failed")
    if not has_checkpoint:
        reasons.append("missing_checkpoint")
    if not has_review_package:
        reasons.append("missing_review_package")
    if not memory_writeback_present:
        reasons.append("missing_memory_writeback")
    if not mutation_present:
        reasons.append("missing_mutation_result")
    grade = "ready" if score == 1.0 else "blocked" if score < 0.75 else "needs_review"
    return ReadinessScore(
        score,
        validation_passed,
        has_checkpoint,
        has_review_package,
        memory_writeback_present,
        mutation_present,
        grade,
        tuple(reasons),
    )


def _value(item: Any, key: str) -> Any:
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key)


def score_persisted_result(payload: dict[str, Any]) -> ReadinessScore:
    timeline = payload.get("timeline", [])
    artifacts = payload.get("artifacts", [])
    validation = payload.get("validation", {})
    validation_results = validation.get("results", []) if isinstance(validation, dict) else []
    validation_passed = all(result.get("status") == "passed" for result in validation_results)
    has_checkpoint = any(_value(event, "kind") == EventKind.CHECKPOINT.value for event in timeline)
    has_memory = any(_value(event, "kind") == EventKind.MEMORY_WRITTEN.value for event in timeline)
    has_review = any(_value(artifact, "artifact_type") == ArtifactType.REVIEW_PACKAGE.value for artifact in artifacts)
    mutation = payload.get("mutation_result")
    if isinstance(mutation, dict):
        repair = payload.get("repair_result", {})
        repair_mutations = repair.get("mutation_results", []) if isinstance(repair, dict) else []
        has_repair_mutation = any(item.get("changed_files") for item in repair_mutations if isinstance(item, dict))
        has_mutation = bool(mutation.get("changed_files")) or has_repair_mutation
    else:
        has_mutation = any(_value(event, "kind") == EventKind.MUTATION_CREATED.value for event in timeline)
    return _score_from_checks(validation_passed, has_checkpoint, has_review, has_memory, has_mutation)


def _safe_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 3)


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def build_run_metrics_report(payload: dict[str, Any]) -> MetricsReport:
    """Build FR10-style evaluation metrics for a persisted run payload."""
    readiness = score_persisted_result(payload)
    task = payload.get("task", {}) if isinstance(payload.get("task"), dict) else {}
    run = payload.get("run", {}) if isinstance(payload.get("run"), dict) else {}
    timeline = payload.get("timeline", []) if isinstance(payload.get("timeline"), list) else []
    validation = payload.get("validation", {}) if isinstance(payload.get("validation"), dict) else {}
    validation_results = [item for item in validation.get("results", []) if isinstance(item, dict)]
    mutation = payload.get("mutation_result", {}) if isinstance(payload.get("mutation_result"), dict) else {}

    validation_checks_total = len(validation_results)
    validation_checks_failed = sum(1 for item in validation_results if _to_text(item.get("status")) != "passed")
    if validation_checks_total:
        validation_pass_rate = _safe_ratio(validation_checks_total - validation_checks_failed, validation_checks_total)
    else:
        validation_pass_rate = 1.0 if readiness.validation_passed else 0.0

    permission_events_total = 0
    permission_overrides = 0
    stale_memory_incidents = sum(1 for reason in readiness.reasons if "stale" in _to_text(reason))
    for event in timeline:
        if not isinstance(event, dict):
            continue
        kind = _to_text(event.get("kind"))
        summary = _to_text(event.get("summary"))
        if kind in {EventKind.PERMISSION_DECIDED.value, EventKind.PERMISSION_DENIED.value}:
            permission_events_total += 1
        if kind == EventKind.PERMISSION_DENIED.value or any(term in summary for term in ("denied", "rejected", "blocked", "override")):
            permission_overrides += 1
        if "stale" in summary:
            stale_memory_incidents += 1

    for result in validation_results:
        if "stale" in _to_text(result.get("summary")):
            stale_memory_incidents += 1

    tool_invocations_total = validation_checks_total + (1 if mutation else 0)
    tool_failures_total = validation_checks_failed
    returncode = mutation.get("returncode") if isinstance(mutation, dict) else None
    if isinstance(returncode, int) and returncode != 0:
        tool_failures_total += 1
    tool_failure_rate = _safe_ratio(tool_failures_total, tool_invocations_total)

    return MetricsReport(
        task_id=str(task.get("id")) if task.get("id") is not None else None,
        run_id=str(run.get("id")) if run.get("id") is not None else None,
        success_rate=1.0 if readiness.grade == "ready" else 0.0,
        validation_pass_rate=validation_pass_rate,
        override_rate=_safe_ratio(permission_overrides, permission_events_total),
        stale_memory_incidents=stale_memory_incidents,
        tool_failure_rate=tool_failure_rate,
        validation_checks_total=validation_checks_total,
        validation_checks_failed=validation_checks_failed,
        permission_events_total=permission_events_total,
        permission_overrides=permission_overrides,
        tool_invocations_total=tool_invocations_total,
        tool_failures_total=tool_failures_total,
        readiness_grade=readiness.grade,
        readiness_reasons=readiness.reasons,
    )


def score_spec10_lite(payload: dict[str, Any]) -> dict[str, Any]:
    readiness = score_persisted_result(payload)
    task = payload.get("task", {}) if isinstance(payload.get("task"), dict) else {}
    linked_prd = task.get("linked_prd") if isinstance(task.get("linked_prd"), str) else None
    linked_specs_raw = task.get("linked_specs")
    linked_specs = [item for item in linked_specs_raw if isinstance(item, str)] if isinstance(linked_specs_raw, list) else []

    def _bin_score(flag: bool) -> float:
        return 1.0 if flag else 0.0

    dimensions = {
        "execution": round(((_bin_score(readiness.has_checkpoint) + _bin_score(readiness.mutation_present)) / 2.0), 3),
        "validation": _bin_score(readiness.validation_passed),
        "review_package": _bin_score(readiness.has_review_package),
        "memory_writeback": _bin_score(readiness.memory_writeback_present),
        "traceability": round(((_bin_score(bool(linked_prd)) + _bin_score(bool(linked_specs))) / 2.0), 3),
        "safety": round(max(0.0, 1.0 - (len(readiness.reasons) * 0.25)), 3),
    }
    composite_score = round(sum(dimensions.values()) / len(dimensions), 3)
    if composite_score >= 0.9:
        grade = "ready"
    elif composite_score >= 0.7:
        grade = "needs_review"
    else:
        grade = "blocked"

    checklist: list[str] = []
    if not readiness.validation_passed:
        checklist.append("validation must pass")
    if not readiness.has_checkpoint:
        checklist.append("checkpoint event is required")
    if not readiness.has_review_package:
        checklist.append("review package artifact is required")
    if not readiness.memory_writeback_present:
        checklist.append("memory writeback event is required")
    if not readiness.mutation_present:
        checklist.append("mutation result with changed files is required")
    if not linked_prd:
        checklist.append("linked PRD is missing")
    if not linked_specs:
        checklist.append("linked specs are missing")

    return {
        "score": composite_score,
        "grade": grade,
        "dimensions": dimensions,
        "checklist": checklist,
    }