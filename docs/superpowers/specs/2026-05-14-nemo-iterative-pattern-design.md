# GAP-06 NEMO Iterative Pattern — Design Spec

**Date:** 2026-05-14
**Sprint:** GAP-06 completion (Phase B)
**Status:** Approved for implementation

---

## Problem

The NEMO MCP infrastructure is fully wired in the outer handoff pipeline (`headless_runner.py`: context_bootstrap, portfolio, create_correction, store_conversation) and the CLI → subprocess chain passes `--mcp-url` correctly. However two loops inside the pipeline run completely blind to NEMO:

1. **`repair_engine.py` — `run_repair_loop`**: iterates up to N times trying to fix validation failures with no NEMO calls. It cannot search for similar past failures before each attempt, and intermediate failures are only ingested post-loop via a callback — meaning a crash during repair loses all attempt history.

2. **`long_handoff_supervisor.py` — `execute_long_handoff_continuation`**: when resuming a paused multi-session run, it emits a `resumed` event but never queries NEMO for what happened in prior iterations. The agent starts the continuation blind to all prior failed strategies.

Additionally: the NEMO integration pattern (search before act → ingest failure → ingest success) is repeated ad-hoc in `headless_runner.py` and would need to be duplicated in every future loop.

---

## Goal

1. Extract the **NEMO iterative memory pattern** into a single reusable module (`nemo_patterns.py`) so any loop can adopt it without reimplementing lifecycle phase checks, error handling, or stub detection.
2. Wire the pattern into `run_repair_loop` — search before each attempt, ingest each failure in real-time, ingest the final solution.
3. Wire cross-run NEMO context into `execute_long_handoff_continuation` — query prior iteration history before resuming.
4. Keep `InMemoryNemoAdapter` (test stub) as a silent no-op in all pattern functions.

---

## Scope

**In scope:**
- `src/nemo_coding_platform/core/nemo_patterns.py` (new)
- `src/nemo_coding_platform/core/repair_engine.py` — `run_repair_loop` gets `nemo_adapter` + `task_id` params and 3 call sites
- `src/nemo_coding_platform/core/long_handoff_supervisor.py` — `nemo_cross_run_context` call in continuation
- `src/nemo_coding_platform/core/headless_runner.py` — pass `adapter` + `task.id` to repair loop
- `tests/test_nemo_patterns.py` (new, 6 tests)
- `tests/test_repair_engine.py` — +1 integration test

**Out of scope (Phase C, next sprint):**
- Per-iteration `build_context_portfolio` refresh in repair loop
- Validation success/failure learning outside repair (broader validation phase wiring)
- Permission engine denial logging to NEMO

---

## Core Principle

Any iterative LLM coding loop has four natural NEMO moments:

```
Before attempt   → search_memories(similar failures/solutions) → inject into context
After failure    → cognitive_ingest(evidence)                  → persists immediately
After success    → cognitive_ingest(solution, importance=8)    → persists with high priority
On continuation  → search_memories(tags=[task_id])             → rebuild cross-run history
```

The key insight: **NEMO turns ephemeral iteration context into a persistent, searchable knowledge graph**. Without NEMO, each iteration starts with only the current context window. With NEMO, the agent sees what approaches failed in prior runs and what solutions worked — preventing repetition and hallucination.

---

## Architecture

```
nemo_patterns.py  (new — 4 functions, ~90 lines)
       ↑ imported by
repair_engine.py  ← nemo_before_attempt + nemo_after_failure + nemo_after_success
       ↑ wired by
headless_runner.py → passes adapter + task.id into run_repair_loop call

long_handoff_supervisor.py ← nemo_cross_run_context before each continuation
```

---

## Module: `nemo_patterns.py`

File: `src/nemo_coding_platform/core/nemo_patterns.py`

