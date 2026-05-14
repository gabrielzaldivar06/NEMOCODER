# GAP-04 Live Timeline Wiring — Design Spec

**Date:** 2026-05-14  
**Sprint:** GAP-04 completion  
**Status:** Approved for implementation

---

## Problem

The Live Timeline infrastructure exists end-to-end (SSE endpoint, `TimelinePanel.tsx`, `HandoffJob.timeline`, `NEMO_EVENT` protocol) but two wiring gaps remain:

1. **Review tab** only shows `WorktreeDiffPanel`. Completed and active runs have no way to view their timeline from the UI.
2. **`long_handoff_supervisor.py`** emits only 2 hardcoded heartbeats at fixed points. The iteration loop has no per-iteration events, no paused/resumed signals, and no elapsed time tracking in the event stream.

Additionally: `_persist_job()` is called after every single timeline event, causing unnecessary IO on fast event streams.

---

## Goal

1. Add a **segmented control sub-tab** inside the Review tab with "Timeline" and "Diff / Merge" sub-tabs — live SSE for active runs, REST snapshot for completed runs.
2. **Instrument the supervisor loop** with per-iteration heartbeats and paused/resumed events.
3. **Batch timeline persistence** to reduce IO thrashing.

---

## Scope

**In scope:**
- `main.tsx`: segmented control sub-tabs in Review tab, TimelinePanel wired for both live and completed runs
- `styles.css`: sub-tab CSS, flash-slide-in event animation, LIVE badge
- `long_handoff_supervisor.py`: per-iteration heartbeat, paused, resumed events
- `mission_control_server.py`: batch persistence (every 5 events or on terminal status)
- 2 new tests in `test_live_timeline.py`

**Out of scope:**
- Changes to the SSE endpoint or REST `api_run_timeline` (already complete)
- `TimelinePanel.tsx` component internals (already complete)
- Replay of historical runs from disk
- Mid-run permission event signaling

---

## Architecture

```
Review tab (main.tsx)
  │
  ├── Segmented control: [ ⚡ Timeline  LIVE ][ ⎇ Diff / Merge ]
  │
  ├── Sub-tab: Timeline
  │     ├── run.status == "running"  → TimelinePanel(jobId, isLive=true)  [SSE stream]
  │     └── run.status == completed  → TimelinePanel(jobId, isLive=false) [REST fetch on mount]
  │
  └── Sub-tab: Diff / Merge
        └── WorktreeDiffPanel(jobId)  [unchanged]

long_handoff_supervisor.py
  └── iteration loop
        ├── start of each iteration → emit_event("heartbeat", ..., payload={iteration, elapsed_minutes})
        ├── before sleep/pause      → emit_event("paused", ..., payload={pause_minutes, elapsed_minutes})
        └── after sleep/resume      → emit_event("resumed", ..., payload={elapsed_minutes})

mission_control_server.py
  └── _run_job() timeline append
        └── persist only on terminal status OR every 5 events (not every event)
```

---

## Frontend Design

### Sub-tab layout (`main.tsx`)

In the Review tab section of `RunsWorkbench`, replace the current direct render of `WorktreeDiffPanel` with:

```tsx
// State: which sub-tab is active
const [reviewSubTab, setReviewSubTab] = useState<"timeline" | "diff">(
  selectedJob?.status === "running" ? "timeline" : "diff"
);

// Reset to correct default when selected job changes
useEffect(() => {
  setReviewSubTab(selectedJob?.status === "running" ? "timeline" : "diff");
}, [selectedJob?.job_id]);
```

