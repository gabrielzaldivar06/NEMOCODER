# GAP-09 Repo Map Integration — Design Spec

**Date:** 2026-05-14  
**Sprint:** GAP-09  
**Status:** Approved for implementation

---

## Problem

The Space Code mutation agent (Aider/subprocess) receives an objective, NEMO context, target files, and acceptance criteria — but no information about what files exist in the repository or what they do. In repos with 60+ source files, the agent navigates blind: it guesses file locations, duplicates logic that already exists, and misses relevant modules it should import or modify. Providing a structured repo map before each mutation task significantly improves hit rate on the first attempt.

---

## Goal

Give the Space Code agent a compact, structured map of the repository — file paths and one-line purposes — as a new section in the engine message it reads before implementing each task.

---

## Scope

**In scope:**
- New module `core/repo_map.py` with a pure-Python file walker and docstring extractor
- Mtime-based JSON cache (caller-supplied path) to avoid rescanning unchanged files
- `MutationRequest.repo_map` field
- New `# Repo Map` section in `render_engine_message`
- Wiring in `execute_headless_handoff`
- 9 tests (7 in `test_repo_map.py`, 2 in `test_engine_interface.py`)

**Out of scope:**
- LLM-generated summaries (too slow, too expensive)
- Symbol/import graph extraction (too large for prompt)
- Filtering by `.gitignore` or file type — the agent should see everything
- Exposing `repo_map` in `HandoffRequest` (kept internal at the runner level)
- CLI flag — map is always built for subprocess and fake providers

---

## Architecture

```
execute_headless_handoff (headless_runner.py)
  │
  ├── build_repo_map(repo_path, cache_path=runtime/repo-map-cache.json)
  │     ├── Walk all files recursively under repo_path
  │     ├── Load cache from cache_path (if exists)
  │     ├── For each file:
  │     │     ├── Check mtime vs cache → use cached summary if unchanged
  │     │     └── Otherwise: _extract_summary(file) → update cache
  │     ├── Save updated cache to cache_path
  │     └── _format_tree(entries) → compact markdown string (≤ max_chars)
  │
  └── MutationRequest(... repo_map=repo_map)
        │
        └── render_engine_message(request)
              └── Injects "# Repo Map\n<tree>" when repo_map is non-empty
```

---

## Module Design: `core/repo_map.py`

### Public API

```python
def build_repo_map(
    repo_path: str | Path,
    *,
    cache_path: str | Path | None = None,
    max_chars: int = 3000,
) -> str:
    """Scan repo_path and return a compact markdown map of files and their purposes.

    Args:
        repo_path:  Root directory to scan.
        cache_path: Optional path for the mtime-based JSON cache. If None, no caching.
        max_chars:  Maximum character count for the returned string. Truncated if exceeded.

    Returns:
        Compact markdown string. Empty string if repo_path is empty or unreadable.
    """
```

### Internal Functions

**`_extract_summary(file_path: Path) -> str`**

Returns a one-line summary string (max 120 chars), or `""` if none found.

- If `.py`: parse with `ast.parse(source, type_comments=False)`, return the value of the first `ast.Expr` node whose value is an `ast.Constant` of type `str` (module docstring). Take only the first line of the docstring.
- For all other text files: read first 4 lines, return the first non-empty line that starts with `#`, `//`, `/*`, `"""`, or `'''` (stripped of comment markers). Skip shebang lines (`#!`).
- If file is binary (`_is_text_file` returns `False`): return `""`.
- On any `OSError`, `SyntaxError`, or `UnicodeDecodeError`: return `""` silently.

**`_is_text_file(file_path: Path) -> bool`**

Read first 512 bytes. If any null byte is present → `False`. Otherwise → `True`. On `OSError` → `False`.

**`_format_tree(entries: list[tuple[Path, str]], repo_root: Path) -> str`**

Takes a list of `(absolute_path, summary)` pairs, sorted by path. Groups by top-level directory relative to `repo_root`. Produces:

```
## src/nemo_coding_platform/core/
  event_emitter.py — Write NEMO_EVENT stdout lines for live timeline streaming.
  headless_runner.py — Execute isolated Full Handoff runs with NEMO context and quality engine.
  repo_map.py — Repo map builder: scan directory tree and extract per-file summaries.

## apps/mission-control/src/
  main.tsx — Mission Control React frontend: chat, timeline, workbench.
```

Files without a summary are listed as:
```
  hand.png
```

If total length exceeds `max_chars`, truncate at `max_chars` with a trailing `\n[truncated]` marker.

**`_load_cache(cache_path: Path) -> dict[str, dict]`**

Read JSON from `cache_path`. Return `{}` on any error. Structure:
```json
{"src/nemo_coding_platform/core/event_emitter.py": {"mtime": 1716000000.123, "summary": "Write NEMO_EVENT..."}}
```

**`_save_cache(cache_path: Path, data: dict[str, dict]) -> None`**

Write `data` as JSON to `cache_path`. Create parent directories if needed. Silently ignore `OSError`.

### Cache Invalidation

For each file encountered during the walk:
1. Compute `rel_path = str(file_path.relative_to(repo_root))` (forward slashes)
2. Get `current_mtime = file_path.stat().st_mtime`
3. If `rel_path in cache` and `cache[rel_path]["mtime"] == current_mtime` → use `cache[rel_path]["summary"]`
4. Else → call `_extract_summary(file_path)`, store `{mtime: current_mtime, summary: result}` in cache

