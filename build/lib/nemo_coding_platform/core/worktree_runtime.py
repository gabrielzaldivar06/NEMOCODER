from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from re import sub
from shutil import rmtree
from typing import Callable

from nemo_coding_platform.core.contracts import RuntimeState


@dataclass(frozen=True, slots=True)
class WorktreeRuntimeSpec:
    runtime_id: str
    task_id: str
    run_id: str
    runtime_root: Path
    worktree_path: Path
    state: RuntimeState = RuntimeState.INITIALIZING

    def resolve_inside(self, relative_path: str) -> Path:
        target = (self.worktree_path / relative_path).resolve()
        root = self.worktree_path.resolve()
        if target != root and root not in target.parents:
            raise ValueError(f"path escapes worktree runtime: {relative_path}")
        return target


def runtime_slug(value: str) -> str:
    slug = sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-._")
    return slug or "runtime"


def create_worktree_runtime(task_id: str, run_id: str, runtime_root: str | Path) -> WorktreeRuntimeSpec:
    root = Path(runtime_root).resolve()
    runtime_id = f"{runtime_slug(task_id)}-{runtime_slug(run_id)}"
    return WorktreeRuntimeSpec(runtime_id, task_id, run_id, root, root / runtime_id)


def assert_not_main_workspace(target: str | Path, main_workspace: str | Path) -> None:
    target_path = Path(target).resolve()
    main_path = Path(main_workspace).resolve()
    if target_path == main_path or main_path in target_path.parents:
        raise PermissionError("autonomous runtime cannot target the main workspace directly")


def require_merge_review(approved: bool) -> None:
    if not approved:
        raise PermissionError("merge to main workspace requires review approval")


def initialize_runtime_dirs(spec: WorktreeRuntimeSpec) -> WorktreeRuntimeSpec:
    spec.runtime_root.mkdir(parents=True, exist_ok=True)
    spec.worktree_path.mkdir(parents=True, exist_ok=True)
    return spec


def reset_runtime_dirs(spec: WorktreeRuntimeSpec, remover: Callable[[Path], object] = rmtree) -> WorktreeRuntimeSpec:
    if spec.worktree_path.exists():
        try:
            remover(spec.worktree_path)
        except OSError:
            base_id = spec.runtime_id
            for index in range(2, 100):
                candidate_id = f"{base_id}-{index}"
                candidate_path = spec.runtime_root / candidate_id
                if not candidate_path.exists():
                    return initialize_runtime_dirs(replace(spec, runtime_id=candidate_id, worktree_path=candidate_path))
            raise
    return initialize_runtime_dirs(spec)


def write_runtime_file(spec: WorktreeRuntimeSpec, relative_path: str, content: str) -> Path:
    initialize_runtime_dirs(spec)
    target = spec.resolve_inside(relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def snapshot_runtime_files(spec: WorktreeRuntimeSpec) -> tuple[str, ...]:
    if not spec.worktree_path.exists():
        return ()
    ignored = {".git", ".venv"}
    files: list[str] = []
    for path in spec.worktree_path.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(spec.worktree_path)
        if any(part in ignored for part in relative.parts):
            continue
        files.append(relative.as_posix())
    return tuple(sorted(files))