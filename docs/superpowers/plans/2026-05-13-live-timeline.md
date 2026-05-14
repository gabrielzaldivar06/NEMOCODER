# Live Timeline (GAP-04) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface typed, real-time timeline events from the Space Code runner subprocess to Mission Control via a `NEMO_EVENT:` stdout protocol, SSE stream, and a new `TimelinePanel` component.

**Architecture:** The runner emits `NEMO_EVENT:{json}` lines to stdout. The server's `_run_job` loop detects the prefix and appends parsed events to `HandoffJob.timeline`. Two new endpoints expose the timeline: a REST snapshot and a SSE live stream. The frontend opens the SSE stream when a job is running and renders events in `TimelinePanel`.

**Tech Stack:** Python 3.11 (backend), React 18 + TypeScript + Vite (frontend), `EventSource` API (SSE client).

---

## File Map

| File | Change |
|------|--------|
| `src/nemo_coding_platform/core/event_emitter.py` | **New** — `emit_event`, `reset_sequence` |
| `src/nemo_coding_platform/mission_control_server.py` | `HandoffJobStatus` enum; `HandoffJob.timeline`; NEMO_EVENT parsing in `_run_job`; `api_run_timeline`; `_handle_timeline_sse`; two new `do_GET` routes |
| `src/nemo_coding_platform/core/headless_runner.py` | 7 `emit_event` calls at key lifecycle points |
| `src/nemo_coding_platform/core/long_handoff_supervisor.py` | 3 `emit_event` calls at heartbeat/pause/resume |
| `apps/mission-control/src/components/TimelinePanel.tsx` | **New** — live + static timeline component |
| `apps/mission-control/src/styles.css` | CSS for `TimelinePanel` |
| `apps/mission-control/src/main.tsx` | `timeline` field on `HandoffJob` type; `jobRunning` memo; `runningJob` prop on `AgentPane`; render `TimelinePanel` |
| `tests/test_live_timeline.py` | **New** — 9 tests |

---

### Task 1: `event_emitter.py` — emit helper

**Files:**
- Create: `src/nemo_coding_platform/core/event_emitter.py`
- Test: `tests/test_live_timeline.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_live_timeline.py
import io, json, sys
import pytest


def test_emit_event_writes_nemo_prefix(capsys):
    from nemo_coding_platform.core.event_emitter import emit_event, reset_sequence
    reset_sequence()
    emit_event("plan_created", "Plan ready", "plan")
    captured = capsys.readouterr()
    assert captured.out.startswith("NEMO_EVENT:")
    data = json.loads(captured.out[len("NEMO_EVENT:"):].strip())
    assert data["kind"] == "plan_created"
    assert data["summary"] == "Plan ready"
    assert data["phase"] == "plan"
    assert data["sequence"] == 1
    assert "ts" in data


def test_emit_event_sequence_increments(capsys):
    from nemo_coding_platform.core.event_emitter import emit_event, reset_sequence
    reset_sequence()
    emit_event("heartbeat", "tick", "execute")
    emit_event("heartbeat", "tock", "execute")
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2
    e1 = json.loads(lines[0][len("NEMO_EVENT:"):])
    e2 = json.loads(lines[1][len("NEMO_EVENT:"):])
    assert e1["sequence"] == 1
    assert e2["sequence"] == 2


def test_reset_sequence(capsys):
    from nemo_coding_platform.core.event_emitter import emit_event, reset_sequence
    reset_sequence()
    emit_event("heartbeat", "a", "execute")
    reset_sequence()
    emit_event("heartbeat", "b", "execute")
    lines = capsys.readouterr().out.strip().splitlines()
    seq_b = json.loads(lines[1][len("NEMO_EVENT:"):])["sequence"]
    assert seq_b == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```powershell
$env:PYTHONPATH = "src"
python -m pytest tests/test_live_timeline.py -v
```

Expected: `ModuleNotFoundError: No module named 'nemo_coding_platform.core.event_emitter'`

- [ ] **Step 3: Create `event_emitter.py`**

```python
# src/nemo_coding_platform/core/event_emitter.py
from __future__ import annotations

import json
from datetime import datetime, timezone

_seq: int = 0


