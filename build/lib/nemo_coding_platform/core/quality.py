from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from nemo_coding_platform.core.contracts import ApprovalLevel


class ValidationStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class ValidationCommand:
    name: str
    command: str
    required: bool = True


@dataclass(frozen=True, slots=True)
class MutationPolicy:
    only_quality_core_writes: bool = True
    dry_run_required: bool = True
    lint_after_edit: bool = True
    test_before_finalize: bool = True
    review_gate_before_commit: bool = True
    commit_requires: ApprovalLevel = ApprovalLevel.REQUIRED


DEFAULT_VALIDATIONS = (
    ValidationCommand(name="unit-tests", command="python -m unittest discover -s tests"),
)

DEFAULT_MUTATION_POLICY = MutationPolicy()
