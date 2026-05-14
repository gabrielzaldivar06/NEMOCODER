# GAP-06 NEMO Iterative Pattern Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the NEMO search-before-act / ingest-after-fail / ingest-after-success / cross-run-context pattern into `nemo_patterns.py` and wire it into `run_repair_loop` and `execute_long_handoff_continuation`.

**Architecture:** A new 90-line module (`nemo_patterns.py`) exposes 4 exception-safe functions. `repair_engine.py` gains two optional parameters (`nemo_adapter`, `task_id`) and 3 call sites inside `run_repair_loop`. `headless_runner.py` passes the already-in-scope `adapter` and `task.id` at the existing call site. `long_handoff_supervisor.py` queries cross-run history before resuming. `InMemoryNemoAdapter` (test stub) is silently skipped by every function.

**Tech Stack:** Python 3.11, `nemo_adapter.py` (`InMemoryNemoAdapter`, `McpNemoAdapter`), `NemoLifecyclePhase` (BUILD for reads, REVIEW for writes), `pytest` + `pytest-mock`.

---

## File Map

| File | Change |
|------|--------|
| `src/nemo_coding_platform/core/nemo_patterns.py` | **New** — 4 public functions, ~90 lines |
| `tests/test_nemo_patterns.py` | **New** — 6 unit tests |
| `src/nemo_coding_platform/core/repair_engine.py` | +1 import, +2 params on `run_repair_loop`, +3 call sites, update `repair_context` assembly |
| `src/nemo_coding_platform/core/headless_runner.py` | +2 kwargs at the `run_repair_loop` call site (~line 601) |
| `src/nemo_coding_platform/core/long_handoff_supervisor.py` | +1 import, +5 lines in `execute_long_handoff_continuation` |
| `tests/test_repair_engine.py` | +1 integration test |

---

## Task 1: Create `nemo_patterns.py`

**Files:**
- Create: `src/nemo_coding_platform/core/nemo_patterns.py`

- [ ] **Step 1: Create the file with all 4 functions**

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

- [ ] **Step 2: Verify the module imports cleanly**

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -c "from nemo_coding_platform.core.nemo_patterns import nemo_before_attempt, nemo_after_failure, nemo_after_success, nemo_cross_run_context; print('OK')"
```

Expected: `OK` with no errors.

- [ ] **Step 3: Commit**

```powershell
git add src/nemo_coding_platform/core/nemo_patterns.py
git commit -m "feat: add nemo_patterns module with 4 exception-safe NEMO lifecycle helpers"
```

---

## Task 2: Tests for `nemo_patterns.py`

**Files:**
- Create: `tests/test_nemo_patterns.py`

- [ ] **Step 1: Create the test file**

```python
import pytest
from nemo_coding_platform.core.nemo_patterns import (
    nemo_before_attempt,
    nemo_after_failure,
    nemo_after_success,
    nemo_cross_run_context,
)


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

- [ ] **Step 2: Run all 6 tests**

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m pytest tests/test_nemo_patterns.py -v
```

Expected: `6 passed`

- [ ] **Step 3: Commit**

```powershell
git add tests/test_nemo_patterns.py
git commit -m "test: add 6 unit tests for nemo_patterns module"
```

---

## Task 3: Wire `repair_engine.py`

**Files:**
- Modify: `src/nemo_coding_platform/core/repair_engine.py`

**Context:** `run_repair_loop` is at line 45. The `failed` variable is computed at line 88. `repair_evidence_lines` is built at lines 126-133. `repair_context` is assembled at lines 135-143. `lint_evidence` is set at line 185. The final `return` is at line 201.

- [ ] **Step 1: Add the import at the top of the file**

Find the last import line in `repair_engine.py` (currently `from nemo_coding_platform.core.post_mutation_lint import lint_changed_files, format_lint_evidence` at line 12). Add immediately after it:

```python
from nemo_coding_platform.core.nemo_patterns import nemo_before_attempt, nemo_after_failure, nemo_after_success
```

- [ ] **Step 2: Add `nemo_adapter` and `task_id` parameters to `run_repair_loop`**

Find the current signature (lines 45–60):

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
) -> RepairRunResult:
```

Replace with (add the two new params at the end, before `-> RepairRunResult:`):

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
    nemo_adapter: object = None,
    task_id: str = "",
) -> RepairRunResult:
```

- [ ] **Step 3: Add Call site 1 — NEMO search before building repair context**

Find this line inside the while loop (line 124):
```python
        noop_attempt = not mutations[-1].changed_files if mutations else False
