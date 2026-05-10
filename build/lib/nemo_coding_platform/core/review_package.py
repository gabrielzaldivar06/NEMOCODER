from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from nemo_coding_platform.core.task_run import Artifact, ArtifactType, MemoryTrace, Run, Task
from nemo_coding_platform.core.validation import ValidationSuiteResult, format_validation_report

if TYPE_CHECKING:
    from nemo_coding_platform.core.engine_interface import MutationResult
    from nemo_coding_platform.core.repair import RepairPlan


class ReviewStatus(StrEnum):
    READY = "ready"
    BLOCKED = "blocked"
    APPROVED = "approved"


@dataclass(frozen=True, slots=True)
class ReviewPackage:
    task: Task
    run: Run
    diff_summary: str
    validation: ValidationSuiteResult
    memory_traces: tuple[MemoryTrace, ...]
    risk_summary: str
    status: ReviewStatus = ReviewStatus.READY
    changed_files: tuple[str, ...] = ()
    diff_artifact: str = ""
    provider_summary: str = ""
    repair_attempts: int = 0
    repair_details: tuple[dict[str, Any], ...] = ()  # per-attempt: {attempt, reason, action, changed_files}
    repair_success_reason: str = ""  # e.g., 'validation_passed', 'repair_noop', 'repair_budget_exhausted'

    @property
    def can_merge_to_main(self) -> bool:
        return self.status == ReviewStatus.APPROVED and self.validation.passed

    def to_markdown(self) -> str:
        memory = "\n".join(f"- {trace.nemo_tool}: {trace.summary}" for trace in self.memory_traces) or "- none"
        changed = "\n".join(f"- {path}" for path in self.changed_files) or "- none"
        validation_details = format_validation_report(self.validation)
        diff = self.diff_artifact or self.diff_summary
        
        # Build repair details section if attempts were made
        repair_section = ""
        if self.repair_attempts > 0:
            repair_lines = [f"Total attempts: {self.repair_attempts}"]
            if self.repair_success_reason:
                repair_lines.append(f"Outcome: {self.repair_success_reason}")
            for detail in self.repair_details:
                repair_lines.extend((
                    f"\n### Attempt {detail.get('attempt', '?')}",
                    f"Reason: {detail.get('reason', 'N/A')}",
                    f"Action: {detail.get('action', 'N/A')}",
                ))
                changed_in_attempt = detail.get('changed_files', ())
                if changed_in_attempt:
                    repair_lines.append(f"Changed: {', '.join(changed_in_attempt)}")
            repair_section = "\n## Repair Attempts\n" + "\n".join(repair_lines) + "\n"
        
        base_parts = [
            f"# Review Package: {self.task.title}",
            "",
            "## Provider",
            self.provider_summary or "No provider summary.",
            f"repair_attempts={self.repair_attempts}",
            "",
            "## Changed Files",
            changed,
            "",
            "## Diff",
            diff,
            "",
            "## Validation",
            validation_details,
        ]
        
        if repair_section:
            base_parts.append(repair_section)
        
        base_parts.extend([
            "## Memory",
            memory,
            "",
            "## Risks",
            self.risk_summary,
            "",
            f"status={self.status.value}",
        ])
        
        return "\n".join(base_parts)

    def to_artifact(self, artifact_id: str) -> Artifact:
        content = self.to_markdown()
        return Artifact.from_content(
            artifact_id,
            self.run.id,
            ArtifactType.REVIEW_PACKAGE,
            "review-package.md",
            "Final review package",
            content,
        )


def build_review_package(
    task: Task,
    run: Run,
    diff_summary: str,
    validation: ValidationSuiteResult,
    memory_traces: tuple[MemoryTrace, ...],
    risk_summary: str = "No unresolved risks.",
    mutation_result: "MutationResult | None" = None,
    repair_attempts: int = 0,
    repair_plan: "RepairPlan | None" = None,
    repair_success_reason: str = "",
) -> ReviewPackage:
    status = ReviewStatus.READY if validation.passed else ReviewStatus.BLOCKED
    changed_files = mutation_result.changed_files if mutation_result else ()
    diff_artifact = mutation_result.diff_artifact if mutation_result else ""
    provider_summary = ""
    if mutation_result:
        model = mutation_result.model_profile.model if mutation_result.model_profile else "unknown-model"
        provider_summary = f"provider={mutation_result.provider} mode={mutation_result.provider_mode} model={model} summary={mutation_result.summary}"
    if mutation_result and validation.passed and not mutation_result.changed_files and not mutation_result.applied_files:
        status = ReviewStatus.BLOCKED
        risk_summary = "No implementation changes were detected."
    
    # Extract repair details from repair_plan if available
    repair_details = ()
    if repair_plan:
        repair_details = tuple(
            {
                "attempt": attempt.attempt,
                "reason": attempt.reason,
                "action": attempt.action,
                "changed_files": (),  # Would need mutation_results to populate
            }
            for attempt in repair_plan.attempts
        )
    
    return ReviewPackage(
        task,
        run,
        diff_summary,
        validation,
        memory_traces,
        risk_summary,
        status,
        changed_files,
        diff_artifact,
        provider_summary,
        repair_attempts,
        repair_details,
        repair_success_reason,
    )