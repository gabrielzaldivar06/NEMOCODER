# Worktree Wiring — Mission Control Design

**Date:** 2026-05-16  
**Sprint:** Worktree activation (Enfoque A — mínimo viable)

---

## Goal

Activate the existing git worktree infrastructure for every handoff started from the Mission Control UI. The backend is complete; the frontend never sends `use_git_worktree: true`, so worktrees never get created.

---

## Context

The full worktree stack already exists:

| Layer | What exists |
|---|---|
| CLI | `long-handoff-run --use-git-worktree` → `initialize_git_worktree_runtime` |
| Backend | `worktree_runtime.py`: `create_git_worktree`, `cleanup_git_worktree`, `worktree_diff`, `merge_worktree_to_main` |
| API | `/api/run/<job_id>/worktree-diff`, `/worktree-merge`, `/worktree-cleanup` |
| UI | `WorktreeDiffPanel.tsx` in Review → Diff/Merge subtab |
| Missing | `use_git_worktree: true` in `startHandoff` payload |

---

## Architecture

No backend changes. Three frontend changes in `apps/mission-control/src/main.tsx`:

1. `startHandoff` (~L1374) — HandoffComposer submit
2. `runAgentAction` handoff_start branch (~L1690) — LLM-triggered handoff
3. `runAgentAction` steer/queue branch (~L2200) — secondary handoff paths

Each adds `use_git_worktree: true` to the `postJson("/api/handoff/start", {...})` call.

One additional UI change: a `⎇` badge on completed jobs in the active job list to signal that a worktree is waiting for review. A completed job started after this change can be assumed to have a worktree; the WorktreeDiffPanel confirms via `worktree_exists` from the API.

---

## End-to-End Flow

```
HandoffComposer → startHandoff
  POST /api/handoff/start { ..., use_git_worktree: true }
    → HandoffJobManager.start()
    → CLI: long-handoff-run --use-git-worktree
      → git worktree add .worktrees/<runtime_id>  branch: wt/<runtime_id>
      → Aider commits inside worktree
      → job.status = "completed"

User → Review tab → Diff / Merge subtab
  WorktreeDiffPanel fetches GET /api/run/<job_id>/worktree-diff
    → { diff, branch: "wt/<id>", worktree_exists: true }
  User clicks "Approve & Merge to main"
    → POST /api/run/<job_id>/worktree-merge
    → git merge --no-ff wt/<id> → cleanup worktree + branch
  User clicks "Reject & Remove"
    → POST /api/run/<job_id>/worktree-cleanup
    → worktree + branch deleted
```

---

## Files Changed

| File | Change |
|---|---|
| `apps/mission-control/src/main.tsx` | Add `use_git_worktree: true` at ~L1374, ~L1690, ~L2200; add `⎇` badge for completed jobs |

---

## Out of Scope

- Per-file accept/reject (Enfoque C — future sprint)
- Diff syntax highlighting (Enfoque B — future sprint)
- Auto-notification when worktree ready (Enfoque B)
- Backend changes of any kind

---

## Acceptance Criteria

1. Starting any handoff from the UI causes `git worktree add` to run — visible as `.worktrees/<id>/` in the repo root.
2. After the job completes, Review → Diff/Merge shows a non-empty diff with the branch name.
3. "Approve & Merge to main" merges the branch and removes the worktree directory.
4. "Reject & Remove" removes the worktree directory without merging.
5. TypeScript compiles clean (`npx tsc --noEmit` produces no output).

---

## Future Decisions Recorded

- **Enfoque B** (polish diff): file list before raw diff, `+/-` syntax highlight, auto-notification on worktree ready
- **Enfoque C** (full review flow): per-file accept/reject, cleanup feedback, TelemetryColumn worktree queue