def emit_event(kind: str, summary: str, phase: str, payload: dict | None = None) -> None:
    """Write a NEMO_EVENT line to stdout for the Mission Control server to parse."""
    global _seq
    _seq += 1
    event: dict[str, object] = {
        "kind": kind,
        "summary": summary,
        "phase": phase,
        "sequence": _seq,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    if payload:
        event["payload"] = payload
    print(f"NEMO_EVENT:{json.dumps(event, separators=(',', ':'))}", flush=True)


def reset_sequence() -> None:
    """Reset sequence counter. Call at the start of each run (used in tests)."""
    global _seq
    _seq = 0
```

- [ ] **Step 4: Run tests to verify they pass**

```powershell
$env:PYTHONPATH = "src"
python -m pytest tests/test_live_timeline.py::test_emit_event_writes_nemo_prefix tests/test_live_timeline.py::test_emit_event_sequence_increments tests/test_live_timeline.py::test_reset_sequence -v
```

Expected: 3 PASSED

- [ ] **Step 5: Commit**

```powershell
git add src/nemo_coding_platform/core/event_emitter.py tests/test_live_timeline.py
git commit -m "feat: add event_emitter module with NEMO_EVENT stdout protocol"
```

---

### Task 2: `HandoffJobStatus` enum + `HandoffJob.timeline`

**Files:**
- Modify: `src/nemo_coding_platform/mission_control_server.py`
- Test: `tests/test_live_timeline.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_live_timeline.py`)

```python
def test_handoff_job_status_is_str_enum():
    from nemo_coding_platform.mission_control_server import HandoffJobStatus
    assert HandoffJobStatus.RUNNING == "running"
    assert HandoffJobStatus.AWAITING_PERMISSION == "awaiting_permission"
    assert HandoffJobStatus.COMPLETED == "completed"
    assert HandoffJobStatus.FAILED == "failed"
    assert HandoffJobStatus.PERMISSION_DENIED == "permission_denied"


def test_handoff_job_timeline_in_to_dict():
    from nemo_coding_platform.mission_control_server import HandoffJob
    job = HandoffJob(
        job_id="j1", task_id="t1", run_id="r1", run_json="/tmp/x.json",
        status="running", command=(), payload={}, logs=[],
    )
    job.timeline.append({"kind": "plan_created", "summary": "ok", "sequence": 1})
    d = job.to_dict()
    assert d["timeline"] == [{"kind": "plan_created", "summary": "ok", "sequence": 1}]


def test_handoff_job_from_snapshot_loads_timeline():
    from nemo_coding_platform.mission_control_server import HandoffJob
    snap = {
        "job_id": "j1", "task_id": "t1", "run_id": "r1", "run_json": "/tmp/x.json",
        "status": "completed", "command": [], "payload": {}, "logs": [],
        "timeline": [{"kind": "heartbeat", "sequence": 1}],
    }
    job = HandoffJob.from_snapshot(snap)
    assert len(job.timeline) == 1
    assert job.timeline[0]["kind"] == "heartbeat"
```

- [ ] **Step 2: Run to verify they fail**

```powershell
$env:PYTHONPATH = "src"
python -m pytest tests/test_live_timeline.py::test_handoff_job_status_is_str_enum tests/test_live_timeline.py::test_handoff_job_timeline_in_to_dict tests/test_live_timeline.py::test_handoff_job_from_snapshot_loads_timeline -v
```

Expected: `ImportError` or `AttributeError` (no `HandoffJobStatus`, no `timeline` field)

- [ ] **Step 3: Add `HandoffJobStatus` enum**

In `mission_control_server.py`, find the block of existing string constants near the top of the file (around line 50, where `DEFAULT_APPLY_RESULTS` etc. are defined). Add the enum after the imports section, before `DEFAULT_APPLY_RESULTS`:

```python
from enum import StrEnum as _StrEnum  # add to existing imports at top of file
```

Then add after the import block, before `DEFAULT_APPLY_RESULTS = ...`:

```python
class HandoffJobStatus(_StrEnum):
    STARTING            = "starting"
    AWAITING_PERMISSION = "awaiting_permission"
    RUNNING             = "running"
    COMPLETED           = "completed"
    FAILED              = "failed"
    PERMISSION_DENIED   = "permission_denied"
    ORPHANED            = "orphaned"
    CANCELLED           = "cancelled"


NEMO_EVENT_PREFIX = "NEMO_EVENT:"
```

> Note: `StrEnum` is already imported elsewhere in the file as `from enum import StrEnum`. Add `HandoffJobStatus` using the existing import — don't add a duplicate. Check imports at the top of the file first; if `StrEnum` is already imported, just define the class directly.

- [ ] **Step 4: Add `timeline` field to `HandoffJob`**

In the `HandoffJob` dataclass (around line 80), add `timeline` as the last field with a default:

```python
@dataclass(slots=True)
class HandoffJob:
    job_id: str
    task_id: str
    run_id: str
    run_json: str
    status: str
    command: tuple[str, ...]
    payload: dict[str, object]
    logs: list[str]
    returncode: int | None = None
    process: subprocess.Popen[str] | None = None
    heartbeat_stop: threading.Event | None = None
    heartbeat_thread: threading.Thread | None = None
    run_thread: threading.Thread | None = None
    error: str | None = None
    last_runtime_signature: str = ""
    stagnant_heartbeats: int = 0
    permission_request: dict[str, object] | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    timeline: list[dict[str, object]] = field(default_factory=list)   # ← add this line
```

- [ ] **Step 5: Update `to_dict()` to include `timeline`**

In `HandoffJob.to_dict()`, add `"timeline": list(self.timeline)` to the returned dict, after `"permission_request"`:

```python
def to_dict(self, *, include_logs: bool = True) -> dict[str, object]:
    payload: dict[str, object] = {
        "job_id": self.job_id,
        "task_id": self.task_id,
        "run_id": self.run_id,
        "run_json": self.run_json,
        "status": self.status,
        "returncode": self.returncode,
        "error": self.error,
        "command": list(self.command),
        "objective": self.payload.get("objective"),
        "timeout_seconds": self.payload.get("timeout_seconds"),
        "created_at": self.created_at,
        "updated_at": self.updated_at,
        "last_runtime_signature": self.last_runtime_signature,
        "stagnant_heartbeats": self.stagnant_heartbeats,
        "permission_request": self.permission_request,
        "timeline": list(self.timeline),    # ← add this line
    }
    if include_logs:
        payload["logs"] = list(self.logs)
    return payload
```

- [ ] **Step 6: Update `from_snapshot()` to load `timeline`**

In `HandoffJob.from_snapshot()`, add `timeline=` to the constructor call:

```python
@classmethod
def from_snapshot(cls, payload: dict[str, Any]) -> "HandoffJob":
    return cls(
        job_id=str(payload.get("job_id") or ""),
        task_id=str(payload.get("task_id") or ""),
        run_id=str(payload.get("run_id") or ""),
        run_json=str(payload.get("run_json") or ""),
        status=str(payload.get("status") or "orphaned"),
        command=tuple(str(item) for item in payload.get("command", []) if isinstance(item, str)),
        payload=dict(payload.get("payload") or {}),
        logs=list(payload.get("logs") or []),
        returncode=payload.get("returncode") if isinstance(payload.get("returncode"), int) else None,
        last_runtime_signature=str(payload.get("last_runtime_signature") or ""),
        stagnant_heartbeats=int(payload.get("stagnant_heartbeats") or 0),
        permission_request=payload.get("permission_request") if isinstance(payload.get("permission_request"), dict) else None,
        created_at=str(payload.get("created_at") or datetime.now(timezone.utc).isoformat()),
        updated_at=str(payload.get("updated_at") or datetime.now(timezone.utc).isoformat()),
        timeline=list(payload.get("timeline") or []),    # ← add this line
    )
```

- [ ] **Step 7: Run tests to verify they pass**

```powershell
$env:PYTHONPATH = "src"
python -m pytest tests/test_live_timeline.py -v
```

Expected: 6 PASSED (3 from Task 1 + 3 from Task 2)

- [ ] **Step 8: Commit**

```powershell
git add src/nemo_coding_platform/mission_control_server.py tests/test_live_timeline.py
git commit -m "feat: add HandoffJobStatus enum, HandoffJob.timeline field, NEMO_EVENT_PREFIX constant"
```

---

### Task 3: NEMO_EVENT parsing + `api_run_timeline` + REST route

**Files:**
- Modify: `src/nemo_coding_platform/mission_control_server.py`
- Test: `tests/test_live_timeline.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_live_timeline.py`)

```python
def _make_job(**kwargs):
    from nemo_coding_platform.mission_control_server import HandoffJob
    defaults = dict(
        job_id="j1", task_id="t1", run_id="r1", run_json="/tmp/x.json",
        status="running", command=(), payload={}, logs=[],
    )
    defaults.update(kwargs)
    return HandoffJob(**defaults)


def test_nemo_event_prefix_detected():
    """NEMO_EVENT: line goes to timeline, not logs."""
    from nemo_coding_platform.mission_control_server import NEMO_EVENT_PREFIX
    import json as _json
    job = _make_job()
    line = f'{NEMO_EVENT_PREFIX}{_json.dumps({"kind": "plan_created", "sequence": 1, "summary": "ok", "phase": "plan"})}'
    # Simulate what _run_job does when it sees the line
    if line.startswith(NEMO_EVENT_PREFIX):
        try:
            event = _json.loads(line[len(NEMO_EVENT_PREFIX):])
            job.timeline.append(event)
        except (_json.JSONDecodeError, ValueError):
            pass
    else:
        job.logs.append(line)
    assert len(job.timeline) == 1
    assert job.timeline[0]["kind"] == "plan_created"
    assert len(job.logs) == 0


def test_malformed_json_ignored():
    """NEMO_EVENT: with invalid JSON does not crash and is not added to timeline."""
    from nemo_coding_platform.mission_control_server import NEMO_EVENT_PREFIX
    import json as _json
    job = _make_job()
    line = f"{NEMO_EVENT_PREFIX}not-valid-json"
    if line.startswith(NEMO_EVENT_PREFIX):
        try:
            event = _json.loads(line[len(NEMO_EVENT_PREFIX):])
            job.timeline.append(event)
        except (_json.JSONDecodeError, ValueError):
            pass
    else:
        job.logs.append(line)
    assert len(job.timeline) == 0
    assert len(job.logs) == 0


def test_api_run_timeline_not_found(tmp_path):
    from nemo_coding_platform.mission_control_server import api_run_timeline, HandoffJobManager, MissionControlServerConfig
    manager = HandoffJobManager()
    config = MissionControlServerConfig(
        repo_path=tmp_path, runtimes_path=tmp_path, run_results_path=tmp_path,
        apply_results_path=tmp_path, memory_db=None,
    )
    result = api_run_timeline(config, "nonexistent", manager)
    assert "error" in result


def test_api_run_timeline_returns_events(tmp_path):
    from nemo_coding_platform.mission_control_server import api_run_timeline, HandoffJobManager, MissionControlServerConfig, HandoffJob
    manager = HandoffJobManager()
    job = _make_job(job_id="j99")
    job.timeline.append({"kind": "heartbeat", "sequence": 1})
    manager._jobs["j99"] = job
    config = MissionControlServerConfig(
        repo_path=tmp_path, runtimes_path=tmp_path, run_results_path=tmp_path,
        apply_results_path=tmp_path, memory_db=None,
    )
    result = api_run_timeline(config, "j99", manager)
    assert result["job_id"] == "j99"
    assert result["event_count"] == 1
    assert result["timeline"][0]["kind"] == "heartbeat"
```

- [ ] **Step 2: Run to verify they fail**

```powershell
$env:PYTHONPATH = "src"
python -m pytest tests/test_live_timeline.py::test_nemo_event_prefix_detected tests/test_live_timeline.py::test_malformed_json_ignored tests/test_live_timeline.py::test_api_run_timeline_not_found tests/test_live_timeline.py::test_api_run_timeline_returns_events -v
```

Expected: `ImportError` on `api_run_timeline`; others pass (they only use `NEMO_EVENT_PREFIX`).

- [ ] **Step 3: Modify `_run_job` stdout loop**

In `mission_control_server.py`, find the stdout loop in `_run_job` (lines ~723–728):

```python
# BEFORE:
if job.process.stdout:
    try:
        for line in job.process.stdout:
            self._append_log(job, line.rstrip())
    finally:
        job.process.stdout.close()
```

Replace with:

```python
# AFTER:
if job.process.stdout:
    try:
        for raw_line in job.process.stdout:
            line = raw_line.rstrip("\n")
            if line.startswith(NEMO_EVENT_PREFIX):
                try:
                    event = json.loads(line[len(NEMO_EVENT_PREFIX):])
                    with self._lock:
                        job.timeline.append(event)
                        job.updated_at = datetime.now(timezone.utc).isoformat()
                except (json.JSONDecodeError, ValueError):
                    pass
            else:
                self._append_log(job, line)
    finally:
        job.process.stdout.close()
```

- [ ] **Step 4: Add `api_run_timeline` function**

Add this function after `api_worktree_cleanup` (search for `def api_worktree_cleanup` and add after its closing):

```python
def api_run_timeline(
    config: "MissionControlServerConfig",
    job_id: str,
    jobs: "HandoffJobManager",
) -> dict[str, object]:
    with jobs._lock:
        job = jobs._jobs.get(job_id)
        if not job:
            return {"error": f"job not found: {job_id}"}
        return {
            "job_id": job_id,
            "status": str(job.status),
            "timeline": list(job.timeline),
            "event_count": len(job.timeline),
        }
```

- [ ] **Step 5: Add REST route in `do_GET`**

In `do_GET`, find the worktree-diff route (line ~6474):

```python
if route.startswith("/api/run/") and route.endswith("/worktree-diff"):
    job_id = route[len("/api/run/"): -len("/worktree-diff")].strip("/")
    self._handle(lambda _: api_worktree_diff(self.server.config, job_id, self.server.jobs), {})
    return
```

Add immediately **after** that block, **before** `if route != "/api/state"`:

```python
if route.startswith("/api/run/") and route.endswith("/timeline"):
    job_id = route[len("/api/run/"): -len("/timeline")].strip("/")
    self._handle(lambda _: api_run_timeline(self.server.config, job_id, self.server.jobs), {})
    return
```

- [ ] **Step 6: Run tests**

```powershell
$env:PYTHONPATH = "src"
python -m pytest tests/test_live_timeline.py -v
```

Expected: all 10 tests PASSED

- [ ] **Step 7: Commit**

```powershell
git add src/nemo_coding_platform/mission_control_server.py tests/test_live_timeline.py
git commit -m "feat: parse NEMO_EVENT lines in _run_job, add api_run_timeline and REST route"
```

---

### Task 4: `_handle_timeline_sse` + SSE route

**Files:**
- Modify: `src/nemo_coding_platform/mission_control_server.py`

No new tests for the SSE handler itself (it requires a live HTTP server; covered by smoke test in Task 8). The REST tests from Task 3 already validate the data layer.

- [ ] **Step 1: Add `_handle_timeline_sse` method**

In `MissionControlHTTPHandler`, add this method immediately after `_handle_plan_sse` (search for `def _handle_plan_sse` and add the new method after its closing):

```python
def _handle_timeline_sse(self, job_id: str) -> None:
    """Stream HandoffJob.timeline events as Server-Sent Events."""
    try:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:5173")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()
    except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
        return

    def _send(data: dict[str, object]) -> bool:
        try:
            line = ("data: " + json.dumps(data, sort_keys=True) + "\n\n").encode("utf-8")
            self.wfile.write(line)
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return False

    _TERMINAL = {"completed", "failed", "permission_denied", "cancelled", "orphaned"}
    _POLL = 0.5  # seconds
    offset = 0

    while True:
        jobs: HandoffJobManager = self.server.jobs
        with jobs._lock:
            job = jobs._jobs.get(job_id)
            if job is None:
                _send({"kind": "stream_end", "status": "not_found"})
                return
            events_slice = list(job.timeline[offset:])
            current_status = str(job.status)

        for event in events_slice:
            if not _send(event):
                return
            offset += 1

        if current_status in _TERMINAL and not events_slice:
            _send({"kind": "stream_end", "status": current_status})
            return

        time.sleep(_POLL)
```

- [ ] **Step 2: Add SSE route in `do_GET`**

Find the timeline REST route added in Task 3:

```python
if route.startswith("/api/run/") and route.endswith("/timeline"):
    job_id = route[len("/api/run/"): -len("/timeline")].strip("/")
    self._handle(lambda _: api_run_timeline(self.server.config, job_id, self.server.jobs), {})
    return
```

Add immediately **before** that block:

```python
if route.startswith("/api/run/") and route.endswith("/timeline/stream"):
    job_id = route[len("/api/run/"): -len("/timeline/stream")].strip("/")
    self._handle_timeline_sse(job_id)
    return
```

> The `/timeline/stream` route must be checked **before** `/timeline` because `/timeline/stream` ends with `/timeline` if you check the wrong suffix. Putting it first prevents the wrong handler being called.

- [ ] **Step 3: Verify `tsc` and `pytest` still clean**

```powershell
$env:PYTHONPATH = "src"
python -m pytest tests/test_live_timeline.py -v
```

Expected: all PASSED (no regressions)

- [ ] **Step 4: Commit**

```powershell
git add src/nemo_coding_platform/mission_control_server.py
git commit -m "feat: add _handle_timeline_sse and SSE route GET /api/run/<id>/timeline/stream"
```

---

### Task 5: Emission points in `headless_runner.py` and `long_handoff_supervisor.py`

**Files:**
- Modify: `src/nemo_coding_platform/core/headless_runner.py`
- Modify: `src/nemo_coding_platform/core/long_handoff_supervisor.py`

No new unit tests — the runner is tested via integration smoke test in Task 8. The `emit_event` function itself is tested in Task 1.

- [ ] **Step 1: Add import to `headless_runner.py`**

At the top of `headless_runner.py`, find the imports block. Add:

```python
from nemo_coding_platform.core.event_emitter import emit_event, reset_sequence
```

- [ ] **Step 2: Reset sequence at the start of `execute_headless_handoff`**

Find `def execute_headless_handoff(` in `headless_runner.py`. As the very first statement in the function body, add:

```python
reset_sequence()
```

This ensures each run starts its sequence from 1.

- [ ] **Step 3: Emit `context_bootstrapped` after NEMO portfolio call**

Find the `build_context_portfolio` call in `headless_runner.py` (around line 347–356). After `nemo_context = _bounded_nemo_context(...)` (the line that uses the portfolio result), add:

```python
emit_event(
    "context_bootstrapped",
    f"Contexto NEMO cargado ({len(nemo_context)} chars)",
    "plan",
    {"nemo_url": str(getattr(request, "nemo_mcp_url", ""))},
)
```

- [ ] **Step 4: Emit `plan_created` after plan is built**

In `headless_runner.py`, find where `plan` is assigned (search for `plan = build_handoff_plan` or equivalent). After the plan is built and before mutation starts, add:

```python
emit_event(
    "plan_created",
    f"Plan: {len(plan.steps)} pasos — {request.objective_summary or 'sin resumen'}",
    "plan",
    {"steps": [step.kind.value for step in plan.steps]},
)
```

- [ ] **Step 5: Emit `mutation_created` after `apply_mutation_request`**

Find `mutation_result = apply_mutation_request(engine, provider, mutation_request)` (around line 433). After that line, add:

```python
emit_event(
    "mutation_created",
    f"Mutación aplicada: {len(mutation_result.changed_files or [])} archivos",
    "execute",
    {"files": list(mutation_result.changed_files or mutation_result.applied_files or [])},
)
```

- [ ] **Step 6: Emit `validation_run` after validation**

Find where `run_validation_suite` is called or `validation` result is produced in the repair loop callback (`on_attempt` lambda, around line 600). After the primary validation check (where `validation.passed` is first evaluated, around line 541), add:

```python
emit_event(
    "validation_run",
    f"Validación: {'✓ pasó' if validation.passed else '✗ falló'}",
    "execute",
    {"passed": validation.passed, "summary": validation.summary()},
)
```

- [ ] **Step 7: Emit `checkpoint` inside `_capture_execution_snapshot`**

Find `_capture_execution_snapshot` inner function (around line 279). After `checkpoint_path = save_execution_snapshot(...)`, add:

```python
emit_event(
    "checkpoint",
    f"Checkpoint guardado: {checkpoint_id}",
    "execute",
    {"checkpoint_id": checkpoint_id, "phase": phase.value},
)
```

- [ ] **Step 8: Emit `review_package_created` near the end**

Find where `workflow_recipe` or `runtime_files` are built (around line 682). This is the end of the run — before returning, add:

```python
emit_event(
    "review_package_created",
    "Run completado — paquete de revisión listo",
    "review",
    {"grade": str(getattr(score_headless_result(locals().get("result", {})), "grade", ""))},
)
```

> Note: this is a best-effort emit — the exact score is not available at this point in all paths. The `payload` can be empty if grade is unavailable; that's fine.

- [ ] **Step 9: Add import to `long_handoff_supervisor.py`**

At the top of `long_handoff_supervisor.py`, add:

```python
from nemo_coding_platform.core.event_emitter import emit_event
```

- [ ] **Step 10: Emit `heartbeat` in the supervisor iteration loop**

Find `execute_long_handoff_supervisor` in `long_handoff_supervisor.py`. Find the loop that calls `execute_headless_handoff` for each iteration (the heartbeat/continuation loop). Before each call to `execute_headless_handoff`, add:

```python
emit_event(
    "heartbeat",
    f"Supervisor: iteración {iteration_index + 1} de {len(planned_heartbeats)}",
    "execute",
    {"iteration": iteration_index + 1, "elapsed_minutes": elapsed_minutes},
)
```

> `iteration_index` and `elapsed_minutes` are the variables available in the supervisor loop — use the actual variable names present in the file.

- [ ] **Step 11: Verify no import errors**

```powershell
$env:PYTHONPATH = "src"
python -c "from nemo_coding_platform.core.headless_runner import execute_headless_handoff; print('ok')"
python -c "from nemo_coding_platform.core.long_handoff_supervisor import execute_long_handoff_supervisor; print('ok')"
```

Expected: `ok` on both lines

- [ ] **Step 12: Run full test suite to check for regressions**

```powershell
$env:PYTHONPATH = "src"
python -m pytest tests/ -v --ignore=tests/test_live_timeline.py -x -q
```

Expected: no new failures

- [ ] **Step 13: Commit**

```powershell
git add src/nemo_coding_platform/core/headless_runner.py src/nemo_coding_platform/core/long_handoff_supervisor.py
git commit -m "feat: emit NEMO_EVENT at lifecycle points in headless_runner and long_handoff_supervisor"
```

---

### Task 6: `TimelinePanel.tsx` + CSS

**Files:**
- Create: `apps/mission-control/src/components/TimelinePanel.tsx`
- Modify: `apps/mission-control/src/styles.css`

- [ ] **Step 1: Create `TimelinePanel.tsx`**

```tsx
// apps/mission-control/src/components/TimelinePanel.tsx
import { useEffect, useRef, useState } from "react";

interface TimelineEvent {
  kind: string;
  summary: string;
  phase: string;
  sequence: number;
  ts: string;
  payload?: Record<string, unknown>;
}

interface Props {
  jobId: string;
  isLive: boolean;
  onStreamEnd?: () => void;
}

const KIND_ICON: Record<string, string> = {
  context_bootstrapped: "🧠",
  plan_created: "📋",
  mutation_created: "⚙",
  validation_run: "✓",
  checkpoint: "💾",
  review_package_created: "📦",
  heartbeat: "◉",
  paused: "⏸",
  resumed: "▶",
  permission_decided: "🔒",
};

function phaseLabel(phase: string) {
  return phase === "plan" ? "plan" : phase === "review" ? "review" : "execute";
}

function formatTs(ts: string) {
  try {
    return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return ts;
  }
}

export function TimelinePanel({ jobId, isLive, onStreamEnd }: Props) {
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [live, setLive] = useState(isLive);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setEvents([]);
    setLive(isLive);

    if (isLive) {
      const es = new EventSource(`/api/run/${jobId}/timeline/stream`);
      es.onmessage = (e) => {
        try {
          const event: TimelineEvent = JSON.parse(e.data);
          if (event.kind === "stream_end") {
            setLive(false);
            es.close();
            onStreamEnd?.();
            return;
          }
          setEvents((prev) => [...prev, event]);
        } catch {
          // ignore malformed SSE data
        }
      };
      es.onerror = () => {
        setLive(false);
        es.close();
      };
      return () => es.close();
    } else {
      // static fetch for completed jobs
      fetch(`/api/run/${jobId}/timeline`)
        .then((r) => r.json())
        .then((data) => {
          if (Array.isArray(data.timeline)) setEvents(data.timeline);
        })
        .catch(() => {});
    }
  }, [jobId, isLive]);

  // auto-scroll to bottom when events arrive
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events.length]);

  return (
    <div className="timeline-panel">
      <div className="timeline-panel-header">
        <span>Timeline</span>
        {live && <span className="timeline-live-dot" title="Live" />}
      </div>
      <div className="timeline-events">
        {events.length === 0 && (
          <div className="timeline-empty">
            {live ? "Esperando eventos…" : "Sin eventos registrados."}
          </div>
        )}
        {events.map((ev, i) => (
          <div className="timeline-event" key={i}>
            <span className="timeline-event-icon">{KIND_ICON[ev.kind] ?? "·"}</span>
            <span className={`timeline-phase-badge ${phaseLabel(ev.phase)}`}>
              {phaseLabel(ev.phase)}
            </span>
            <div className="timeline-event-body">
              <span className="timeline-event-summary">{ev.summary}</span>
              <span className="timeline-event-meta">{formatTs(ev.ts)}</span>
            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Append CSS to `styles.css`**

Open `apps/mission-control/src/styles.css` and append at the very end:

```css
/* ── TimelinePanel ──────────────────────────────────────────────────── */
.timeline-panel { display: flex; flex-direction: column; gap: 0; border: 1px solid #30363d; border-radius: 6px; overflow: hidden; margin: 8px 0; }
.timeline-panel-header { display: flex; align-items: center; justify-content: space-between; padding: 8px 12px; background: #161b22; border-bottom: 1px solid #30363d; font-size: 11px; color: #8b949e; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; }
.timeline-live-dot { width: 7px; height: 7px; border-radius: 50%; background: #f85149; display: inline-block; animation: pulse-dot 1.2s ease-in-out infinite; }
@keyframes pulse-dot { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }
.timeline-events { display: flex; flex-direction: column; max-height: 340px; overflow-y: auto; }
.timeline-event { display: grid; grid-template-columns: 20px 56px 1fr; gap: 6px; align-items: start; padding: 7px 12px; border-bottom: 1px solid #21262d; font-size: 11px; }
.timeline-event:last-child { border-bottom: none; }
.timeline-event-icon { color: #8b949e; font-size: 12px; padding-top: 1px; }
.timeline-phase-badge { display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; white-space: nowrap; }
.timeline-phase-badge.plan    { background: #1c2d4a; color: #79c0ff; }
.timeline-phase-badge.execute { background: #1a2d1a; color: #56d364; }
.timeline-phase-badge.review  { background: #2d1a3a; color: #d2a8ff; }
.timeline-event-body { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.timeline-event-summary { color: #e6edf3; line-height: 1.4; word-break: break-word; }
.timeline-event-meta { color: #8b949e; font-size: 10px; }
.timeline-empty { padding: 20px; text-align: center; color: #8b949e; font-size: 12px; }
```

- [ ] **Step 3: Type-check**

```powershell
cd apps/mission-control && npx tsc --noEmit
```

Expected: zero errors

- [ ] **Step 4: Commit**

```powershell
git add apps/mission-control/src/components/TimelinePanel.tsx apps/mission-control/src/styles.css
git commit -m "feat: add TimelinePanel component and CSS"
```

---

### Task 7: Wiring in `main.tsx`

**Files:**
- Modify: `apps/mission-control/src/main.tsx`

- [ ] **Step 1: Add `timeline` field to `HandoffJob` type**

Find the `HandoffJob` type definition (around line 121):

```typescript
type HandoffJob = {
  job_id: string;
  task_id: string;
  run_id: string;
  run_json: string;
  status: string;
  returncode: number | null;
  error: string | null;
  logs: string[];
  objective?: string | null;
  permission_request?: { ... } | null;
};
```

Add `timeline` field:

```typescript
type HandoffJob = {
  job_id: string;
  task_id: string;
  run_id: string;
  run_json: string;
  status: string;
  returncode: number | null;
  error: string | null;
  logs: string[];
  objective?: string | null;
  permission_request?: {
    job_id: string;
    categories: string[];
    rationale: string;
    auto_approved: string[];
    requires_user_approval: string[];
    decision?: {
      decided_at: string;
      decided_by: string;
      approved: boolean;
      categories: string[];
      note: string;
    };
  } | null;
  timeline?: Array<{
    kind: string;
    summary: string;
    phase: string;
    sequence: number;
    ts: string;
    payload?: Record<string, unknown>;
  }>;
};
```

- [ ] **Step 2: Add import for `TimelinePanel`**

Find the existing import for `PermissionRequestPanel`:

```typescript
import { PermissionRequestPanel } from "./components/PermissionRequestPanel";
```

Add immediately after:

```typescript
import { TimelinePanel } from "./components/TimelinePanel";
```

- [ ] **Step 3: Add `jobRunning` memo**

Find `jobAwaitingPermission` memo (around line 986):

```typescript
const jobAwaitingPermission = useMemo(() => state.jobs.find((j) => j.status === "awaiting_permission") ?? null, [state.jobs]);
```

Add immediately after:

```typescript
const jobRunning = useMemo(() => state.jobs.find((j) => j.status === "running") ?? null, [state.jobs]);
```

- [ ] **Step 4: Extend `AgentPaneProps`**

Find `AgentPaneProps` type (around line 3197):

```typescript
type AgentPaneProps = {
  ...
  permissionJob?: HandoffJob | null;
  onGrantPermission?: (jobId: string, note: string) => void;
  onDenyPermission?: (jobId: string, note: string) => void;
};
```

Add `runningJob` prop:

```typescript
type AgentPaneProps = {
  ...
  permissionJob?: HandoffJob | null;
  onGrantPermission?: (jobId: string, note: string) => void;
  onDenyPermission?: (jobId: string, note: string) => void;
  runningJob?: HandoffJob | null;
};
```

- [ ] **Step 5: Destructure `runningJob` in `AgentPane` function**

Find the `AgentPane` function signature (the line that destructures props). Add `runningJob` to the destructuring:

```typescript
function AgentPane({
  run,
  state,
  readyRuns,
  blockedRuns,
  // ... other props ...
  permissionJob,
  onGrantPermission,
  onDenyPermission,
  runningJob,             // ← add this
}: AgentPaneProps) {
```

- [ ] **Step 6: Render `TimelinePanel` in `AgentPane`**

Find where `PermissionRequestPanel` is rendered inside `<aside className="agent-pane">` (around line 3433):

```tsx
<aside className="agent-pane">
  {permissionJob && permissionJob.permission_request && onGrantPermission && onDenyPermission && (
    <PermissionRequestPanel ... />
  )}
  <div className="panel-title">...
```

Add `TimelinePanel` after `PermissionRequestPanel`:

```tsx
<aside className="agent-pane">
  {permissionJob && permissionJob.permission_request && onGrantPermission && onDenyPermission && (
    <PermissionRequestPanel
      jobId={permissionJob.job_id}
      objective={String(permissionJob.objective || permissionJob.permission_request.rationale || "")}
      permissionRequest={permissionJob.permission_request as { job_id: string; categories: string[]; rationale: string; auto_approved: string[]; requires_user_approval: string[] }}
      onGranted={() => onGrantPermission(permissionJob.job_id, "")}
      onDenied={() => onDenyPermission(permissionJob.job_id, "")}
    />
  )}
  {runningJob && (
    <TimelinePanel
      jobId={runningJob.job_id}
      isLive={true}
    />
  )}
  <div className="panel-title">...
```

- [ ] **Step 7: Pass `runningJob` to `<AgentPane>` call**

Find the `<AgentPane>` JSX call (around line 2634) and add the prop:

```tsx
<AgentPane
  run={selectedRun}
  state={state}
  readyRuns={readyRuns}
  blockedRuns={blockedRuns}
  {/* ... existing props ... */}
  permissionJob={jobAwaitingPermission}
  onGrantPermission={grantPermission}
  onDenyPermission={denyPermission}
  runningJob={jobRunning}        {/* ← add this */}
/>
```

- [ ] **Step 8: Type-check**

```powershell
cd apps/mission-control && npx tsc --noEmit
```

Expected: zero errors

- [ ] **Step 9: Commit**

```powershell
git add apps/mission-control/src/main.tsx
git commit -m "feat: wire TimelinePanel into AgentPane with jobRunning memo"
```

---

### Task 8: Smoke test + final verification

**Files:** none (verification only)

- [ ] **Step 1: Run all Python tests**

```powershell
$env:PYTHONPATH = "src"
python -m pytest tests/test_live_timeline.py -v
```

Expected: 9/9 PASSED

- [ ] **Step 2: Run full test suite for regressions**

```powershell
$env:PYTHONPATH = "src"
python -m pytest tests/ -q
```

Expected: no new failures vs. pre-sprint baseline

- [ ] **Step 3: TypeScript check**

```powershell
cd apps/mission-control && npx tsc --noEmit
```

Expected: zero errors

- [ ] **Step 4: Smoke test via curl (backend must be running)**

If the backend is running (`start-mvp-local.ps1`), start a handoff and verify the timeline endpoint:

```powershell
# Start a handoff (replace JOB_ID with actual id from response)
$body = '{"objective":"add a comment to src/nemo_coding_platform/core/event_emitter.py","provider":"subprocess","autonomy_mode":"aggressive"}'
$resp = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8787/api/handoff/start" -Body $body -ContentType "application/json"
$jobId = $resp.job.job_id

# Poll timeline
Start-Sleep -Seconds 3
Invoke-RestMethod -Uri "http://127.0.0.1:8787/api/run/$jobId/timeline"
```

Expected: `{"job_id": "...", "status": "running", "timeline": [...], "event_count": N}`

- [ ] **Step 5: Visual check in Mission Control**

1. Open `http://127.0.0.1:5173` in browser
2. Start a handoff from the HandoffComposer
3. Observe `TimelinePanel` appears in AgentPane with `● Live` indicator
4. Events appear in real time as the run progresses
5. On completion, `● Live` disappears and timeline is frozen

---

## Self-Review

**Spec coverage:**
- ✅ `NEMO_EVENT_PREFIX` constant → Task 2
- ✅ Event schema (kind/summary/phase/sequence/ts/payload) → Task 1 (`emit_event`)
- ✅ `HandoffJobStatus` enum → Task 2
- ✅ `HandoffJob.timeline` field → Task 2
- ✅ NEMO_EVENT parsing in `_run_job` → Task 3
- ✅ `api_run_timeline` REST endpoint → Task 3
- ✅ `_handle_timeline_sse` SSE stream → Task 4
- ✅ `/timeline` REST route in `do_GET` → Task 3
- ✅ `/timeline/stream` SSE route in `do_GET` → Task 4 (note: `/timeline/stream` checked **before** `/timeline`)
- ✅ `event_emitter.py` with `emit_event` + `reset_sequence` → Task 1
- ✅ Emission points in `headless_runner.py` → Task 5
- ✅ Emission points in `long_handoff_supervisor.py` → Task 5
- ✅ `TimelinePanel.tsx` with SSE + REST logic → Task 6
- ✅ CSS → Task 6
- ✅ `HandoffJob` TypeScript type extended → Task 7
- ✅ `jobRunning` memo → Task 7
- ✅ `AgentPaneProps` + `AgentPane` → Task 7
- ✅ 9 tests in `test_live_timeline.py` → Tasks 1–3

**Placeholder scan:** No TBDs found. All code blocks complete.

**Type consistency:**
- `emit_event(kind: str, summary: str, phase: str, payload: dict | None)` defined in Task 1, called consistently in Task 5.
- `api_run_timeline(config, job_id, jobs)` defined in Task 3, routed in Task 3.
- `TimelinePanel({ jobId, isLive, onStreamEnd })` defined in Task 6, consumed in Task 7 with `jobId` and `isLive` — consistent.
- `HandoffJob.timeline: list[dict[str, object]]` (Python) maps to `timeline?: Array<{kind, summary, phase, sequence, ts, payload?}>` (TypeScript) — consistent.
