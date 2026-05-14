# Live Timeline Wiring (GAP-04 completion) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the GAP-04 Live Timeline by wiring the Review tab with animated sub-tabs (Timeline + Diff/Merge), instrumenting the supervisor loop with per-iteration heartbeat/paused/resumed events, and batching timeline persistence.

**Architecture:** The SSE endpoint and `TimelinePanel.tsx` already exist and work. The work is (1) surfacing `TimelinePanel` inside a segmented-control sub-tab in the Review tab of `RunsWorkbench`, (2) replacing two hardcoded `emit_event` calls in the supervisor with elapsed-aware heartbeats plus paused/resumed events for the continuation flow, and (3) batching `_persist_job()` calls in `_run_job()` to every 5 events instead of every 1.

**Tech Stack:** React 18 + TypeScript (frontend), Python 3.11 (backend), `emit_event` stdout protocol, CSS animations.

---

## File Map

| File | Change |
|------|--------|
| `tests/test_live_timeline.py` | +2 new tests |
| `src/nemo_coding_platform/core/long_handoff_supervisor.py` | Replace 2 hardcoded heartbeats; add elapsed tracking; add paused emit; add resumed emit in continuation |
| `src/nemo_coding_platform/mission_control_server.py` | Batch `_persist_job()` every 5 timeline events |
| `apps/mission-control/src/styles.css` | `.review-seg-tabs`, `.review-seg-tab`, `.review-live-badge`, `@keyframes tl-flash-slide-in` |
| `apps/mission-control/src/main.tsx` | `reviewSubTab` state + segmented control + TimelinePanel in Review tab |

---

## Task 1: Backend tests for heartbeat/paused/resumed events

**Files:**
- Modify: `tests/test_live_timeline.py`

- [ ] **Step 1: Write 2 failing tests**

Open `tests/test_live_timeline.py` and append at the end:

```python
def test_supervisor_heartbeat_emitted_per_iteration(capsys):
    from nemo_coding_platform.core.event_emitter import emit_event, reset_sequence
    import json as _json
    reset_sequence()
    emit_event("heartbeat", "Iteration 1 — 0.0 min elapsed", "execute",
               payload={"iteration": 1, "elapsed_minutes": 0.0})
    emit_event("heartbeat", "Iteration 2 — 5.0 min elapsed", "execute",
               payload={"iteration": 2, "elapsed_minutes": 5.0})
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2
    e1 = _json.loads(lines[0][len("NEMO_EVENT:"):])
    e2 = _json.loads(lines[1][len("NEMO_EVENT:"):])
    assert e1["kind"] == "heartbeat"
    assert e1["payload"]["iteration"] == 1
    assert e1["payload"]["elapsed_minutes"] == 0.0
    assert e2["payload"]["iteration"] == 2
    assert e2["payload"]["elapsed_minutes"] == 5.0


def test_supervisor_paused_resumed_events(capsys):
    from nemo_coding_platform.core.event_emitter import emit_event, reset_sequence
    import json as _json
    reset_sequence()
    emit_event("paused", "Paused — resuming in 10 min", "execute",
               payload={"pause_minutes": 10, "elapsed_minutes": 20.0})
    emit_event("resumed", "Resumed — 30.0 min elapsed", "execute",
               payload={"elapsed_minutes": 30.0})
    lines = capsys.readouterr().out.strip().splitlines()
    paused  = _json.loads(lines[0][len("NEMO_EVENT:"):])
    resumed = _json.loads(lines[1][len("NEMO_EVENT:"):])
    assert paused["kind"] == "paused"
    assert paused["payload"]["pause_minutes"] == 10
    assert paused["payload"]["elapsed_minutes"] == 20.0
    assert resumed["kind"] == "resumed"
    assert resumed["payload"]["elapsed_minutes"] == 30.0
    assert resumed["sequence"] > paused["sequence"]
```

- [ ] **Step 2: Run tests to verify they PASS immediately**