Segmented control markup:
```tsx
<div className="review-seg-tabs">
  <button
    className={`review-seg-tab ${reviewSubTab === "timeline" ? "active" : ""}`}
    onClick={() => setReviewSubTab("timeline")}
  >
    ⚡ Timeline
    {selectedJob?.status === "running" && (
      <span className="review-live-badge">LIVE</span>
    )}
  </button>
  <button
    className={`review-seg-tab ${reviewSubTab === "diff" ? "active" : ""}`}
    onClick={() => setReviewSubTab("diff")}
  >
    ⎇ Diff / Merge
  </button>
</div>

<div className={`review-subtab-content ${reviewSubTab}`}>
  {reviewSubTab === "timeline" && selectedJob && (
    <TimelinePanel
      jobId={selectedJob.job_id}
      isLive={selectedJob.status === "running"}
    />
  )}
  {reviewSubTab === "diff" && selectedJob && (
    <WorktreeDiffPanel jobId={selectedJob.job_id} />
  )}
</div>
```

Sub-tab transition: `review-subtab-content` uses `opacity + transform` CSS transition on tab switch (200ms ease).

### New event animation (`styles.css`)

When `TimelinePanel` appends a new event DOM node (already uses `.timeline-event` class), add `.timeline-event-new` on creation and remove after animation completes:

```css
@keyframes tl-flash-slide-in {
  0%   { opacity: 0; transform: translateX(-8px); background: #1a3a5c; }
  40%  { opacity: 1; transform: translateX(0);    background: #1a3a5c; }
  100% { background: #161b22; }
}

.timeline-event-new {
  animation: tl-flash-slide-in 0.5s ease forwards;
}
```

### Sub-tab CSS (`styles.css`)

```css
.review-seg-tabs {
  display: flex;
  border: 1px solid #30363d;
  border-radius: 6px;
  overflow: hidden;
  width: fit-content;
  margin-bottom: 12px;
}

.review-seg-tab {
  padding: 5px 16px;
  font-size: 11px;
  color: #8b949e;
  font-family: monospace;
  background: none;
  border: none;
  border-right: 1px solid #30363d;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}

.review-seg-tab:last-child { border-right: none; }

.review-seg-tab.active {
  background: #21262d;
  color: #e6edf3;
  font-weight: 600;
}

.review-live-badge {
  font-size: 9px;
  background: #3fb950;
  color: #000;
  border-radius: 3px;
  padding: 1px 4px;
  margin-left: 5px;
  font-weight: 700;
  letter-spacing: 0.5px;
}

.review-subtab-content {
  transition: opacity 0.2s ease, transform 0.2s ease;
}
```

---

## Backend: Supervisor Instrumentation

### `long_handoff_supervisor.py`

Remove the 2 existing hardcoded heartbeat calls. In their place, add dynamic instrumentation at the three points:

**Import** (top of file, alongside existing event_emitter import):
```python
from nemo_coding_platform.core.event_emitter import emit_event
```

**Iteration start** (inside the main while/for loop, before each handoff execution):
```python
elapsed = (time.monotonic() - run_start) / 60
emit_event(
    "heartbeat",
    f"Iteration {iteration_n} — {elapsed:.1f} min elapsed",
    "execute",
    payload={"iteration": iteration_n, "elapsed_minutes": round(elapsed, 1)},
)
```

**Before pause/sleep:**
```python
elapsed = (time.monotonic() - run_start) / 60
emit_event(
    "paused",
    f"Paused — resuming in {pause_minutes} min",
    "execute",
    payload={"pause_minutes": pause_minutes, "elapsed_minutes": round(elapsed, 1)},
)
time.sleep(pause_minutes * 60)
```

**After resume:**
```python
elapsed = (time.monotonic() - run_start) / 60
emit_event(
    "resumed",
    f"Resumed — {elapsed:.1f} min elapsed",
    "execute",
    payload={"elapsed_minutes": round(elapsed, 1)},
)
```

`run_start = time.monotonic()` is set once at the start of `execute_long_handoff_supervisor`.

---

## Backend: Persistence Optimization

### `mission_control_server.py` — `_run_job()`

Replace the per-event `_persist_job()` call with a counter-based batch:

```python
_TIMELINE_PERSIST_BATCH = 5

# Inside the stdout-reading loop, after appending to job.timeline:
job._timeline_event_count = getattr(job, "_timeline_event_count", 0) + 1
if job._timeline_event_count % _TIMELINE_PERSIST_BATCH == 0:
    _persist_job(config, job)
```

