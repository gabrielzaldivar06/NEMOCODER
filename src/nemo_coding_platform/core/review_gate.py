from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any

from nemo_coding_platform.core.persistence import summarize_persisted_result
from nemo_coding_platform.core.workspace import Workspace


PROTECTED_PATH_PARTS = {".git", ".venv", ".nemo-runtimes", ".nemo-apply-backups", "node_modules", "__pycache__"}
PROTECTED_PATHS = {"pyproject.toml", "package-lock.json", "pnpm-lock.yaml", "yarn.lock"}


@dataclass(frozen=True, slots=True)
class MergeFilePlan:
    path: str
    operation: str
    source_path: str
    target_path: str
    source_exists: bool
    target_exists: bool
    source_hash: str | None
    target_hash: str | None
    risk_flags: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "operation": self.operation,
            "source_path": self.source_path,
            "target_path": self.target_path,
            "source_exists": self.source_exists,
            "target_exists": self.target_exists,
            "source_hash": self.source_hash,
            "target_hash": self.target_hash,
            "risk_flags": list(self.risk_flags),
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
            for item in self.files:
                lines.append(f"- {item.operation}: {item.path}")
                lines.append(f"  source_hash={item.source_hash or 'missing'}")
                lines.append(f"  target_hash={item.target_hash or 'missing'}")
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
    repo_path: str
    applied_files: tuple[str, ...]
    created_files: tuple[str, ...]
    updated_files: tuple[str, ...]
    review_approved: bool
    backup_dir: str | None
    backup_files: tuple[str, ...]
    auto_applied: bool = False
    autonomy_profile: str = "manual"

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "repo_path": self.repo_path,
            "applied_files": list(self.applied_files),
            "created_files": list(self.created_files),
            "updated_files": list(self.updated_files),
            "review_approved": self.review_approved,
            "backup_dir": self.backup_dir,
            "backup_files": list(self.backup_files),
            "auto_applied": self.auto_applied,
            "autonomy_profile": self.autonomy_profile,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "MergeApplyResult":
        def string_tuple(key: str) -> tuple[str, ...]:
            value = payload.get(key, [])
            if not isinstance(value, list):
                return ()
            return tuple(str(item) for item in value)

        return cls(
            task_id=str(payload.get("task_id") or ""),
            run_id=str(payload.get("run_id") or ""),
            repo_path=str(payload.get("repo_path") or "."),
            applied_files=string_tuple("applied_files"),
            created_files=string_tuple("created_files"),
            updated_files=string_tuple("updated_files"),
            review_approved=bool(payload.get("review_approved")),
            backup_dir=str(payload.get("backup_dir")) if payload.get("backup_dir") else None,
            backup_files=string_tuple("backup_files"),
            auto_applied=bool(payload.get("auto_applied")),
            autonomy_profile=str(payload.get("autonomy_profile") or "manual"),
        )

    def to_markdown(self) -> str:
        lines = [
            "# Apply Report",
            "",
            f"task_id={self.task_id}",
            f"run_id={self.run_id}",
            f"repo_path={self.repo_path}",
            f"review_approved={str(self.review_approved).lower()}",
            f"auto_applied={str(self.auto_applied).lower()}",
            f"autonomy_profile={self.autonomy_profile}",
            "",
            "## Applied Files",
        ]
        if self.applied_files:
            lines.extend(f"- {path}" for path in self.applied_files)
        else:
            lines.append("- none")
        lines.extend(["", "## Backups"])
        if self.backup_files:
            if self.backup_dir:
                lines.append(f"backup_dir={self.backup_dir}")
            lines.extend(f"- {path}" for path in self.backup_files)
        else:
            lines.append("- none")
        return "\n".join(lines) + "\n"


@dataclass(frozen=True, slots=True)
class MergeRollbackResult:
    task_id: str
    run_id: str
    restored_files: tuple[str, ...]
    deleted_files: tuple[str, ...]
    review_approved: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "restored_files": list(self.restored_files),
            "deleted_files": list(self.deleted_files),
            "review_approved": self.review_approved,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Rollback Report",
            "",
            f"task_id={self.task_id}",
            f"run_id={self.run_id}",
            f"review_approved={str(self.review_approved).lower()}",
            "",
            "## Restored Files",
        ]
        lines.extend(f"- {path}" for path in self.restored_files) if self.restored_files else lines.append("- none")
        lines.extend(["", "## Deleted Created Files"])
        lines.extend(f"- {path}" for path in self.deleted_files) if self.deleted_files else lines.append("- none")
        return "\n".join(lines) + "\n"


