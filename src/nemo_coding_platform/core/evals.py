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