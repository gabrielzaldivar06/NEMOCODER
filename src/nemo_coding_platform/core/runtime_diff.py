from __future__ import annotations

from dataclasses import dataclass
from difflib import unified_diff
from hashlib import sha256
from pathlib import Path


IGNORED_PARTS = {".git", ".venv", "__pycache__", ".nemo-aider-message.md"}
MAX_UNIFIED_DIFF_CHARS = 10_000


def _ignored_relative_path(relative: Path) -> bool:
    return any(part in IGNORED_PARTS for part in relative.parts) or relative.name.startswith(".aider")


@dataclass(frozen=True, slots=True)
class RuntimeFileSnapshot:
    path: str
    content_hash: str
    content: str


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    root: str
    files: tuple[RuntimeFileSnapshot, ...]

    def by_path(self) -> dict[str, RuntimeFileSnapshot]:
        return {item.path: item for item in self.files}


@dataclass(frozen=True, slots=True)
class RuntimeDiff:
    created: tuple[str, ...]
    updated: tuple[str, ...]
    deleted: tuple[str, ...]
    unified_diff: str

    @property
    def changed_files(self) -> tuple[str, ...]:
        return tuple(sorted(self.created + self.updated + self.deleted))


def snapshot_path(root: str | Path) -> RuntimeSnapshot:
    base = Path(root).resolve()
    files: list[RuntimeFileSnapshot] = []
    if not base.exists():
        return RuntimeSnapshot(str(base), ())
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(base)
        if _ignored_relative_path(relative):
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        files.append(RuntimeFileSnapshot(relative.as_posix(), sha256(content.encode("utf-8")).hexdigest(), content))
    return RuntimeSnapshot(str(base), tuple(sorted(files, key=lambda item: item.path)))


def diff_snapshots(before: RuntimeSnapshot, after: RuntimeSnapshot) -> RuntimeDiff:
    before_files = before.by_path()
    after_files = after.by_path()
    before_paths = set(before_files)
    after_paths = set(after_files)
    created = tuple(sorted(after_paths - before_paths))
    deleted = tuple(sorted(before_paths - after_paths))
    updated = tuple(sorted(path for path in before_paths & after_paths if before_files[path].content_hash != after_files[path].content_hash))
    chunks: list[str] = []
    for path in tuple(sorted(created + updated)):
        old_content = before_files[path].content if path in before_files else ""
        new_content = after_files[path].content
        chunks.extend(
            unified_diff(
                old_content.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=f"before/{path}",
                tofile=f"after/{path}",
            )
        )
    for path in deleted:
        chunks.extend(
            unified_diff(
                before_files[path].content.splitlines(keepends=True),
                [],
                fromfile=f"before/{path}",
                tofile=f"after/{path}",
            )
        )
    rendered = "".join(chunks)
    if len(rendered) > MAX_UNIFIED_DIFF_CHARS:
        rendered = rendered[:MAX_UNIFIED_DIFF_CHARS] + "\n... diff truncated ...\n"
    return RuntimeDiff(created, updated, deleted, rendered)