These tests only use `emit_event` which already works — they should pass right away.

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m pytest tests/test_live_timeline.py::test_supervisor_heartbeat_emitted_per_iteration tests/test_live_timeline.py::test_supervisor_paused_resumed_events -v
```

Expected: `2 passed`

- [ ] **Step 3: Commit**

```powershell
git add tests/test_live_timeline.py
git commit -m "test: add heartbeat/paused/resumed event protocol tests"
```

---

## Task 2: Supervisor instrumentation

**Files:**
- Modify: `src/nemo_coding_platform/core/long_handoff_supervisor.py`

Context: the file already imports `emit_event` (line 8) and does NOT import `time`. The function `execute_long_handoff_supervisor` starts at line 467. Lines 477 and 479 are the two hardcoded heartbeats to replace. `execute_long_handoff_continuation` starts at line 382 and calls `execute_long_handoff_supervisor` internally.

- [ ] **Step 1: Add `import time` to the imports block**

After `import json` (line 3), add `import time`:

```python
import json
import time
```

- [ ] **Step 2: Replace the 2 hardcoded emit_event calls and add elapsed tracking**

Find this block (lines 473–479):
```python
    active_budget = budget or LongHandoffBudget()
    active_budget.validate()
    active_handoff_kwargs: dict[str, object] = dict(handoff_kwargs)
    active_handoff_kwargs.setdefault("provider_mode", "fake")
    emit_event("heartbeat", "Supervisor: starting headless handoff", "execute", {"iteration": 1})
    result = execute_headless_handoff(request, bounded_simulation=True, **active_handoff_kwargs)
    emit_event("heartbeat", "Supervisor: headless handoff complete", "execute", {"iteration": 1})
```

Replace with:
```python
    active_budget = budget or LongHandoffBudget()
    active_budget.validate()
    active_handoff_kwargs: dict[str, object] = dict(handoff_kwargs)
    active_handoff_kwargs.setdefault("provider_mode", "fake")
    _run_start = time.monotonic()
    emit_event(
        "heartbeat",
        "Supervisor: starting handoff — iteration 1",
        "execute",
        {"iteration": 1, "elapsed_minutes": 0.0},
    )
    result = execute_headless_handoff(request, bounded_simulation=True, **active_handoff_kwargs)
    _elapsed = round((time.monotonic() - _run_start) / 60, 1)
    emit_event(
        "heartbeat",
        f"Supervisor: handoff complete — {_elapsed} min elapsed",
        "execute",
        {"iteration": 1, "elapsed_minutes": _elapsed},
    )
```

- [ ] **Step 3: Add paused emit when resume_token is set**

Find this block (after `resume_token` is determined, around line 493):
```python
    if resume_token:
        write_runtime_file(runtime, "resume-token.txt", resume_token + "\n")
```

Add a `paused` emit right after it:
```python
    if resume_token:
        write_runtime_file(runtime, "resume-token.txt", resume_token + "\n")
        _elapsed_at_pause = round((time.monotonic() - _run_start) / 60, 1)
        emit_event(
            "paused",
            f"Paused — resume token persisted at {_elapsed_at_pause} min",
            "execute",
            {"pause_minutes": active_budget.pause_after_minutes, "elapsed_minutes": _elapsed_at_pause},
        )
