from __future__ import annotations

import ast
import json
from pathlib import Path

_COMMENT_PREFIXES = ("#", "//", "/*", '"""', "'''")


def _is_text_file(file_path: Path) -> bool:
    """Return True if file_path appears to be a text file (no null bytes in first 512 bytes)."""
    try:
        chunk = file_path.read_bytes()[:512]
        return b"\x00" not in chunk
    except OSError:
        return False


def _extract_summary(file_path: Path) -> str:
    """Return a one-line summary (max 120 chars) from file_path, or '' if not extractable."""
    if not _is_text_file(file_path):
        return ""
    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""

    if file_path.suffix == ".py":
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return ""
        for node in tree.body:
            if (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                first_line = node.value.value.strip().splitlines()[0]
                return first_line[:120]
        return ""

    for line in source.splitlines()[:4]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#!"):
            continue
        for prefix in _COMMENT_PREFIXES:
            if stripped.startswith(prefix):
                summary = stripped.lstrip("#/!*\"' \t").strip()
                return summary[:120]
    return ""


def _format_tree(entries: list[tuple[Path, str]], repo_root: Path) -> str:
    """Format (abs_path, summary) entries as a grouped markdown tree."""
    entries_sorted = sorted(entries, key=lambda e: e[0])

    groups: dict[str, list[tuple[str, str]]] = {}
    for abs_path, summary in entries_sorted:
        try:
            rel = abs_path.relative_to(repo_root)
        except ValueError:
            rel = abs_path
        parts = rel.parts
        if len(parts) > 1:
            group_key = "/".join(parts[:-1])
        else:
            group_key = "."
        filename = parts[-1]
        groups.setdefault(group_key, []).append((filename, summary))

    lines: list[str] = []
    for group_key in sorted(groups):
        prefix = f"## {group_key}/" if group_key != "." else "## ./"
        lines.append(prefix)
        for filename, summary in groups[group_key]:
            if summary:
                lines.append(f"  {filename} — {summary}")
            else:
                lines.append(f"  {filename}")
        lines.append("")

    return "\n".join(lines).rstrip()


def _load_cache(cache_path: Path) -> dict[str, dict]:
    """Load JSON cache from cache_path. Returns {} on any error."""
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _save_cache(cache_path: Path, data: dict[str, dict]) -> None:
    """Write data as JSON to cache_path, creating parent dirs as needed."""
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass


def build_repo_map(
    repo_path: str | Path,
    *,
    cache_path: str | Path | None = None,
    max_chars: int = 3000,
) -> str:
    """Scan repo_path and return a compact markdown map of files and their one-line purposes.

    Args:
        repo_path:  Root directory to scan (recursive).
        cache_path: Optional path for mtime-based JSON cache. None = no caching.
        max_chars:  Maximum output length. Truncated with '[truncated]' marker if exceeded.

    Returns:
        Compact markdown string, or '' if repo_path is not a directory.
    """
    root = Path(repo_path).resolve()
    if not root.is_dir():
        return ""

    _cache_path = Path(cache_path).resolve() if cache_path is not None else None
    cache: dict[str, dict] = _load_cache(_cache_path) if _cache_path is not None else {}
    cache_dirty = False

    entries: list[tuple[Path, str]] = []

    for file_path in sorted(root.rglob("*")):
        if not file_path.is_file():
            continue
        # Skip the cache file itself to avoid polluting the map
        if _cache_path is not None and file_path.resolve() == _cache_path:
            continue
        try:
            rel = str(file_path.relative_to(root)).replace("\\", "/")
            mtime = file_path.stat().st_mtime
        except (OSError, ValueError):
            continue

        cached = cache.get(rel)
        if cached is not None and cached.get("mtime") == mtime:
            summary = cached["summary"]
        else:
            summary = _extract_summary(file_path)
            cache[rel] = {"mtime": mtime, "summary": summary}
            cache_dirty = True

        entries.append((file_path, summary))

    if cache_dirty and _cache_path is not None:
        _save_cache(_cache_path, cache)

    result = _format_tree(entries, root)
    if len(result) > max_chars:
        result = result[:max_chars] + "\n[truncated]"
    return result
