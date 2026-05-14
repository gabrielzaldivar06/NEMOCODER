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