```python
from __future__ import annotations

from nemo_coding_platform.core.nemo_adapter import InMemoryNemoAdapter
from nemo_coding_platform.core.nemo_lifecycle import NemoLifecyclePhase


def nemo_before_attempt(
    adapter: object,
    query: str,
    tags: tuple[str, ...] = (),
    limit: int = 3,
) -> str:
    """Search NEMO for similar past failures/solutions before an attempt.

    Returns a formatted context snippet (possibly empty) to prepend to the
    repair context. Never raises — NEMO errors return empty string silently.
    Skips InMemoryNemoAdapter (test stub) and None.
    """
    if adapter is None or isinstance(adapter, InMemoryNemoAdapter):
        return ""
    try:
        _, result = adapter.call(
            NemoLifecyclePhase.BUILD,
            "search_memories",
            query=query,
            limit=limit,
            compact=True,
            tags_include=list(tags) if tags else [],
        )
        if not result.ok:
            return ""
        memories = result.payload.get("results") or result.payload.get("memories") or ""
        if not memories:
            return ""
        return f"\n# Prior NEMO Memory — similar failures and solutions\n{memories}\n"
    except Exception:
        return ""


def nemo_after_failure(
    adapter: object,
    evidence: str,
    task_id: str = "",
    attempt_n: int = 0,
    tags: tuple[str, ...] = ("repair_failure",),
) -> None:
    """Immediately ingest a repair failure into NEMO.

    Called after each failed attempt — not post-loop — so crashes during repair
    do not lose intermediate failure history. Never raises.
    """
    if adapter is None or isinstance(adapter, InMemoryNemoAdapter):
        return
    try:
        content_parts = []
        if task_id:
            content_parts.append(f"task={task_id}")
        content_parts.append(f"repair_attempt_{attempt_n}: {evidence}")
        content = " ".join(content_parts)
        all_tags = list(tags)
        if task_id:
            all_tags.append(task_id)
        adapter.call(
            NemoLifecyclePhase.REVIEW,
            "cognitive_ingest",
            content=content,
            memory_type="repair_failure",
            tags=all_tags,
            context=f"attempt {attempt_n}",
        )
    except Exception:
        pass


def nemo_after_success(
    adapter: object,
    solution_summary: str,
    task_id: str = "",
    tags: tuple[str, ...] = ("repair_success",),
) -> None:
    """Ingest a successful repair solution into NEMO with high importance.

    importance_level=8 ensures this is retrieved preferentially in future
    search_memories calls about similar problems. Never raises.
    """
    if adapter is None or isinstance(adapter, InMemoryNemoAdapter):
        return
    try:
        content_parts = []
        if task_id:
            content_parts.append(f"task={task_id}")
        content_parts.append(solution_summary)
        content = " ".join(content_parts)
        all_tags = list(tags)
        if task_id:
            all_tags.append(task_id)
        adapter.call(
            NemoLifecyclePhase.REVIEW,
            "cognitive_ingest",
            content=content,
            memory_type="repair_success",
            tags=all_tags,
            importance_level=8,
        )
    except Exception:
        pass


def nemo_cross_run_context(
    adapter: object,
    task_id: str,
    objective: str,
    limit: int = 8,
) -> str:
    """Retrieve cross-run context for a continuation — what happened in prior iterations.

    Used by long_handoff_supervisor before resuming a paused run. Never raises.
    Returns formatted string or "" if nothing found or adapter is stub.
    """
    if adapter is None or isinstance(adapter, InMemoryNemoAdapter):
        return ""
    try:
        _, result = adapter.call(
            NemoLifecyclePhase.BUILD,
            "search_memories",
            query=f"task {task_id}: {objective[:80]}",
            limit=limit,
            compact=True,
            tags_include=[task_id] if task_id else [],
        )
        if not result.ok:
            return ""
        memories = result.payload.get("results") or result.payload.get("memories") or ""
        if not memories:
            return ""
        return f"\n# NEMO Cross-Run Memory — prior iteration history for task {task_id}\n{memories}\n"
    except Exception:
        return ""
```

---

## Changes: `repair_engine.py`

### New parameters on `run_repair_loop`

```python
def run_repair_loop(
    initial_validation: ValidationSuiteResult,
    commands: tuple[str, ...],
    fail_validation: tuple[str, ...],
    budget: RepairBudget,
    engine: QualityMutationEngine,
    provider: EngineProvider,
    base_request: MutationRequest,
    validator: Callable[[], ValidationSuiteResult] | None = None,
    todo_reminder: str = "",
    time_limit_seconds: float | None = None,
    on_attempt: Callable[[int, float], None] | None = None,
    token_budget: int | None = None,
    attempt_offset: int = 0,
    evidence_compactor: Callable[[str, int], tuple[str, str | None]] | None = None,
    nemo_adapter: object = None,     # NEW
    task_id: str = "",               # NEW
) -> RepairRunResult:
```

### Import at top of file

```python
from nemo_coding_platform.core.nemo_patterns import nemo_before_attempt, nemo_after_failure, nemo_after_success
```

### Call site 1 — before building `repair_context` (inside the while loop)

```python
# Search NEMO for similar past failures before building repair context
nemo_snippet = nemo_before_attempt(
    nemo_adapter,
    query=f"repair failure: {failed}",
    tags=("repair_failure",),
)
```

Then prepend `nemo_snippet` to `repair_context`:
```python
repair_context = "\n".join(
    item
    for item in (
        nemo_snippet,
        base_request.context,
        "",
        *repair_evidence_lines,
    )
    if item is not None
)
```

### Call site 2 — after `apply_mutation_request`, before re-running validation

```python
# Ingest this failure immediately — before next iteration, not post-loop
nemo_after_failure(nemo_adapter, raw_validation_evidence, task_id, attempt_number)
```

This call goes after `raw_validation_evidence` is computed and after `validation_evidence` is computed (since we pass the raw form for maximum detail).

### Call site 3 — after the while loop, when repair passed

