# Unified Dispatcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-execute LLM tool decisions (plan_generate, handoff, browser_task) in the frontend without requiring the user to click an action button.

**Architecture:** A `useEffect` in the root React component watches `agentMessages` for newly arrived assistant messages. If the last message contains any auto-dispatchable action, it calls `runAgentAction` immediately. A `useRef<Set<string>>` dedup guard prevents double-execution across re-renders. The guard is cleared when the user starts a new chat.

**Tech Stack:** React 18, TypeScript, `useEffect`, `useRef` — all already imported in `main.tsx`.

---

## File Structure

- **Modify:** `apps/mission-control/src/main.tsx`
  - Add `AUTO_DISPATCH_KINDS` constant (module-level, near other constants)
  - Add `autoDispatchedRef` useRef (near `agentRequestControllerRef` at line ~951)
  - Add `useEffect` hook watching `agentMessages` (near other useEffects at line ~979)
  - Add `autoDispatchedRef.current.clear()` in `startNewChat` (line ~2407)

---

### Task 1: Add AUTO_DISPATCH_KINDS constant and autoDispatchedRef

**Files:**
- Modify: `apps/mission-control/src/main.tsx`

Context: `useRef` and `useEffect` are already imported on line 1. The existing refs are at lines 951–952. We add our constant near the top of the component and our ref next to the others.

- [ ] **Step 1: Add AUTO_DISPATCH_KINDS constant**

Find the block of existing `useRef` declarations around line 951 in `main.tsx`:
```typescript
const agentRequestControllerRef = useRef<AbortController | null>(null);
const queuedAgentPromptsRef = useRef<string[]>([]);
```

Insert the constant and the new ref immediately after `queuedAgentPromptsRef`:
```typescript
const agentRequestControllerRef = useRef<AbortController | null>(null);
const queuedAgentPromptsRef = useRef<string[]>([]);
const autoDispatchedRef = useRef<Set<string>>(new Set());
```

And add the constant at **module level** (outside the component function), near the other module-level constants. A good place is just before the component function definition (search for `function MissionControlApp` or the main export). Add:
```typescript
const AUTO_DISPATCH_KINDS = new Set<AgentAction["kind"]>([
  "plan_generate",
  "run",
  "handoff",
  "browser_task",
]);
```

- [ ] **Step 2: Verify TypeScript compiles**

```powershell
cd apps/mission-control
npx tsc --noEmit
```

Expected: no errors. If you see `Type '"plan_generate"' is not assignable`, check that `AgentAction["kind"]` union includes those values — it does per line 201 of `main.tsx`.

- [ ] **Step 3: Commit**

```powershell
git add apps/mission-control/src/main.tsx
git commit -m "feat: add AUTO_DISPATCH_KINDS constant and autoDispatchedRef"
```

---

### Task 2: Add the auto-dispatch useEffect

**Files:**
- Modify: `apps/mission-control/src/main.tsx`

Context: `runAgentAction` is defined at line 1666. The useEffect hooks that react to state are at lines 979–1052. We add our hook in that same cluster, after the existing useEffects, so it runs after `agentMessages` is updated.

- [ ] **Step 1: Add the useEffect hook**

Find the cluster of `useEffect` hooks around line 979. After the last useEffect in that cluster (around line 1052), insert:

```typescript
// Auto-dispatch: execute tool-type actions without requiring user button click.
useEffect(() => {
  const last = agentMessages[agentMessages.length - 1];
  if (!last || last.role !== "assistant") return;
  for (const action of last.actions ?? []) {
    if (AUTO_DISPATCH_KINDS.has(action.kind) && !autoDispatchedRef.current.has(action.id)) {
      autoDispatchedRef.current.add(action.id);
      runAgentAction(action);
    }
  }
}, [agentMessages]);
```

- [ ] **Step 2: Verify TypeScript compiles**

```powershell
cd apps/mission-control
npx tsc --noEmit
```

Expected: no errors. If you see `Argument of type 'string' is not assignable to parameter of type ...` on `action.kind`, cast: `AUTO_DISPATCH_KINDS.has(action.kind as AgentAction["kind"])`.

- [ ] **Step 3: Commit**

```powershell
git add apps/mission-control/src/main.tsx
git commit -m "feat: auto-dispatch LLM tool actions without user click"
```

---

### Task 3: Clear dedup ref on new chat

**Files:**
- Modify: `apps/mission-control/src/main.tsx`

Context: `startNewChat` is at line 2405. It resets `agentMessages`, draft, and queue. We add the ref clear so stale action IDs don't accumulate across chat sessions.

- [ ] **Step 1: Add clear call in startNewChat**

Find `startNewChat` (~line 2405):
```typescript
const startNewChat = () => {
  if (confirm("¿Iniciar un nuevo chat? El historial actual se cerrará sin borrar los archivos del proyecto.")) {
    setAgentMessages([]);
    setAgentDraft("");
    setHomeAgentDraft("");
    setQueuedAgentPrompts([]);
    setStatus("Nuevo chat iniciado ✓");
  }
};
```

Add the clear call right after `setAgentMessages([])`:
```typescript
const startNewChat = () => {
  if (confirm("¿Iniciar un nuevo chat? El historial actual se cerrará sin borrar los archivos del proyecto.")) {
    setAgentMessages([]);
    autoDispatchedRef.current.clear();
    setAgentDraft("");
    setHomeAgentDraft("");
    setQueuedAgentPrompts([]);
    setStatus("Nuevo chat iniciado ✓");
  }
};
```

- [ ] **Step 2: Verify TypeScript compiles clean**

```powershell
cd apps/mission-control
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Commit**

```powershell
git add apps/mission-control/src/main.tsx
git commit -m "feat: clear autoDispatchedRef on new chat"
```

---

### Task 4: Manual end-to-end verification

**Files:** none (verification only)

- [ ] **Step 1: Start the dev server**

```powershell
cd apps/mission-control
npm run dev
```

Open `http://127.0.0.1:5173` in Chrome.

- [ ] **Step 2: Verify plan_generate auto-dispatches**

In the chat, type:
```
genera un script Python que imprima los primeros 10 números de Fibonacci
```

Expected behavior:
1. Message sends, LLM responds with a `plan_generate` tool call embedded or via native function calling
2. The action button appears in the chat **and immediately starts executing** (SSE progress events appear, no click needed)
3. Iteration events stream in automatically

- [ ] **Step 3: Verify handoff auto-dispatches**

In a new chat, type:
```
añade una función hello_world() a src/nemo_coding_platform/core/repo_map.py que imprima 'hello'
```

Expected behavior:
1. LLM responds with a `handoff_start` tool call
2. The handoff job starts automatically — visible in the Runs panel

- [ ] **Step 4: Verify dedup guard (no double-execution)**

After step 2 completes, trigger a React re-render (e.g., switch tabs and come back). Verify the plan_generate does NOT re-execute. The dedup ref holds the action ID and blocks the second call.

- [ ] **Step 5: Verify new chat clears the ref**

After step 4, click "Nuevo chat" → confirm. Send the same Fibonacci request again. Verify it auto-dispatches normally (ref was cleared, so the same action ID from a prior session is no longer blocked — new message will have a new action ID anyway since IDs are generated fresh).

- [ ] **Step 6: Final commit (if any cleanup needed)**

```powershell
git add apps/mission-control/src/main.tsx
git commit -m "fix: unified dispatcher cleanup after verification"
```

Only needed if verification revealed any issues requiring changes.
