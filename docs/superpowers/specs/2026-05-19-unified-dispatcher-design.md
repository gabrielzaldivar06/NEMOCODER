# Unified Dispatcher — Design Spec

**Date:** 2026-05-19

## Goal

Eliminate the button-click step between the LLM routing decision and action execution. When the LLM decides to use `handoff_start`, `plan_generate`, or `browser_task`, the action executes immediately without user intervention.

## Architecture

Change is confined to `apps/mission-control/src/main.tsx`. The backend is unchanged.

**Current flow:**
```
user message → /api/agent/message → response with actions[] → buttons rendered → user clicks → runAgentAction
```

**New flow:**
```
user message → /api/agent/message → response with actions[] → useEffect fires → runAgentAction (automatic)
                                                                ↑ dedup guard (Set of already-dispatched IDs)
```

## Auto-Dispatch Predicate

A `Set` called `AUTO_DISPATCH_KINDS` defines which action kinds execute automatically:

| Kind | Auto-dispatch | Reason |
|------|--------------|--------|
| `plan_generate` | ✅ yes | Runs headless Python, no repo side-effects |
| `run` / `handoff` | ✅ yes | User explicitly asked to implement something |
| `browser_task` | ✅ yes | User explicitly asked to navigate/scrape |
| `apply` | ❌ no | Writes to working tree — requires explicit intent |
| `layout` | ❌ no | UI state change — should be intentional |
| `plan_cancel` | ❌ no | Destructive stop — must be user-initiated |
| `plan_steer` | ❌ no | Directive input — requires user text |
| `workspace_open` | ❌ no | Changes active project — must be intentional |

## Implementation

Two additions to the main component in `main.tsx`:

```typescript
const AUTO_DISPATCH_KINDS = new Set(["plan_generate", "run", "handoff", "browser_task"]);
const autoDispatchedRef = useRef<Set<string>>(new Set());

useEffect(() => {
  const last = messages[messages.length - 1];
  if (!last || last.role !== "assistant") return;
  for (const action of last.actions ?? []) {
    if (AUTO_DISPATCH_KINDS.has(action.kind) && !autoDispatchedRef.current.has(action.id)) {
      autoDispatchedRef.current.add(action.id);
      runAgentAction(action);
    }
  }
}, [messages]);
```

The `autoDispatchedRef` is reset on `onStartNewChat` via `autoDispatchedRef.current.clear()`.

## Observability

Action buttons still render in the chat history unchanged — the user can see which action was triggered and when. The only difference is they no longer need to click. Error handling is unchanged: failures surface as inline error messages via the existing `runAgentAction` error path.

## Files Changed

- `apps/mission-control/src/main.tsx` — add `AUTO_DISPATCH_KINDS` constant, `autoDispatchedRef`, `useEffect` hook, and `.clear()` call in `onStartNewChat`.

## Out of Scope

- Backend changes
- Pre-LLM heuristic routing (Approach C) — deferred
- Merging chat+execution into a single SSE stream (Approach B) — deferred