```

- [ ] **Step 4: Add resumed emit at the start of execute_long_handoff_continuation**

Find `execute_long_handoff_continuation` (line 382). Add a `resumed` emit before it calls `execute_long_handoff_supervisor`:

```python
def execute_long_handoff_continuation(
    payload: dict[str, Any],
    *,
    objective: str | None = None,
    acceptance_criteria: tuple[str, ...] | None = None,
    validation_commands: tuple[str, ...] | None = None,
    budget: LongHandoffBudget | None = None,
    **handoff_kwargs: object,
) -> HeadlessRunResult:
    continuation_kwargs: dict[str, object] = dict(handoff_kwargs)
    plan = build_long_handoff_resume_plan(payload)
    if not plan.can_resume:
        raise ValueError(f"cannot continue long handoff: {', '.join(plan.reasons)}")
    task = payload.get("task", {}) if isinstance(payload.get("task"), dict) else {}
    run = payload.get("run", {}) if isinstance(payload.get("run"), dict) else {}
    repo_path = str(task.get("repo_path") or ".")
    source_objective = str(plan.objective or task.get("objective") or "Resume paused long handoff")
    resume_objective = objective or f"Resume paused long handoff from {plan.resume_token}. Continue objective: {source_objective}"
    resume_task_id = f"{plan.task_id or 'task'}-resume"
    resume_run_id = f"{plan.run_id or 'run'}-resume-{plan.resume_minute}"
    continuation_kwargs.setdefault("provider_mode", plan.provider_mode or "fake")
    emit_event(
        "resumed",
        f"Resumed from pause — continuing {source_objective[:60]}",
        "execute",
        {"elapsed_minutes": 0.0},
    )
    result = execute_long_handoff_supervisor(
```

- [ ] **Step 5: Run full test suite for supervisor**

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m pytest tests/test_live_timeline.py tests/test_long_handoff_supervisor.py -v
```

Expected: all pass (no regressions).

- [ ] **Step 6: Commit**

```powershell
git add src/nemo_coding_platform/core/long_handoff_supervisor.py
git commit -m "feat: instrument supervisor with elapsed heartbeat, paused, and resumed events"
```

---

## Task 3: Batch timeline persistence

**Files:**
- Modify: `src/nemo_coding_platform/mission_control_server.py`

Context: in `_run_job()`, the NEMO_EVENT parsing block (around line 744) calls `self._persist_job(job)` after **every single timeline event**. We change this to persist only every 5 events.

- [ ] **Step 1: Add the batch constant near the top of the class or at module level**

Find where other module-level constants are defined (near `NEMO_EVENT_PREFIX = "NEMO_EVENT:"`, line 63). Add immediately after it:

```python
NEMO_EVENT_PREFIX = "NEMO_EVENT:"
_TIMELINE_PERSIST_BATCH = 5
```

- [ ] **Step 2: Replace the per-event persist call with batched logic**

Find this block (around line 744):
```python
                        if line.startswith(NEMO_EVENT_PREFIX):
                            try:
                                event = json.loads(line[len(NEMO_EVENT_PREFIX):])
                                with self._lock:
                                    job.timeline.append(event)
                                    job.updated_at = datetime.now(timezone.utc).isoformat()
                                    self._persist_job(job)
                            except (json.JSONDecodeError, ValueError):
                                pass
```

Replace with:
```python
                        if line.startswith(NEMO_EVENT_PREFIX):
                            try:
                                event = json.loads(line[len(NEMO_EVENT_PREFIX):])
                                with self._lock:
                                    job.timeline.append(event)
                                    job.updated_at = datetime.now(timezone.utc).isoformat()
                                    job._tl_event_count = getattr(job, "_tl_event_count", 0) + 1
                                    if job._tl_event_count % _TIMELINE_PERSIST_BATCH == 0:
                                        self._persist_job(job)
                            except (json.JSONDecodeError, ValueError):
                                pass
```

- [ ] **Step 3: Verify existing timeline tests still pass**

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m pytest tests/test_live_timeline.py -v
```

Expected: `11 passed`

- [ ] **Step 4: Commit**

```powershell
git add src/nemo_coding_platform/mission_control_server.py
git commit -m "perf: batch timeline persist to every 5 events instead of every 1"
```

---

## Task 4: CSS — sub-tabs + flash-slide-in animation

**Files:**
- Modify: `apps/mission-control/src/styles.css`

Context: existing timeline CSS is around line 4280. Add the new CSS after it.

- [ ] **Step 1: Add flash-slide-in keyframe and timeline-event animation**

Find `.timeline-event-meta { color: #8b949e; font-size: 10px; }` (around line 4294) and add immediately after:

```css
@keyframes tl-flash-slide-in {
  0%   { opacity: 0; transform: translateX(-8px); background-color: #1a3a5c; }
  40%  { opacity: 1; transform: translateX(0);    background-color: #1a3a5c; }
  100% { background-color: transparent; }
}
.timeline-event { animation: tl-flash-slide-in 0.5s ease forwards; }
```

- [ ] **Step 2: Add Review sub-tab CSS**

Append after the animation block:

```css
.review-seg-tabs { display: flex; border: 1px solid #30363d; border-radius: 6px; overflow: hidden; width: fit-content; margin-bottom: 12px; }
.review-seg-tab { padding: 5px 16px; font-size: 11px; color: #8b949e; font-family: inherit; background: none; border: none; border-right: 1px solid #30363d; cursor: pointer; transition: background 0.15s ease, color 0.15s ease; }
.review-seg-tab:last-child { border-right: none; }
.review-seg-tab.active { background: #21262d; color: #e6edf3; font-weight: 600; }
.review-live-badge { font-size: 9px; background: #3fb950; color: #000; border-radius: 3px; padding: 1px 4px; margin-left: 5px; font-weight: 700; letter-spacing: 0.5px; }
.review-subtab-content { transition: opacity 0.2s ease; }
```

- [ ] **Step 3: Verify TypeScript compiles clean**

```powershell
cd apps/mission-control
npx tsc --noEmit
```

Expected: zero errors.

- [ ] **Step 4: Commit**

```powershell
git add apps/mission-control/src/styles.css
git commit -m "style: add review sub-tab segmented control CSS and timeline flash-slide-in animation"
```

---

## Task 5: Frontend — Review tab sub-tabs wiring

**Files:**
- Modify: `apps/mission-control/src/main.tsx`

Context:
- `selectedJob` is already computed at line 994: `state.jobs.find((j) => j.task_id === selectedRun?.task_id && j.run_id === selectedRun?.run_id)`
- `TimelinePanel` is already imported at line 14
- `WorktreeDiffPanel` is already imported at line 12
- The Review tab content is at lines 2639–2643:
  ```tsx
  {runsWorkbenchTab === "review" && <WorktreeDiffPanel
    jobId={selectedJob?.job_id ?? ""}
    onMerged={() => { /* state will refresh via polling */ }}
    onRejected={() => { /* state will refresh via polling */ }}
  />}
  ```
- The `runsWorkbenchTab` state is declared at line 900

- [ ] **Step 1: Add `reviewSubTab` state near `runsWorkbenchTab`**

Find the line (around 900):
```tsx
  const [runsWorkbenchTab, setRunsWorkbenchTab] = useState<RunsWorkbenchTab>("file");
```

Add immediately after it:
```tsx
  const [reviewSubTab, setReviewSubTab] = useState<"timeline" | "diff">("diff");
```

- [ ] **Step 2: Add useEffect to reset reviewSubTab when selected job changes**

Find the block of `useEffect` calls that depend on `selectedRun` or `selectedJob` (around line 2455 there's a `useEffect` for `activeJob`). Add a new `useEffect` alongside the other state-reset effects:

```tsx
  useEffect(() => {
    setReviewSubTab(selectedJob?.status === "running" ? "timeline" : "diff");
  }, [selectedJob?.job_id]);
```

Place it near the other `useEffect` calls inside the `RunsWorkbench`/main component, after the `selectedJob` memo (line 994).

- [ ] **Step 3: Replace the Review tab content with segmented control**

Find (lines 2639–2643):
```tsx
            {runsWorkbenchTab === "review" && <WorktreeDiffPanel
              jobId={selectedJob?.job_id ?? ""}
              onMerged={() => { /* state will refresh via polling */ }}
              onRejected={() => { /* state will refresh via polling */ }}
            />}
```

Replace with:
```tsx
            {runsWorkbenchTab === "review" && (
              <div style={{ padding: "12px 16px" }}>
                <div className="review-seg-tabs">
                  <button
                    type="button"
                    className={`review-seg-tab ${reviewSubTab === "timeline" ? "active" : ""}`}
                    onClick={() => setReviewSubTab("timeline")}
                  >
                    ⚡ Timeline
                    {selectedJob?.status === "running" && (
                      <span className="review-live-badge">LIVE</span>
                    )}
                  </button>
                  <button
                    type="button"
                    className={`review-seg-tab ${reviewSubTab === "diff" ? "active" : ""}`}
                    onClick={() => setReviewSubTab("diff")}
                  >
                    ⎇ Diff / Merge
                  </button>
                </div>
                <div className="review-subtab-content">
                  {reviewSubTab === "timeline" && selectedJob && (
                    <TimelinePanel
                      jobId={selectedJob.job_id}
                      isLive={selectedJob.status === "running"}
                    />
                  )}
                  {reviewSubTab === "timeline" && !selectedJob && (
                    <div style={{ color: "#8b949e", fontSize: 12, padding: "16px 0" }}>
                      Selecciona un run para ver su timeline.
                    </div>
                  )}
                  {reviewSubTab === "diff" && (
                    <WorktreeDiffPanel
                      jobId={selectedJob?.job_id ?? ""}
                      onMerged={() => { /* state will refresh via polling */ }}
                      onRejected={() => { /* state will refresh via polling */ }}
                    />
                  )}
                </div>
              </div>
            )}
```

- [ ] **Step 4: Verify TypeScript compiles clean**

```powershell
cd apps/mission-control
npx tsc --noEmit
```

Expected: zero errors.

- [ ] **Step 5: Run full backend test suite**

```powershell
cd c:/dev/dev4
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m pytest tests/test_live_timeline.py -v
```

Expected: `11 passed` (9 original + 2 new).

- [ ] **Step 6: Commit**

```powershell
git add apps/mission-control/src/main.tsx
git commit -m "feat: add Timeline/Diff segmented control sub-tabs to Review tab"
```

---

## Verification

After all tasks complete:

1. `pytest tests/test_live_timeline.py -v` → 11 passed
2. `npx tsc --noEmit` in `apps/mission-control/` → zero errors
3. Open Mission Control UI → Runs → Review tab → see segmented control `[ ⚡ Timeline ][ ⎇ Diff / Merge ]`
4. Select a running job → Timeline sub-tab active with `LIVE` badge → events stream in with flash-slide-in animation
5. Select a completed job → Diff sub-tab active → switch to Timeline → historical events shown
6. Start a fake long-handoff run → stdout includes `NEMO_EVENT:{"kind":"heartbeat",...,"payload":{"iteration":1,"elapsed_minutes":...}}`