def _file_hash(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_binary_file(path: Path) -> bool:
    if not path.exists() or not path.is_file():
        return False
    return b"\0" in path.read_bytes()[:4096]


def _protected_path_risk(relative_path: str) -> str | None:
    parts = tuple(Path(relative_path).parts)
    if any(part in PROTECTED_PATH_PARTS for part in parts):
        return f"protected_path:{relative_path}"
    if relative_path in PROTECTED_PATHS:
        return f"protected_path:{relative_path}"
    return None


def _backup_root(plan: MergePlan, backup_dir: str | None = None) -> Path:
    if backup_dir:
        return Path(backup_dir)
    safe_task = "".join(char if char.isalnum() or char in ("-", "_") else "-" for char in plan.task_id)
    safe_run = "".join(char if char.isalnum() or char in ("-", "_") else "-" for char in plan.run_id)
    return Path(plan.repo_path) / ".nemo-apply-backups" / f"{safe_task}-{safe_run}"


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
        file_risks: list[str] = []
        protected_risk = _protected_path_risk(relative_path)
        if protected_risk:
            file_risks.append(protected_risk)
        if _is_binary_file(source) or _is_binary_file(target):
            file_risks.append(f"binary_file:{relative_path}")
        if not source_exists:
            file_risks.append("missing_source_file")
        operation = "update" if target_exists else "create"
        risk_flags.extend(file_risks)
        files.append(MergeFilePlan(relative_path, operation, str(source), str(target), source_exists, target_exists, _file_hash(source), _file_hash(target), tuple(file_risks)))
    unique_risks = tuple(dict.fromkeys(risk_flags))
    mergeable = not unique_risks and bool(files)
    return MergePlan(task_id, run_id, str(repo.root), str(sandbox.root), str(summary.get("grade")), changed_files, tuple(files), unique_risks, mergeable)


def apply_merge_plan(plan: MergePlan, *, approve_review: bool = False, backup_dir: str | None = None, autonomy_profile: str = "manual") -> MergeApplyResult:
    auto_applied = False
    if not approve_review:
        from nemo_coding_platform.core.autonomy_gate import evaluate_auto_apply

        decision = evaluate_auto_apply(plan, autonomy_profile)
        if not decision.allowed:
            raise PermissionError(f"review approval required before applying run changes: {', '.join(decision.reasons)}")
        auto_applied = True
    if not plan.mergeable:
        raise PermissionError(f"merge plan is not mergeable: {', '.join(plan.risk_flags)}")
    applied: list[str] = []
    created_files: list[str] = []
    updated_files: list[str] = []
    backup_files: list[str] = []
    backup_root = _backup_root(plan, backup_dir)
    for item in plan.files:
        source = Path(item.source_path)
        target = Path(item.target_path)
        current_target_hash = _file_hash(target)
        if current_target_hash != item.target_hash:
            raise PermissionError(f"target changed since merge plan was built: {item.path}")
        if item.operation == "update" and target.exists():
            backup_target = backup_root / item.path
            backup_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(target, backup_target)
            backup_files.append(item.path)
            updated_files.append(item.path)
        elif item.operation == "create":
            created_files.append(item.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        applied.append(item.path)
    return MergeApplyResult(plan.task_id, plan.run_id, plan.repo_path, tuple(applied), tuple(created_files), tuple(updated_files), approve_review, str(backup_root) if backup_files else None, tuple(backup_files), auto_applied, autonomy_profile)


def rollback_apply_result(result: MergeApplyResult, *, approve_review: bool = False) -> MergeRollbackResult:
    if not approve_review:
        raise PermissionError("review approval required before rolling back applied changes")
    repo = Workspace.from_path(result.repo_path)
    backup_root = Path(result.backup_dir) if result.backup_dir else None
    restored: list[str] = []
    deleted: list[str] = []
    for relative_path in result.backup_files:
        if backup_root is None:
            raise PermissionError("rollback backup directory is missing")
        source = _resolve_plan_path(Workspace.from_path(backup_root), relative_path)
        target = _resolve_plan_path(repo, relative_path)
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(str(source))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        restored.append(relative_path)
    for relative_path in result.created_files:
        target = _resolve_plan_path(repo, relative_path)
        if target.exists() and target.is_file():
            target.unlink()
            deleted.append(relative_path)
    return MergeRollbackResult(result.task_id, result.run_id, tuple(restored), tuple(deleted), True)
