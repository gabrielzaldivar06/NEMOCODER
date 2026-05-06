from __future__ import annotations

from dataclasses import dataclass

from nemo_coding_platform.core.quality import DEFAULT_MUTATION_POLICY, MutationPolicy
from nemo_coding_platform.core.workspace import Workspace


@dataclass(frozen=True, slots=True)
class FileWrite:
    path: str
    content: str


@dataclass(frozen=True, slots=True)
class MutationPlan:
    writes: tuple[FileWrite, ...]
    approved: bool = False
    dry_run_completed: bool = False


@dataclass(frozen=True, slots=True)
class DryRunResult:
    files: tuple[str, ...]
    creates: tuple[str, ...]
    updates: tuple[str, ...]


class QualityMutationEngine:
    def __init__(self, workspace: Workspace, policy: MutationPolicy = DEFAULT_MUTATION_POLICY) -> None:
        self.workspace = workspace
        self.policy = policy

    def dry_run(self, plan: MutationPlan) -> DryRunResult:
        creates: list[str] = []
        updates: list[str] = []
        files: list[str] = []
        for write in plan.writes:
            path = self.workspace.resolve_inside(write.path)
            files.append(write.path)
            if path.exists():
                updates.append(write.path)
            else:
                creates.append(write.path)
        return DryRunResult(files=tuple(files), creates=tuple(creates), updates=tuple(updates))

    def apply(self, plan: MutationPlan) -> tuple[str, ...]:
        if self.policy.dry_run_required and not plan.dry_run_completed:
            raise PermissionError("dry-run is required before applying mutations")
        if not plan.approved:
            raise PermissionError("approved mutation plan is required")

        applied: list[str] = []
        for write in plan.writes:
            path = self.workspace.resolve_inside(write.path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(write.content, encoding="utf-8")
            applied.append(write.path)
        return tuple(applied)