```python
if validation.passed and mutations:
    nemo_after_success(
        nemo_adapter,
        f"fixed: {base_request.objective}. diff: {mutations[-1].diff_artifact[:500]}",
        task_id,
    )
```

---

## Changes: `long_handoff_supervisor.py`

### Import at top of file

```python
from nemo_coding_platform.core.nemo_patterns import nemo_cross_run_context
```

### In `execute_long_handoff_continuation`

After the `resumed` emit (added in GAP-04), before calling `execute_long_handoff_supervisor`:

```python
# Retrieve cross-run NEMO context — what failed/succeeded in prior iterations
_prior_nemo_adapter = continuation_kwargs.get("nemo_adapter")
prior_context = nemo_cross_run_context(
    _prior_nemo_adapter,
    task_id=plan.task_id or "",
    objective=source_objective,
)
if prior_context:
    resume_objective = prior_context + "\n\n" + resume_objective
```

---

## Changes: `headless_runner.py`

At the `run_repair_loop` call site, add the two new keyword arguments:

```python
repair_result = run_repair_loop(
    ...,                          # existing args unchanged
    nemo_adapter=adapter,         # adapter already in scope
    task_id=task.id,              # task already in scope
)
```

No other changes to `headless_runner.py`.

---

## Tests

### `tests/test_nemo_patterns.py` (new, 6 tests)

```python
def test_before_attempt_none_adapter():
    result = nemo_before_attempt(None, "repair: pytest failed")
    assert result == ""

def test_before_attempt_in_memory_adapter():
    from nemo_coding_platform.core.nemo_adapter import InMemoryNemoAdapter
    result = nemo_before_attempt(InMemoryNemoAdapter(), "repair: pytest failed")
    assert result == ""

def test_after_failure_none_adapter():
    # Must not raise
    nemo_after_failure(None, "evidence text", task_id="t1", attempt_n=1)

def test_after_failure_calls_cognitive_ingest(mocker):
    adapter = mocker.MagicMock()
    adapter.call.return_value = (adapter, mocker.MagicMock(ok=True, payload={}))
    nemo_after_failure(adapter, "cmd failed with exit 1", task_id="task-42", attempt_n=2)
    adapter.call.assert_called_once()
    call_args = adapter.call.call_args
    assert call_args.kwargs.get("memory_type") == "repair_failure"
    assert "task-42" in call_args.kwargs.get("tags", [])

def test_after_success_uses_high_importance(mocker):
    adapter = mocker.MagicMock()
    adapter.call.return_value = (adapter, mocker.MagicMock(ok=True, payload={}))
    nemo_after_success(adapter, "fixed: add auth. diff: +10 lines", task_id="task-42")
    call_args = adapter.call.call_args
    assert call_args.kwargs.get("importance_level") == 8

def test_cross_run_context_returns_empty_for_none():
    result = nemo_cross_run_context(None, "task-1", "add JWT auth")
    assert result == ""
```

### `tests/test_repair_engine.py` — additional test

```python
def test_run_repair_loop_accepts_nemo_adapter_without_crash():
    from nemo_coding_platform.core.nemo_adapter import InMemoryNemoAdapter
    # Run the loop with InMemoryNemoAdapter — should complete without error
    # (all nemo_patterns calls are no-ops for InMemoryNemoAdapter)
    result = run_repair_loop(
        initial_validation=...,   # use simulate_validation() for a pre-passed result
        ...,
        nemo_adapter=InMemoryNemoAdapter(),
        task_id="test-task",
    )
    assert result is not None
```

---

## Acceptance Criteria

1. `pytest tests/test_nemo_patterns.py -v` — 6 passed
2. `pytest tests/test_repair_engine.py -v` — all pass (no regressions)
3. `run_repair_loop` accepts `nemo_adapter=None` without change in behavior (backward-compatible)
4. When `nemo_adapter` is a live `McpNemoAdapter`, a failing repair run produces `cognitive_ingest` events visible in NEMO search after the run
5. `execute_long_handoff_continuation` prepends cross-run NEMO context to `resume_objective` when adapter is present and memories exist
6. All 4 `nemo_patterns` functions are exception-safe — no NEMO failure propagates to the caller

---

## File Summary

| File | Change |
|------|--------|
| `src/nemo_coding_platform/core/nemo_patterns.py` | New — 4 functions, ~90 lines |
| `src/nemo_coding_platform/core/repair_engine.py` | +2 params on `run_repair_loop`, +3 call sites, +1 import |
| `src/nemo_coding_platform/core/long_handoff_supervisor.py` | +1 import, +5 lines in `execute_long_handoff_continuation` |
| `src/nemo_coding_platform/core/headless_runner.py` | +2 kwargs at `run_repair_loop` call site |
| `tests/test_nemo_patterns.py` | New — 6 tests |
| `tests/test_repair_engine.py` | +1 integration test |
