from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Virtual alias shown in all agent-facing output instead of the real host path.
SPACECODE_VIRTUAL_WORKSPACE = "/spacecode/workspace"


def _path_variants(path: str) -> set[str]:
    """Return a set of path representations covering both slash styles."""
    return {path, path.replace("\\", "/"), path.replace("/", "\\")}


def mask_host_paths(output: str, workspace: Workspace) -> str:
    """Replace all real host workspace path occurrences in *output* with the
    Space Code virtual alias.

    Handles Windows-style backslash paths, POSIX-style forward-slash paths, and
    mixed forms that can appear in subprocess stdout/stderr. Inspired by
    deer-flow's ``mask_local_paths_in_output`` in sandbox/tools.py.

    Args:
        output: Raw string that may contain host filesystem paths.
        workspace: The Workspace whose root should be masked.

    Returns:
        The sanitised string with virtual paths substituted.
    """
    result = output
    raw_root = str(workspace.root)
    resolved_root = str(workspace.root.resolve())

    for base in _path_variants(raw_root) | _path_variants(resolved_root):
        escaped = re.escape(base).replace(r"\\", r"[/\\]")
        pattern = re.compile(escaped + r'(?:[/\\][^\s"\';&|<>()]*)?')

        def _replace(match: re.Match, _base: str = base) -> str:
            matched = match.group(0)
            if matched == _base:
                return SPACECODE_VIRTUAL_WORKSPACE
            relative = matched[len(_base):].lstrip("/\\")
            return f"{SPACECODE_VIRTUAL_WORKSPACE}/{relative}" if relative else SPACECODE_VIRTUAL_WORKSPACE

        result = pattern.sub(_replace, result)

    return result


@dataclass(frozen=True, slots=True)
class Workspace:
    root: Path

    @classmethod
    def from_path(cls, path: str | Path) -> Workspace:
        return cls(root=Path(path).resolve())

    def resolve_inside(self, relative_path: str | Path) -> Path:
        normalized = Path(str(relative_path).replace("\\", "/"))
        candidate = (self.root / normalized).resolve()
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