After processing all files, save the updated cache (only if `cache_path` is provided).

---

## Changes to `engine_interface.py`

### `MutationRequest` — new field

```python
@dataclass(frozen=True, slots=True)
class MutationRequest:
    ...
    repo_map: str = ""   # compact markdown repo map, injected as "# Repo Map" section
```

### `render_engine_message` — new section

After `# NEMO Context` and before `# Memory Tools`:

```python
repo_map_section = ("\n", "# Repo Map", request.repo_map) if request.repo_map.strip() else ()
```

Included in the `"\n".join(...)` call. The section is entirely omitted when `repo_map` is empty, preserving full backward compatibility.

---

## Changes to `headless_runner.py`

In `execute_headless_handoff`, after `runtime` and `agent_runtime` are set up and before `MutationRequest` is constructed:

```python
from nemo_coding_platform.core.repo_map import build_repo_map

_repo_map = build_repo_map(
    request.repo_path,
    cache_path=runtime.worktree_path / "repo-map-cache.json",
)
```

Pass into `MutationRequest`:
```python
mutation_request = MutationRequest(
    ...
    repo_map=_repo_map,
)
```

Also pass into the `retry_request` (chunked retry path):
```python
retry_request = MutationRequest(
    ...
    repo_map=mutation_request.repo_map,
)
```

No other files need changes.

---

## Tests

### `tests/test_repo_map.py` (7 new tests)

| Test | What it verifies |
|------|-----------------|
| `test_build_repo_map_returns_string` | Returns non-empty string for a tmp_path with `.py` files |
| `test_python_docstring_extracted` | `.py` with `"""My module."""` → summary `"My module."` appears in output |
| `test_non_python_comment_extracted` | `.ts` with `// React component` → summary appears in output |
| `test_binary_file_has_no_summary` | File with null bytes → path appears, no crash, no summary line |
| `test_mtime_cache_avoids_rescan` | Build twice; patch `_extract_summary` to count calls; second build call count doesn't increase for unchanged file |
| `test_cache_invalidated_on_file_change` | Build, modify file mtime, rebuild; summary re-extracted for changed file |
| `test_max_chars_truncates_output` | Large file tree → output length ≤ `max_chars` |

### `tests/test_engine_interface.py` (2 new tests)

| Test | What it verifies |
|------|-----------------|
| `test_render_engine_message_includes_repo_map` | `MutationRequest(repo_map="my map")` → `"# Repo Map"` in output |
| `test_render_engine_message_empty_repo_map_omitted` | `repo_map=""` → `"# Repo Map"` NOT in output |

---

## Output Example

Given this repo, the `# Repo Map` section in the engine message would look like:

```
# Repo Map

## src/nemo_coding_platform/
  cli.py — CLI entrypoint for the Space Code / Mission Control platform.
  mission_control_server.py — ThreadingHTTPServer backend for Mission Control UI and handoff orchestration.

## src/nemo_coding_platform/core/
  checkpoint.py — Save and restore atomic execution snapshots for deterministic resume.
  engine_interface.py — MutationRequest, MutationResult, and EngineProvider protocol.
  event_emitter.py — Write NEMO_EVENT stdout lines for live timeline streaming.
  headless_runner.py — Execute isolated Full Handoff runs with NEMO context and quality engine.
  headless_handoff.py — HandoffRequest, HandoffPlan, and build_handoff_plan factory.
  permission_engine.py — PermissionRuleset: evaluate write/exec actions against policy profiles.
  repo_map.py — Repo map builder: scan directory tree and extract per-file summaries.
  worktree_runtime.py — Git worktree create/cleanup/diff/merge helpers for task isolation.

## apps/mission-control/src/
  main.tsx — Mission Control React frontend: AgentPane, RunsWorkbench, HandoffComposer.
  styles.css — Mission Control UI styles.

## tests/
  test_engine_interface.py — Tests for MutationRequest rendering and engine provider selection.
  test_live_timeline.py — Tests for NEMO_EVENT protocol, HandoffJobStatus, timeline SSE.
  test_repo_map.py — Tests for repo map builder, docstring extraction, and mtime cache.
```

---

## File Summary

| File | Change |
|------|--------|
| `src/nemo_coding_platform/core/repo_map.py` | **New** — full module |
| `src/nemo_coding_platform/core/engine_interface.py` | Add `repo_map: str = ""` to `MutationRequest`; add `# Repo Map` section in `render_engine_message` |
| `src/nemo_coding_platform/core/headless_runner.py` | Call `build_repo_map`, pass `repo_map` into both `MutationRequest` and `retry_request` |
| `tests/test_repo_map.py` | **New** — 7 tests |
| `tests/test_engine_interface.py` | 2 new tests |

---

## Acceptance Criteria

1. `pytest tests/test_repo_map.py tests/test_engine_interface.py -v` — all 9 new tests pass.
2. `pytest tests/` — full suite passes (no regressions).
3. A fake-provider headless run produces a `mutation_request` whose message contains `# Repo Map` with at least 5 file entries.
4. Cache file is written to `runtime.worktree_path / "repo-map-cache.json"` after a run.
5. Second run with the same repo (unchanged files) completes faster because `_extract_summary` is not called for cached files.
6. Binary files (e.g., `.png`) do not cause errors and appear in the map as path-only entries.
