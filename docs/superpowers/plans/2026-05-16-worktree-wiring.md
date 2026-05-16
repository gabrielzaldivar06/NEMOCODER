# Worktree Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Activate git worktree isolation for every handoff started from Mission Control by adding `use_git_worktree: true` to the three `/api/handoff/start` payloads and adding a visual indicator on the Review tab when completed jobs await diff review.

**Architecture:** The entire backend stack already exists — `--use-git-worktree` CLI flag, `worktree_runtime.py`, three merge gate API endpoints, and `WorktreeDiffPanel.tsx`. The only missing piece is that the frontend never sends the flag. Two files change: `main.tsx` (3 payload sites + 1 badge) and `styles.css` (1 CSS rule).

**Tech Stack:** React 18 + TypeScript, Vite dev server on port 5173. Backend Python on port 8787. Run `npx tsc --noEmit` inside `apps/mission-control/` to verify TypeScript.

---

## File Map

| File | Change |
|---|---|
| `apps/mission-control/src/main.tsx` | Add `use_git_worktree: true` at 3 payload sites; add badge span to Review tab button |
| `apps/mission-control/src/styles.css` | Add `.tab-worktree-dot` rule |

---

### Task 1: Add `use_git_worktree: true` to `startHandoff`

This is the primary handoff path triggered by the HandoffComposer submit button.

**Files:**
- Modify: `apps/mission-control/src/main.tsx` (around `startHandoff` function, search for `selected_nemo_tools: selectedNemoTools,` inside `startHandoff`)

- [ ] **Step 1: Locate the exact site**

  Open `apps/mission-control/src/main.tsx`. Search for the function `startHandoff`. Inside it find the `postJson("/api/handoff/start", {` call. The last field before the closing `})` is:

  ```tsx
        selected_nemo_tools: selectedNemoTools,
      })
        .then((payload) => {
          setActiveJob(payload.job);
          setComposerOpen(false);
  ```

- [ ] **Step 2: Add the flag**

  Replace that section with:

  ```tsx
        selected_nemo_tools: selectedNemoTools,
        use_git_worktree: true,
      })
        .then((payload) => {
          setActiveJob(payload.job);
          setComposerOpen(false);
  ```

- [ ] **Step 3: Verify TypeScript compiles**

  ```powershell
  cd apps/mission-control
  npx tsc --noEmit
  ```

  Expected: no output (clean).

---

### Task 2: Add `use_git_worktree: true` to LLM-triggered handoff

This is the `runAgentAction` path for `action.kind === "run" || action.kind === "handoff"` (triggered when the LLM emits a `handoff_start` tool call and the user clicks the action button in the timeline).

**Files:**
- Modify: `apps/mission-control/src/main.tsx` (inside `runAgentAction`, after `if (action.kind === "run" || action.kind === "handoff")`)

- [ ] **Step 1: Locate the exact site**

  Inside `runAgentAction`, find the `postJson("/api/handoff/start", {` call under `if (action.kind === "run" || action.kind === "handoff")`. The last two fields before closing are:

  ```tsx
        require_nemo_mcp_capabilities: true,
        require_nemo_roundtrip: false,
      })
        .then((payload) => {
  ```

- [ ] **Step 2: Add the flag**

  ```tsx
        require_nemo_mcp_capabilities: true,
        require_nemo_roundtrip: false,
        use_git_worktree: true,
      })
        .then((payload) => {
  ```

- [ ] **Step 3: Verify TypeScript compiles**

  ```powershell
  cd apps/mission-control
  npx tsc --noEmit
  ```

  Expected: no output (clean).

---

### Task 3: Add `use_git_worktree: true` to fallthrough handoff path

This is the final `postJson("/api/handoff/start", ...)` call at the bottom of `runAgentAction`, reached when the action kind is not `run`, `handoff`, `plan_generate`, etc. — it handles generic agent actions that map to a handoff.

**Files:**
- Modify: `apps/mission-control/src/main.tsx` (last `postJson("/api/handoff/start", {` in `runAgentAction`)

- [ ] **Step 1: Locate the exact site**

  Find the line `setStatus(\`Starting agent action: ${action.label}\`);` in `runAgentAction`. Immediately after it is:

  ```tsx
      postJson<HandoffJobResult>("/api/handoff/start", {
        ...action.payload,
        validation_commands: handoffValidationCommand(action.payload.validation_commands),
        nemo_mcp_url: String(action.payload.nemo_mcp_url || settingsDraft.nemo_mcp_url || initialState.settings.nemo_mcp_url),
        nemo_mcp_prefix: String(action.payload.nemo_mcp_prefix || "nemo."),
        selected_nemo_tools: selectedNemoTools,
      })
        .then((payload) => {
          setActiveJob(payload.job);
          setActiveSection("runs");
  ```

- [ ] **Step 2: Add the flag**

  ```tsx
      postJson<HandoffJobResult>("/api/handoff/start", {
        ...action.payload,
        validation_commands: handoffValidationCommand(action.payload.validation_commands),
        nemo_mcp_url: String(action.payload.nemo_mcp_url || settingsDraft.nemo_mcp_url || initialState.settings.nemo_mcp_url),
        nemo_mcp_prefix: String(action.payload.nemo_mcp_prefix || "nemo."),
        selected_nemo_tools: selectedNemoTools,
        use_git_worktree: true,
      })
        .then((payload) => {
          setActiveJob(payload.job);
          setActiveSection("runs");
  ```