Always persist on terminal status (existing behavior for status changes is unchanged — `_persist_job` is already called when status transitions to `completed`/`failed`).

`_timeline_event_count` is a transient attribute (not serialized) — reset to 0 on each job load from snapshot.

---

## Tests

### `tests/test_live_timeline.py` — 2 new tests

**`test_supervisor_heartbeat_emitted_per_iteration`**

```python
def test_supervisor_heartbeat_emitted_per_iteration(capsys):
    from nemo_coding_platform.core.event_emitter import reset_sequence
    import json
    reset_sequence()
    # Simulate what the supervisor loop does at iteration start
    from nemo_coding_platform.core.event_emitter import emit_event
    emit_event("heartbeat", "Iteration 1 — 0.0 min elapsed", "execute",
               payload={"iteration": 1, "elapsed_minutes": 0.0})
    emit_event("heartbeat", "Iteration 2 — 5.0 min elapsed", "execute",
               payload={"iteration": 2, "elapsed_minutes": 5.0})
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2
    e1 = json.loads(lines[0][len("NEMO_EVENT:"):])
    e2 = json.loads(lines[1][len("NEMO_EVENT:"):])
    assert e1["kind"] == "heartbeat"
    assert e1["payload"]["iteration"] == 1
    assert e2["payload"]["iteration"] == 2
    assert e2["payload"]["elapsed_minutes"] == 5.0
```

**`test_supervisor_paused_resumed_events`**

```python
def test_supervisor_paused_resumed_events(capsys):
    from nemo_coding_platform.core.event_emitter import emit_event, reset_sequence
    import json
    reset_sequence()
    emit_event("paused", "Paused — resuming in 10 min", "execute",
               payload={"pause_minutes": 10, "elapsed_minutes": 20.0})
    emit_event("resumed", "Resumed — 30.0 min elapsed", "execute",
               payload={"elapsed_minutes": 30.0})
    lines = capsys.readouterr().out.strip().splitlines()
    paused  = json.loads(lines[0][len("NEMO_EVENT:"):])
    resumed = json.loads(lines[1][len("NEMO_EVENT:"):])
    assert paused["kind"] == "paused"
    assert paused["payload"]["pause_minutes"] == 10
    assert resumed["kind"] == "resumed"
    assert resumed["payload"]["elapsed_minutes"] == 30.0
    assert resumed["sequence"] > paused["sequence"]
```

---

## File Summary

| File | Change |
|------|--------|
| `apps/mission-control/src/main.tsx` | Add `reviewSubTab` state + segmented control + conditional TimelinePanel/WorktreeDiffPanel render in Review tab |
| `apps/mission-control/src/styles.css` | `.review-seg-tabs`, `.review-seg-tab`, `.review-live-badge`, `.review-subtab-content`, `@keyframes tl-flash-slide-in`, `.timeline-event-new` |
| `src/nemo_coding_platform/core/long_handoff_supervisor.py` | Remove 2 hardcoded heartbeats; add per-iteration heartbeat + paused + resumed events; add `run_start = time.monotonic()` |
| `src/nemo_coding_platform/mission_control_server.py` | `_TIMELINE_PERSIST_BATCH = 5`; batch persist logic in `_run_job()` |
| `tests/test_live_timeline.py` | 2 new tests |

---

## Acceptance Criteria

1. `pytest tests/test_live_timeline.py -v` — all tests pass (existing 9 + 2 new = 11).
2. `npx tsc --noEmit` in `apps/mission-control/` — zero errors.
3. Review tab shows segmented control with "⚡ Timeline" and "⎇ Diff / Merge" sub-tabs.
4. Selecting an active run → Timeline sub-tab auto-selected with LIVE badge.
5. Selecting a completed run → Diff sub-tab auto-selected; switching to Timeline shows its historical events.
6. New events in live mode appear with flash-slide-in animation.
7. A long-handoff fake run emits `heartbeat` events with `iteration` and `elapsed_minutes` in payload.
8. `_persist_job` is not called on every single event (verified by patching and counting calls).
