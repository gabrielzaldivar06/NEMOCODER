from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Workspace:
    root: Path

    @classmethod
    def from_path(cls, path: str | Path) -> Workspace:
        return cls(root=Path(path).resolve())

    def resolve_inside(self, relative_path: str | Path) -> Path:
        candidate = (self.root / relative_path).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError(f"path escapes workspace: {relative_path}")
        return candidate

    def read_text(self, relative_path: str | Path) -> str:
        return self.resolve_inside(relative_path).read_text(encoding="utf-8")


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    root: Path
    tracked_files: tuple[str, ...]


def snapshot_workspace(workspace: Workspace) -> WorkspaceSnapshot:
    files: list[str] = []
    for path in workspace.root.rglob("*"):
        if path.is_file() and ".git" not in path.parts and ".venv" not in path.parts:
            files.append(path.relative_to(workspace.root).as_posix())
    return WorkspaceSnapshot(root=workspace.root, tracked_files=tuple(sorted(files)))