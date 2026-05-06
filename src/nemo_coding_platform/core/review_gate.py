from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nemo_coding_platform.core.persistence import summarize_persisted_result
from nemo_coding_platform.core.workspace import Workspace


@dataclass(frozen=True, slots=True)
class MergeFilePlan:
    path: str
    operation: str
    source_path: str
    target_path: str
    source_exists: bool
    target_exists: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "operation": self.operation,
            "source_path": self.source_path,
            "target_path": self.target_path,
            "source_exists": self.source_exists,
            "target_exists": self.target_exists,
        }


@dataclass(frozen=True, slots=True)
class MergePlan:
    task_id: str
    run_id: str
    repo_path: str
    sandbox_path: str
    grade: str
    changed_files: tuple[str, ...]
    files: tuple[MergeFilePlan, ...]
    risk_flags: tuple[str, ...]
    mergeable: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "repo_path": self.repo_path,
            "sandbox_path": self.sandbox_path,
            "grade": self.grade,
            "changed_files": list(self.changed_files),
            "files": [item.to_dict() for item in self.files],
            "risk_flags": list(self.risk_flags),
            "mergeable": self.mergeable,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Merge Plan",
            "",
            f"task_id={self.task_id}",
            f"run_id={self.run_id}",
            f"grade={self.grade}",
            f"mergeable={str(self.mergeable).lower()}",
            "",
            "## Files",
        ]
        if self.files:
            lines.extend(f"- {item.operation}: {item.path}" for item in self.files)
        else:
            lines.append("- none")
        lines.extend(["", "## Risk Flags"])
        if self.risk_flags:
            lines.extend(f"- {item}" for item in self.risk_flags)
        else:
            lines.append("- none")
        return "\n".join(lines) + "\n"


@dataclass(frozen=True, slots=True)
class MergeApplyResult:
    task_id: str
    run_id: str
    applied_files: tuple[str, ...]
    review_approved: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "applied_files": list(self.applied_files),
            "review_approved": self.review_approved,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Apply Report",
            "",
            f"task_id={self.task_id}",
            f"run_id={self.run_id}",
            f"review_approved={str(self.review_approved).lower()}",
            "",
            "## Applied Files",
        ]
        if self.applied_files:
            lines.extend(f"- {path}" for path in self.applied_files)
        else:
            lines.append("- none")
        return "\n".join(lines) + "\n"


def _changed_files(payload: dict[str, Any], summary: dict[str, Any]) -> tuple[str, ...]:
    changed = summary.get("changed_files") or []
    if isinstance(changed, str):
        return (changed,)
    return tuple(str(item).replace("\\", "/") for item in changed)


def _payload_value(payload: dict[str, Any], section: str, key: str) -> str:
    value = payload.get(section, {}) if isinstance(payload.get(section), dict) else {}
    return str(value.get(key) or "")


def _resolve_plan_path(workspace: Workspace, relative_path: str) -> Path:
    if Path(relative_path).is_absolute():
        raise ValueError(relative_path)
    return workspace.resolve_inside(relative_path)


def build_merge_plan(payload: dict[str, Any]) -> MergePlan:
    summary = summarize_persisted_result(payload)
    task_id = str(summary.get("task_id") or _payload_value(payload, "task", "id"))
    run_id = str(summary.get("run_id") or _payload_value(payload, "run", "id"))
    repo_path = _payload_value(payload, "task", "repo_path") or "."
    sandbox_path = _payload_value(payload, "run", "sandbox_path")
    changed_files = _changed_files(payload, summary)
    repo = Workspace.from_path(repo_path)
    sandbox = Workspace.from_path(sandbox_path) if sandbox_path else Workspace.from_path(".")
    files: list[MergeFilePlan] = []
    risk_flags: list[str] = []
    if summary.get("grade") != "ready":
        risk_flags.append("run_not_ready")
    risk_flags.extend(str(reason) for reason in summary.get("reasons", []))
    if not changed_files:
        risk_flags.append("no_changed_files")
    for relative_path in changed_files:
        try:
            source = _resolve_plan_path(sandbox, relative_path)
            target = _resolve_plan_path(repo, relative_path)
        except ValueError:
            risk_flags.append("path_escapes_workspace")
            continue
        source_exists = source.exists() and source.is_file()
        target_exists = target.exists()
        if not source_exists:
            risk_flags.append("missing_source_file")
        operation = "update" if target_exists else "create"
        files.append(MergeFilePlan(relative_path, operation, str(source), str(target), source_exists, target_exists))
    unique_risks = tuple(dict.fromkeys(risk_flags))
    mergeable = not unique_risks and bool(files)
    return MergePlan(task_id, run_id, str(repo.root), str(sandbox.root), str(summary.get("grade")), changed_files, tuple(files), unique_risks, mergeable)


def apply_merge_plan(plan: MergePlan, *, approve_review: bool = False) -> MergeApplyResult:
    if not approve_review:
        raise PermissionError("review approval required before applying run changes")
    if not plan.mergeable:
        raise PermissionError(f"merge plan is not mergeable: {', '.join(plan.risk_flags)}")
    applied: list[str] = []
    for item in plan.files:
        source = Path(item.source_path)
        target = Path(item.target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        applied.append(item.path)
    return MergeApplyResult(plan.task_id, plan.run_id, tuple(applied), True)