```

Add the NEMO search immediately after it (before `repair_evidence_lines = [`):

```python
        noop_attempt = not mutations[-1].changed_files if mutations else False
        nemo_snippet = nemo_before_attempt(
            nemo_adapter,
            query=f"repair failure: {failed}",
            tags=("repair_failure",),
        )
```

- [ ] **Step 4: Update `repair_context` assembly to include `nemo_snippet`**

Find the `repair_context` block (lines 135–143):
```python
        repair_context = "\n".join(
            item
            for item in (
                base_request.context,
                "",
                *repair_evidence_lines,
            )
            if item is not None
        )
```

Replace with (prepend `nemo_snippet`, use `if item` to skip empty string):

```python
        repair_context = "\n".join(
            item
            for item in (
                nemo_snippet,
                base_request.context,
                "",
                *repair_evidence_lines,
            )
            if item
        )
```

- [ ] **Step 5: Add Call site 2 — ingest each failure immediately after lint**

Find this line (line 185):
```python
        lint_evidence = format_lint_evidence(lint_results)
```

Add the failure ingest immediately after it:

```python
        lint_evidence = format_lint_evidence(lint_results)
        nemo_after_failure(nemo_adapter, raw_validation_evidence, task_id, attempt_number)
```

- [ ] **Step 6: Add Call site 3 — ingest success before returning**

Find the final lines of the function (lines 199–201):
```python
    if not validation.passed and not stop_reason and not plan.can_record_attempt():
        stop_reason = "repair_budget_exhausted"
    return RepairRunResult(plan, tuple(mutations), validation, stop_reason, tokens_consumed=_tokens_consumed)
```

Replace with:

```python
    if not validation.passed and not stop_reason and not plan.can_record_attempt():
        stop_reason = "repair_budget_exhausted"
    if validation.passed and mutations:
        nemo_after_success(
            nemo_adapter,
            f"fixed: {base_request.objective}. diff: {mutations[-1].diff_artifact[:500]}",
            task_id,
        )
    return RepairRunResult(plan, tuple(mutations), validation, stop_reason, tokens_consumed=_tokens_consumed)
```

- [ ] **Step 7: Run existing repair_engine tests to verify no regressions**

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m pytest tests/test_repair_engine.py -v
```

Expected: all tests pass (same count as before — no new test yet).

- [ ] **Step 8: Commit**

```powershell
git add src/nemo_coding_platform/core/repair_engine.py
git commit -m "feat: wire NEMO iterative pattern into run_repair_loop (search/ingest per attempt)"
```

---

## Task 4: Wire `headless_runner.py`

**Files:**
- Modify: `src/nemo_coding_platform/core/headless_runner.py`

**Context:** `adapter` is set at line 339 (`adapter = nemo_adapter or InMemoryNemoAdapter()`). `task.id` is the task identifier already in scope. The `run_repair_loop` call is at line 601 and ends at line 629.

- [ ] **Step 1: Add the two new kwargs to the `run_repair_loop` call**

Find the end of the `run_repair_loop` call block (around line 628–629):
```python
                attempt_offset=seeded_repair_cursor,
                evidence_compactor=_compress_repair_evidence,
            )
```

Replace with:

```python
                attempt_offset=seeded_repair_cursor,
                evidence_compactor=_compress_repair_evidence,
                nemo_adapter=adapter,
                task_id=task.id,
            )
```

- [ ] **Step 2: Run the full test suite to verify no regressions**

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m pytest tests/test_repair_engine.py tests/test_nemo_patterns.py -v
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

```powershell
git add src/nemo_coding_platform/core/headless_runner.py
git commit -m "feat: pass nemo_adapter and task_id into run_repair_loop from headless_runner"
```

---

## Task 5: Wire `long_handoff_supervisor.py`

**Files:**
- Modify: `src/nemo_coding_platform/core/long_handoff_supervisor.py`

**Context:** `execute_long_handoff_continuation` starts at line 383. The `emit_event("resumed", ...)` block is at lines 404–409. `result = execute_long_handoff_supervisor(...)` is at line 410. `continuation_kwargs` is a `dict[str, object]` built from `**handoff_kwargs` at line 392 — it may contain `nemo_adapter` if the caller passed it.

- [ ] **Step 1: Add the import**

Find the existing nemo_adapter import at line 12:
```python
from nemo_coding_platform.core.nemo_adapter import InMemoryNemoAdapter, PersistentNemoAdapter
```

Add a new import line immediately after it:
```python
from nemo_coding_platform.core.nemo_patterns import nemo_cross_run_context
```

- [ ] **Step 2: Add the cross-run context retrieval in `execute_long_handoff_continuation`**

Find this block (lines 404–410):
```python
    emit_event(
        "resumed",
        f"Resumed from pause — continuing {source_objective[:60]}",
        "execute",
        {"elapsed_minutes": 0.0},
    )
    result = execute_long_handoff_supervisor(
```

Replace with:

```python
    emit_event(
        "resumed",
        f"Resumed from pause — continuing {source_objective[:60]}",
        "execute",
        {"elapsed_minutes": 0.0},
    )
    _prior_nemo_adapter = continuation_kwargs.get("nemo_adapter")
    prior_context = nemo_cross_run_context(
        _prior_nemo_adapter,
        task_id=plan.task_id or "",
        objective=source_objective,
    )
    if prior_context:
        resume_objective = prior_context + "\n\n" + resume_objective
    result = execute_long_handoff_supervisor(
```

- [ ] **Step 3: Run the supervisor tests to verify no regressions**

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m pytest tests/test_long_handoff_supervisor.py tests/test_nemo_patterns.py -v
```

Expected: all tests pass. (The pre-existing Windows PermissionError in `test_long_handoff_supervisor.py` on `.nemo-runtimes/` cleanup is unrelated — skip if it appears, it predates this work.)

- [ ] **Step 4: Commit**

```powershell
git add src/nemo_coding_platform/core/long_handoff_supervisor.py
git commit -m "feat: inject NEMO cross-run context into execute_long_handoff_continuation"
```

---

## Task 6: Integration test and full verification

**Files:**
- Modify: `tests/test_repair_engine.py`

- [ ] **Step 1: Add the integration test to `RepairEngineTests`**

Open `tests/test_repair_engine.py`. Find the `RepairEngineTests` class. Add this test method to the class (after the last existing test method):

```python
    def test_run_repair_loop_accepts_nemo_adapter_without_crash(self) -> None:
        from nemo_coding_platform.core.nemo_adapter import InMemoryNemoAdapter
        with tempfile.TemporaryDirectory() as tmp:
            # simulate_validation with no commands → pre-passed, loop body never runs
            result = run_repair_loop(
                simulate_validation((), ()),
                (),
                (),
                RepairBudget(1),
                QualityMutationEngine(Workspace.from_path(tmp)),
                FakeEngineProvider(),
                MutationRequest("Build", "spec.md", ("ok",), "context"),
                nemo_adapter=InMemoryNemoAdapter(),
                task_id="test-task",
            )
        self.assertIsNotNone(result)
        self.assertEqual(result.stop_reason, "")
```

- [ ] **Step 2: Run the new test alone**

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m pytest tests/test_repair_engine.py::RepairEngineTests::test_run_repair_loop_accepts_nemo_adapter_without_crash -v
```

Expected: `1 passed`

- [ ] **Step 3: Run the full acceptance suite**

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m pytest tests/test_nemo_patterns.py tests/test_repair_engine.py tests/test_live_timeline.py -v
```

Expected: all tests pass (`6 + existing + 1 new + 11 = all green`).

- [ ] **Step 4: Commit**

```powershell
git add tests/test_repair_engine.py
git commit -m "test: integration test for run_repair_loop with InMemoryNemoAdapter"
```

---

## Verification

After all 6 tasks complete:

1. `pytest tests/test_nemo_patterns.py -v` → 6 passed
2. `pytest tests/test_repair_engine.py -v` → all pass (no regressions + 1 new)
3. `pytest tests/test_live_timeline.py -v` → 11 passed (no regressions)
4. `run_repair_loop(nemo_adapter=None, task_id="")` → identical behavior to before (backward-compatible)
5. `run_repair_loop(nemo_adapter=InMemoryNemoAdapter(), task_id="t1")` → no error, all NEMO calls silently skipped
6. `execute_long_handoff_continuation(payload, nemo_adapter=None)` → `resume_objective` unchanged (no NEMO, no prepend)