- [ ] **Step 3: Verify TypeScript compiles**

  ```powershell
  cd apps/mission-control
  npx tsc --noEmit
  ```

  Expected: no output (clean).

- [ ] **Step 4: Commit tasks 1–3**

  ```powershell
  git add apps/mission-control/src/main.tsx
  git commit -m "feat: always activate git worktree isolation for all handoffs"
  ```

---

### Task 4: Add "worktree pending" indicator to the Review tab

When one or more HandoffJobs have `status === "completed"`, show a small amber dot on the Review tab button to signal that there is a worktree ready for diff review. `state.jobs` is `HandoffJob[]` — the array of all known jobs from the `/api/jobs` polling loop.

**Context:** `state.jobs.some(j => j.status === "completed")` is true when any job is done. The WorktreeDiffPanel inside the Review → Diff subtab will show the actual diff when `worktree_exists: true` (confirmed via `/api/run/<job_id>/worktree-diff`).

**Files:**
- Modify: `apps/mission-control/src/main.tsx` (Review tab button in the `activeSection === "runs"` block)
- Modify: `apps/mission-control/src/styles.css` (add `.tab-worktree-dot` rule)

- [ ] **Step 1: Find the Review tab button**

  In `main.tsx`, search for the string `runsWorkbenchTab === "review"`. Find the button that renders the Review tab:

  ```tsx
  <button type="button" className={`tab ${runsWorkbenchTab === "review" ? "active" : ""}`} onClick={() => setRunsWorkbenchTab("review")}>
    <GitCompare size={14} /> Review
  </button>
  ```

- [ ] **Step 2: Add the dot badge**

  ```tsx
  <button type="button" className={`tab ${runsWorkbenchTab === "review" ? "active" : ""}`} onClick={() => setRunsWorkbenchTab("review")}>
    <GitCompare size={14} /> Review
    {state.jobs.some((j) => j.status === "completed") && (
      <span className="tab-worktree-dot" title="Worktree listo para review" />
    )}
  </button>
  ```

- [ ] **Step 3: Add CSS rule**

  In `apps/mission-control/src/styles.css`, find the `.tab.active` rule (search for `.tab.active`). Add after it:

  ```css
  .tab-worktree-dot {
    display: inline-block;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: #e3b341;
    flex-shrink: 0;
  }
  ```

- [ ] **Step 4: Verify TypeScript compiles**

  ```powershell
  cd apps/mission-control
  npx tsc --noEmit
  ```

  Expected: no output (clean).

- [ ] **Step 5: Commit**

  ```powershell
  git add apps/mission-control/src/main.tsx apps/mission-control/src/styles.css
  git commit -m "feat: amber dot on Review tab when worktree awaits review"
  ```

---

### Task 5: End-to-end smoke test

Verify the full flow works with the system running.

**Prerequisites:** backend running on port 8787 (`start-mvp-local.ps1`), frontend on port 5173 (`npm run dev` in `apps/mission-control/`). Use `provider=fake_smoke` to avoid needing LM Studio.

- [ ] **Step 1: Start a handoff from the UI**

  Open Mission Control at `http://127.0.0.1:5173`. Click "New Full Handoff" (Hand icon in CommandDock). Enter any objective (e.g. "add a hello world function"). Submit.

- [ ] **Step 2: Verify the worktree is created**

  While the job runs, check in PowerShell:

  ```powershell
  ls c:\dev\dev4\.worktrees\
  ```

  Expected: a directory like `.worktrees\mc-run-<timestamp>-<id>\` appears.

  Also verify the branch exists:

  ```powershell
  git -C c:\dev\dev4 branch | Select-String "wt/"
  ```

  Expected: `wt/mc-run-<timestamp>-<id>` listed.

- [ ] **Step 3: Review the diff**

  After the job completes (status shown in TelemetryColumn), note the amber dot on the Review tab. Click the Review tab → Diff / Merge subtab. The `WorktreeDiffPanel` should show:
  - The branch name (e.g. `wt/mc-run-...`)
  - A diff (may be empty for fake smoke, but `worktree_exists: true`)

  If diff is empty (fake smoke creates no files), that is expected — the panel shows "No changes in this worktree." The key verification is that `worktree_exists` is `true` (no "No active worktree for this run" error).

- [ ] **Step 4: Test Approve & Merge**

  If the diff is non-empty, click "Approve & Merge to main". Verify:

  ```powershell
  git -C c:\dev\dev4 branch | Select-String "wt/"
  ```

  Expected: the branch is gone after merge.

  ```powershell
  ls c:\dev\dev4\.worktrees\
  ```

  Expected: the worktree directory is gone after merge.

- [ ] **Step 5: Test Reject & Remove**

  Start another handoff. After it completes, click "Reject & Remove". Verify branch and worktree directory are removed (same commands as Step 4).

---

## Self-Review Notes

- Task 3 Site 3 uses `...action.payload` spread — if `action.payload` already includes a `use_git_worktree` key set to `false`, the explicit `use_git_worktree: true` after it will override correctly (last key wins in spread + explicit assignment).
- The badge uses `state.jobs.some(j => j.status === "completed")` — this will show the dot even after the user has already reviewed the job. Clearing the dot after review (e.g. after merge/reject) is Enfoque B scope.
- No backend changes needed. No Python test changes needed.
