# Frontend State + AgentPane Refactor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 4 frontend bugs (Runs flicker, artifacts reappear, chat returns after archive, AgentPane stacking) and refactor AgentPane into a tabbed, ergonomic layout with tooltips.

**Architecture:** Centralized persistence module (`persistenceStore.ts`) owns all localStorage operations with proper delete semantics. `statusLoaded` flag distinguishes "loading" from "empty" in Runs navigation. AgentPane gains internal tabs (Chat / Run / Insights) with a fixed header and scrollable body.

**Tech Stack:** React 18 + TypeScript + Vite. No new dependencies. CSS-only tooltips.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `apps/mission-control/src/services/persistenceStore.ts` | **Create** | All localStorage ops: chat session, archives, artifact tombstones |
| `apps/mission-control/src/services/artifactRegistry.ts` | **Modify** | Accept `isTombstoned` option in `mergeArtifactsIntoRegistry` |
| `apps/mission-control/src/hooks/useGeneratedArtifacts.ts` | **Modify** | Pass tombstone fns through; call `addArtifactTombstone` on remove |
| `apps/mission-control/src/main.tsx` | **Modify** | `statusLoaded`, replace inline localStorage, AgentPane JSX refactor, Tooltip component, prop reduction |
| `apps/mission-control/src/styles.css` | **Modify** | AgentPane layout CSS, tooltip CSS, metrics strip, tab bar |

---

## Task 1: BUG-01 — `statusLoaded` guard for Runs navigation

**Files:**
- Modify: `apps/mission-control/src/main.tsx` (lines ~573, ~595, ~1121, ~2529)

- [ ] **Step 1: Add `statusLoaded` to `MissionState` type**

Find the `MissionState` type definition (search for `type MissionState`). Add the field:

```ts
type MissionState = {
  statusLoaded: boolean;   // ← add this line near the top
  // ... rest of existing fields unchanged
};
```

- [ ] **Step 2: Set default in `initialState`**

Find `const initialState: MissionState = {` (~L595). Add:

```ts
const initialState: MissionState = {
  statusLoaded: false,   // ← add
  // ... rest unchanged
};
```

- [ ] **Step 3: Set `statusLoaded: true` in `refreshState`**

Find `refreshState` (~L1121). Change the `setState` call at ~L1129–1130:

```ts
// Before:
const nextState = normalizeState(payload);
setState(nextState);

// After:
const nextState = normalizeState(payload);
setState({ ...nextState, statusLoaded: true });
```

- [ ] **Step 4: Fix the navigation guard**

Find the useEffect at ~L2529:

```ts
// Before:
useEffect(() => {
  if (activeSection === "runs" && state.runs.length === 0) {
    setActiveSection("home");
  }
}, [activeSection, state.runs.length]);

// After:
useEffect(() => {
  if (activeSection === "runs" && state.statusLoaded && state.runs.length === 0) {
    setActiveSection("home");
  }
}, [activeSection, state.statusLoaded, state.runs.length]);
```

- [ ] **Step 5: Add `RunsSkeleton` component and render it**

Add this component near other small utility components in `main.tsx` (search for `function EmptyState`  and add before it):

```tsx
function RunsSkeleton() {
  return (
    <div className="runs-skeleton">
      {[1, 2, 3].map((n) => (
        <div key={n} className="runs-skeleton-row" />
      ))}
    </div>
  );
}
```

Then in the Runs section (~L2701), add the skeleton before the tab row:

```tsx
{activeSection === "runs" && <>
  {!state.statusLoaded && <RunsSkeleton />}
  {state.statusLoaded && <>
    <div className="tab-row">
      {/* ... existing tab buttons unchanged ... */}
    </div>
    <div className="workspace-main" ...>
      {/* ... existing content unchanged ... */}
    </div>
  </>}
</>}
```

- [ ] **Step 6: Add skeleton CSS to `styles.css`**

```css
.runs-skeleton { padding: 24px 20px; display: flex; flex-direction: column; gap: 10px; }
.runs-skeleton-row {
  height: 36px;
  border-radius: 6px;
  background: linear-gradient(90deg, #161b22 25%, #1f2937 50%, #161b22 75%);
  background-size: 200% 100%;
  animation: skeleton-shimmer 1.4s infinite;
}
@keyframes skeleton-shimmer { 0% { background-position: 200% 0 } 100% { background-position: -200% 0 } }
```

- [ ] **Step 7: Verify TypeScript**

```powershell
cd apps/mission-control && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 8: Manual test**

With the backend running: click "Runs" in the sidebar immediately on page load → should stay on Runs (show skeleton briefly, then content). Should NOT bounce back to Home.

- [ ] **Step 9: Commit**

```powershell
git add apps/mission-control/src/main.tsx apps/mission-control/src/styles.css
git commit -m "fix: statusLoaded guard prevents Runs screen flicker on initial load"
```

---

## Task 2: Create `persistenceStore.ts` + fix BUG-03 chat archive

**Files:**
- Create: `apps/mission-control/src/services/persistenceStore.ts`
- Modify: `apps/mission-control/src/main.tsx` (archiveAgentChat ~L2092, session save ~L2500)

- [ ] **Step 1: Create `persistenceStore.ts`**

```ts
// apps/mission-control/src/services/persistenceStore.ts

const CHAT_SESSION_KEY = "mission-control-chat-session-v1";
const CHAT_ARCHIVE_KEY = "mission-control-chat-archives-v1";
export const ARTIFACT_TOMBSTONES_KEY = "mission-control-artifact-tombstones-v1";

// ── Chat session ──────────────────────────────────────────────

export function clearChatSession(): void {
  try { window.localStorage.removeItem(CHAT_SESSION_KEY); } catch {}
}

export function archiveChatSession(
  messages: Array<{ id: string; role: string; content: string }>,
  title?: string
): void {
  try {
    const raw = window.localStorage.getItem(CHAT_ARCHIVE_KEY);
    const existing = raw ? (JSON.parse(raw) as unknown[]) : [];
    const entry = {
      id: `chat-${Date.now()}`,
      created_at: new Date().toISOString(),
      title: title ?? "Chat archivado",
      messages,
    };
    const nextArchives = [entry, ...(Array.isArray(existing) ? existing : [])].slice(0, 20);
    window.localStorage.setItem(CHAT_ARCHIVE_KEY, JSON.stringify(nextArchives));
    clearChatSession();
  } catch {}
}

// ── Artifact tombstones ───────────────────────────────────────

export function loadArtifactTombstones(): Set<string> {
  try {
    const raw = window.localStorage.getItem(ARTIFACT_TOMBSTONES_KEY);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? new Set(parsed as string[]) : new Set();
  } catch {
    return new Set();
  }
}

export function addArtifactTombstone(registryId: string): void {
  try {
    const tombstones = loadArtifactTombstones();
    tombstones.add(registryId);
    window.localStorage.setItem(ARTIFACT_TOMBSTONES_KEY, JSON.stringify([...tombstones]));
  } catch {}
}

export function isArtifactTombstoned(registryId: string): boolean {
  return loadArtifactTombstones().has(registryId);
}

export function clearArtifactTombstones(): void {
  try { window.localStorage.removeItem(ARTIFACT_TOMBSTONES_KEY); } catch {}
}
```

- [ ] **Step 2: Fix `archiveAgentChat` in `main.tsx`**

Find `archiveAgentChat` at ~L2092. Add import at top of file:

```ts
import { archiveChatSession } from "./services/persistenceStore";
```

Replace the `archiveAgentChat` body:

```ts
const archiveAgentChat = () => {
  if (agentMessages.length === 0) {
    setStatus("No hay mensajes para archivar");
    return;
  }
  const firstUserMessage = agentMessages.find((m) => m.role === "user")?.content.trim();
  const title = firstUserMessage ? firstUserMessage.slice(0, 80) : "Chat archivado";
  archiveChatSession(agentMessages, title);  // writes archive + clears session key
  setAgentMessages([]);
  setAgentDraft("");
  setHomeAgentDraft("");
  setQueuedAgentPrompts([]);
  setStatus("Chat archivado ✓");
};
```

- [ ] **Step 3: Verify TypeScript**

```powershell
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4: Manual test**

1. Send a message in the chat.
2. Click "💬 Nuevo" or "📦 Archivar" (whichever triggers `archiveAgentChat`).
3. Hard-refresh the browser (Ctrl+Shift+R).
4. Expected: chat is empty. Messages did NOT return.

- [ ] **Step 5: Commit**

```powershell
git add apps/mission-control/src/services/persistenceStore.ts apps/mission-control/src/main.tsx
git commit -m "fix: archiveChatSession clears session storage so chat does not return on refresh"
```

---

## Task 3: BUG-02 — Artifact tombstones (deleted artifacts stay deleted)

**Files:**
- Modify: `apps/mission-control/src/services/artifactRegistry.ts`
- Modify: `apps/mission-control/src/hooks/useGeneratedArtifacts.ts`

- [ ] **Step 1: Add `isTombstoned` option to `mergeArtifactsIntoRegistry`**

Open `artifactRegistry.ts`. Change the signature and add the tombstone check inside the loop:

```ts
// Before signature:
export function mergeArtifactsIntoRegistry(
  generatedArtifacts: GeneratedArtifact[],
  currentRegistry = loadArtifactRegistry()
): PersistedGeneratedArtifact[] {

// After:
export function mergeArtifactsIntoRegistry(
  generatedArtifacts: GeneratedArtifact[],
  currentRegistry = loadArtifactRegistry(),
  options?: { isTombstoned?: (registryId: string) => boolean }
): PersistedGeneratedArtifact[] {
```

Inside the loop (after computing `registryId`, before the `existing` lookup), add:

```ts
// Skip tombstoned artifacts — user explicitly deleted them
if (options?.isTombstoned?.(registryId)) continue;
```

The full relevant section of the loop becomes:

```ts
for (const artifact of generatedArtifacts.slice().reverse()) {
  const contentHash = hashText(`${artifact.kind}\n${artifact.language}\n${artifact.content}`);
  const versionGroup = artifactVersionGroup(artifact);
  const registryId = artifactRegistryId(artifact, versionGroup, contentHash);

  // ← add this guard:
  if (options?.isTombstoned?.(registryId)) continue;

  const existing = registryById.get(registryId);
  // ... rest of loop unchanged
}
```

- [ ] **Step 2: Wire tombstones into `useGeneratedArtifacts`**

Open `useGeneratedArtifacts.ts`. Add import:

```ts
import { isArtifactTombstoned, addArtifactTombstone } from "../services/persistenceStore";
```

Change the `useEffect` that calls `mergeArtifactsIntoRegistry`:

```ts
// Before:
useEffect(() => {
  setArtifacts(mergeArtifactsIntoRegistry(generatedArtifacts));
}, [generatedArtifacts]);

// After:
useEffect(() => {
  setArtifacts(mergeArtifactsIntoRegistry(generatedArtifacts, undefined, {
    isTombstoned: isArtifactTombstoned,
  }));
}, [generatedArtifacts]);
```

Change `removeArtifact` to also add a tombstone:

```ts
// Before:
const removeArtifact = (artifactId: string) => {
  setArtifacts((current) => removeArtifactFromRegistry(artifactId, current));
};

// After:
const removeArtifact = (artifactId: string) => {
  addArtifactTombstone(artifactId);
  setArtifacts((current) => removeArtifactFromRegistry(artifactId, current));
};
```

- [ ] **Step 3: Verify TypeScript**

```powershell
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4: Manual test**

1. Generate an artifact (ask the agent for something visual).
2. Delete the artifact from the Artifact Studio.
3. Hard-refresh the browser.
4. Expected: artifact is gone. Does NOT reappear.

- [ ] **Step 5: Commit**

```powershell
git add apps/mission-control/src/services/artifactRegistry.ts apps/mission-control/src/hooks/useGeneratedArtifacts.ts apps/mission-control/src/services/persistenceStore.ts
git commit -m "fix: artifact tombstones prevent deleted artifacts from reappearing on refresh"
```

---

## Task 4: AgentPane CSS layout foundation

**Files:**
- Modify: `apps/mission-control/src/styles.css`

- [ ] **Step 1: Add AgentPane layout rules**

Append to `styles.css` (or replace the existing `.agent-pane` rule block — search for `.agent-pane {`):

```css
/* ── AgentPane layout ──────────────────────────────── */
.agent-pane {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
  min-width: 0;
}

.agent-pane-header {
  flex-shrink: 0;
  border-bottom: 1px solid #21262d;
  padding-bottom: 0;
}

.agent-metrics-strip {
  display: flex;
  gap: 12px;
  padding: 6px 12px;
  font-size: 11px;
  color: #8b949e;
  border-bottom: 1px solid #21262d;
}
.agent-metric { display: flex; align-items: center; gap: 4px; }
.agent-metric span { font-weight: 600; color: #c9d1d9; }
.agent-metric-ready span { color: #3fb950; }
.agent-metric-blocked span { color: #f85149; }

.agent-tab-bar {
  display: flex;
  gap: 0;
  border-bottom: 1px solid #21262d;
}
.agent-tab {
  flex: 1;
  padding: 7px 0;
  font-size: 12px;
  font-weight: 500;
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  color: #8b949e;
  cursor: pointer;
  transition: color 0.15s, border-color 0.15s;
}
.agent-tab:hover { color: #c9d1d9; }
.agent-tab.active { color: #58a6ff; border-bottom-color: #58a6ff; }

.agent-chat-body {
  flex: 1;
  overflow-y: auto;
  padding: 8px 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.agent-chat-footer {
  flex-shrink: 0;
  border-top: 1px solid #21262d;
  padding: 8px 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.agent-run-body,
.agent-insights-body {
  flex: 1;
  overflow-y: auto;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.run-action-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 6px 0;
}
.run-action-row button { flex: 1; min-width: 80px; }

.run-destructive-zone {
  margin-top: auto;
  padding-top: 10px;
  border-top: 1px solid #21262d;
  display: flex;
  gap: 6px;
}
.run-destructive-zone button {
  flex: 1;
  font-size: 11px;
  color: #8b949e;
  background: none;
  border: 1px solid #30363d;
  border-radius: 4px;
  padding: 4px 6px;
  cursor: pointer;
}
.run-destructive-zone button:hover { color: #f85149; border-color: #f85149; }

.agent-live-badge {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  font-size: 11px;
  font-weight: 600;
  color: #3fb950;
  cursor: pointer;
  background: rgba(63,185,80,.06);
  border-bottom: 1px solid rgba(63,185,80,.15);
  letter-spacing: .04em;
}

.chat-menu-wrap { position: relative; }
.chat-menu-dropdown {
  position: absolute;
  bottom: calc(100% + 4px);
  right: 0;
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 6px;
  padding: 4px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  z-index: 50;
  min-width: 180px;
}
.chat-menu-dropdown button {
  text-align: left;
  padding: 6px 10px;
  font-size: 12px;
  background: none;
  border: none;
  border-radius: 4px;
  color: #c9d1d9;
  cursor: pointer;
  width: 100%;
}
.chat-menu-dropdown button:hover { background: #21262d; }
.chat-menu-dropdown hr { border: none; border-top: 1px solid #21262d; margin: 2px 0; }
.chat-menu-toggle {
  background: none;
  border: 1px solid #30363d;
  border-radius: 4px;
  color: #8b949e;
  padding: 3px 8px;
  font-size: 14px;
  cursor: pointer;
  line-height: 1;
}
.chat-menu-toggle:hover { color: #c9d1d9; border-color: #58a6ff; }

/* ── Tooltip ────────────────────────────────────────── */
.tooltip-wrap { position: relative; display: inline-flex; }
.tooltip-bubble {
  display: none;
  position: absolute;
  bottom: calc(100% + 6px);
  left: 50%;
  transform: translateX(-50%);
  background: #1c2128;
  border: 1px solid #30363d;
  color: #c9d1d9;
  font-size: 11px;
  line-height: 1.4;
  padding: 5px 9px;
  border-radius: 5px;
  white-space: nowrap;
  max-width: 240px;
  white-space: normal;
  z-index: 200;
  pointer-events: none;
  text-align: center;
}
.tooltip-wrap:hover .tooltip-bubble {
  display: block;
  animation: tooltip-appear 0.12s ease 0.3s both;
}
@keyframes tooltip-appear { from { opacity: 0; transform: translateX(-50%) translateY(4px); } to { opacity: 1; transform: translateX(-50%) translateY(0); } }
```

- [ ] **Step 2: Commit CSS**

```powershell
git add apps/mission-control/src/styles.css
git commit -m "style: AgentPane flex layout foundation — tabs, metrics strip, tooltip CSS"
```

---

## Task 5: AgentPane JSX — header, tabs, Chat tab

**Files:**
- Modify: `apps/mission-control/src/main.tsx`

- [ ] **Step 1: Add `Tooltip` component**

Search for `function EmptyState` in `main.tsx`. Add this component just before it:

```tsx
function Tooltip({ text, children }: { text: string; children: React.ReactNode }) {
  return (
    <span className="tooltip-wrap">
      {children}
      <span className="tooltip-bubble">{text}</span>
    </span>
  );
}
```

- [ ] **Step 2: Replace `AgentPane` function body**

Find `function AgentPane(` at ~L3562 and replace the entire function body (everything inside the `return (...)`, keeping the function signature and props type untouched for now):

```tsx
function AgentPane({ run, state, readyRuns, blockedRuns, autonomyMode, onAutonomyModeChange, applyJson, onReview, onApply, onAutoApply, onRollback, messages, draft, busy, queuedPrompt, queuedPrompts, onDraftChange, onSend, onStop, onRemoveQueued, onPrioritizeQueued, onRunAction, nemoState, mcpWatcher, selfInsights, reviewPlan, applyHistory, riskMap, onRefreshRiskMap, onSendGuidedPrompt, onClearChat, onStartNewChat, onArchiveOldRuns, onClearAllRuns, planObjective, currentPlan, activeStepId, planProgress, onOpenObjectiveModal, onGeneratePlan, onSelectPlanStep, permissionJob, onGrantPermission, onDenyPermission, runningJob }: AgentPaneProps) {
  const [tab, setTab] = useState<"chat" | "run" | "insights">("chat");
  const [chatMenuOpen, setChatMenuOpen] = useState(false);
  const riskCount = run?.risk_flags.length ?? 0;

  return (
    <aside className="agent-pane">

      {/* ── HEADER (always visible) ── */}
      <div className="agent-pane-header">
        {permissionJob && permissionJob.permission_request && onGrantPermission && onDenyPermission && (
          <PermissionRequestPanel
            jobId={permissionJob.job_id}
            objective={String(permissionJob.objective || permissionJob.permission_request.rationale || "")}
            permissionRequest={permissionJob.permission_request as { job_id: string; categories: string[]; rationale: string; auto_approved: string[]; requires_user_approval: string[] }}
            onGranted={() => onGrantPermission(permissionJob.job_id, "")}
            onDenied={() => onDenyPermission(permissionJob.job_id, "")}
          />
        )}
        {runningJob && !permissionJob && (
          <Tooltip text="Job en ejecución — click para ver el timeline en la tab Run">
            <div className="agent-live-badge" onClick={() => setTab("run")} role="button" tabIndex={0}>
              <span className="agent-live-dot" aria-hidden="true" /> LIVE — {runningJob.job_id.slice(-8)}
            </div>
          </Tooltip>
        )}
        <div className="agent-metrics-strip">
          <Tooltip text="Total de runs en el workspace actual">
            <span className="agent-metric"><span>{state.runs.length}</span> Runs</span>
          </Tooltip>
          <Tooltip text="Runs listos para aplicar (sin risk flags bloqueantes)">
            <span className="agent-metric agent-metric-ready"><span>{readyRuns}</span> Ready</span>
          </Tooltip>
          <Tooltip text="Runs con risk flags que requieren revisión manual antes de aplicar">
            <span className="agent-metric agent-metric-blocked"><span>{blockedRuns}</span> Blocked</span>
          </Tooltip>
          <Tooltip text="Memoria NEMO: activa cuando nemo_required está habilitado en Settings">
            <span className="agent-metric"><span>{state.settings.nemo_required ? "on" : "off"}</span> NEMO</span>
          </Tooltip>
        </div>
        <div className="agent-tab-bar" role="tablist">
          <Tooltip text="Chat con el agente — envía instrucciones y recibe respuestas">
            <button role="tab" className={`agent-tab ${tab === "chat" ? "active" : ""}`} onClick={() => setTab("chat")}>Chat</button>
          </Tooltip>
          <Tooltip text="Controla el run seleccionado: revisa el diff, aplica o deshace cambios">
            <button role="tab" className={`agent-tab ${tab === "run" ? "active" : ""}`} onClick={() => setTab("run")}>Run</button>
          </Tooltip>
          <Tooltip text="Análisis: Self-Improvement, Risk Map, NEMO Memory, Apply Plan">
            <button role="tab" className={`agent-tab ${tab === "insights" ? "active" : ""}`} onClick={() => setTab("insights")}>Insights</button>
          </Tooltip>
        </div>
      </div>

      {/* ── CHAT TAB ── */}
      {tab === "chat" && (
        <>
          <div className="agent-chat-body">
            <AgentLiveStatus busy={busy} queuedPrompt={queuedPrompt} messages={messages} />
            {planObjective && (
              <div className="agent-card" style={{ marginBottom: 4 }}>
                <span>Objetivo activo</span>
                <strong>{planObjective.title}</strong>
                <p>{planObjective.description || "Sin descripcion"}</p>
              </div>
            )}
            {currentPlan && (
              <PlanProgress
                objective={planObjective}
                currentPlan={currentPlan}
                activeStepId={activeStepId}
                planProgress={planProgress}
                onStepClick={onSelectPlanStep}
              />
            )}
            <div className="chat-thread">
              {messages.map((message) => (
                <AgentChatMessage message={message} onRunAction={onRunAction} key={message.id} />
              ))}
            </div>
          </div>

          <div className="agent-chat-footer">
            <div className="chat-steering">
              <Tooltip text="Continua desde el ultimo paso y explicame el avance en 3 bullets.">
                <button onClick={() => onSendGuidedPrompt("Continua desde el ultimo paso y explicame el avance en 3 bullets.")}>Continuar</button>
              </Tooltip>
              <Tooltip text="Activa modo plan: genera o refina pasos concretos en formato [STEP N: titulo -> resultado esperado].">
                <button onClick={() => onSendGuidedPrompt("Activa modo plan: genera o refina pasos concretos en formato [STEP N: titulo -> resultado esperado].")}>Plan</button>
              </Tooltip>
              <Tooltip text="Reformula la respuesta con opciones accionables y pasos concretos.">
                <button onClick={() => onSendGuidedPrompt("Reformula la respuesta con opciones accionables y pasos concretos.")}>Reformular</button>
              </Tooltip>
              <Tooltip text="Detener la respuesta del agente en curso">
                <button className="stop" onClick={onStop} disabled={!busy}><Square size={13} /> Detener</button>
              </Tooltip>
            </div>
            <div className="chat-composer">
              <textarea
                value={draft}
                onChange={(e) => onDraftChange(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) onSend(); }}
                placeholder="Pide al agente continuar, corregir o aplicar cambios..."
              />
              <div style={{ display: "flex", gap: 4 }}>
                <Tooltip text="Enviar mensaje (también Ctrl+Enter)">
                  <button onClick={onSend} disabled={!draft.trim()} title="Enviar"><Send size={15} /></button>
                </Tooltip>
                <div className="chat-menu-wrap">
                  <Tooltip text="Más acciones: nuevo chat, limpiar, objetivo, plan">
                    <button className="chat-menu-toggle" onClick={() => setChatMenuOpen((o) => !o)} aria-label="Más opciones">···</button>
                  </Tooltip>
                  {chatMenuOpen && (
                    <div className="chat-menu-dropdown">
                      <button onClick={() => { onStartNewChat(); setChatMenuOpen(false); }}>💬 Nuevo chat</button>
                      <button onClick={() => { onClearChat(); setChatMenuOpen(false); }}>🗑️ Limpiar chat</button>
                      <hr />
                      <button onClick={() => { onOpenObjectiveModal(); setChatMenuOpen(false); }}>🎯 Definir objetivo</button>
                      <button onClick={() => { onGeneratePlan(); setChatMenuOpen(false); }} disabled={!planObjective}>🧭 Generar plan</button>
                    </div>
                  )}
                </div>
              </div>
            </div>
            {(busy || queuedPrompt) && (
              <div className="chat-queue-status">
                <span>{busy ? "El agente esta respondiendo..." : ""}</span>
                {queuedPrompt && <strong>Siguiente: {queuedPrompt}</strong>}
                {queuedPrompts.length > 1 && <span>{queuedPrompts.length - 1} mensaje(s) adicionales en cola</span>}
                {queuedPrompts.length > 0 && (
                  <div className="queued-list">
                    {queuedPrompts.map((item, index) => (
                      <div className="queued-item" key={`${item}-${index}`}>
                        <span>{index + 1}. {item}</span>
                        <div>
                          <button onClick={() => onPrioritizeQueued(index)} disabled={index === 0}>Priorizar</button>
                          <button onClick={() => onRemoveQueued(index)}>Quitar</button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </>
      )}

      {/* ── RUN TAB ── */}
      {tab === "run" && (
        <div className="agent-run-body">
          {runningJob && (
            <TimelinePanel jobId={runningJob.job_id} isLive={true} />
          )}
          {!run && <EmptyState />}
          {run && (
            <>
              <div className="agent-card">
                <span>Run seleccionado</span>
                <strong>{run.task_id} / {run.run_id}</strong>
                <p>{run.source_json}</p>
              </div>
              <div className="agent-card">
                <span>Estado</span>
                <strong>{statusLabel(run.review_status)}</strong>
                <p>
                  {run.mergeable ? "Listo para aplicar" : "Requiere revisión"} · {riskCount} riesgo(s) · {run.changed_files.length} archivo(s)
                </p>
                {run.risk_flags.length > 0 && (
                  <div className="risk-box">{run.risk_flags.map((r) => <span key={r}>{r}</span>)}</div>
                )}
              </div>
              <AutopilotFlowPanel
                run={run}
                mode={autonomyMode}
                onModeChange={onAutonomyModeChange}
                busy={busy}
                onReview={onReview}
                onApply={onApply}
                onAutoApply={onAutoApply}
                onSendGuidedPrompt={onSendGuidedPrompt}
              />
              <div className="run-action-row">
                <Tooltip text="Abre el diff completo para revisar los cambios antes de aplicar">
                  <button onClick={() => onReview(run)}><GitPullRequest size={16} /> Revisar</button>
                </Tooltip>
                <Tooltip text="Escribe los cambios al repo. Solo disponible si el run es mergeable">
                  <button disabled={!run.mergeable} onClick={() => onApply(run)}><CheckCircle2 size={16} /> Aplicar</button>
                </Tooltip>
                <Tooltip text="Aplica directamente si pasa controles automáticos, sin revisión manual">
                  <button disabled={!run.mergeable} onClick={() => onAutoApply(run)}><ShieldCheck size={16} /> Rápido</button>
                </Tooltip>
                <Tooltip text="Revierte el último apply. Requiere que el run tenga snapshot previo">
                  <button onClick={() => onRollback(run)}><RotateCcw size={16} /> Deshacer</button>
                </Tooltip>
              </div>
              {applyJson && <p className="muted">Last apply: {applyJson}</p>}
              <InsightSection title="Apply History" summary={`${applyHistory.length} evento(s)`} open={false}>
                <ApplyHistoryPanel applies={applyHistory} />
              </InsightSection>
            </>
          )}
          <div className="run-destructive-zone">
            <Tooltip text="Limpia runs completados con más de 24h. No afecta runs activos.">
              <button onClick={onArchiveOldRuns}>📦 Limpiar antiguos</button>
            </Tooltip>
            <Tooltip text="Elimina TODOS los runs del workspace. Acción irreversible.">
              <button onClick={onClearAllRuns}>🧨 Limpiar todo</button>
            </Tooltip>
          </div>
        </div>
      )}

      {/* ── INSIGHTS TAB ── */}
      {tab === "insights" && (
        <div className="agent-insights-body">
          <InsightSection
            title="Self-Improvement"
            summary={`${selfInsights?.trajectory?.grade ?? "sin run"} / ${selfInsights?.impact?.risk_flags.length ?? 0} riesgo(s)`}
            open={false}
          >
            <SelfImprovementPanel insights={selfInsights} />
          </InsightSection>
          <InsightSection
            title="Risk Map"
            summary={`${riskMap?.count ?? 0} patron(es)`}
            open={false}
          >
            <RiskMapPanel riskMap={riskMap} onRefresh={onRefreshRiskMap} />
          </InsightSection>
          <InsightSection
            title="NEMO Memory"
            summary={`${nemoState?.health.atom_count ?? 0} atoms / ${nemoState?.health.evidence_count ?? 0} evidence`}
            open={false}
          >
            <NemoMemoryPanel nemoState={nemoState} mcpWatcher={mcpWatcher} />
          </InsightSection>
          <InsightSection
            title="Apply Plan"
            summary={reviewPlan ? `${reviewPlan.mergeable ? "mergeable" : "blocked"} / ${reviewPlan.risk_flags.length} riesgo(s)` : "sin plan"}
            open={false}
          >
            <ReviewPlanPanel plan={reviewPlan} />
          </InsightSection>
        </div>
      )}

    </aside>
  );
}
```

- [ ] **Step 3: Close the chat menu on outside click**

Add this `useEffect` inside `AgentPane`, right after the `chatMenuOpen` state:

```ts
const chatMenuRef = React.useRef<HTMLDivElement>(null);
useEffect(() => {
  if (!chatMenuOpen) return;
  const handle = (e: MouseEvent) => {
    if (chatMenuRef.current && !chatMenuRef.current.contains(e.target as Node)) {
      setChatMenuOpen(false);
    }
  };
  document.addEventListener("mousedown", handle);
  return () => document.removeEventListener("mousedown", handle);
}, [chatMenuOpen]);
```

Then wrap the `.chat-menu-wrap` div with `ref={chatMenuRef}`:

```tsx
<div className="chat-menu-wrap" ref={chatMenuRef}>
```

- [ ] **Step 4: Verify TypeScript**

```powershell
npx tsc --noEmit
```

Expected: no errors. If there are errors about removed props in `AgentPaneProps`, that's expected — fix in Task 6.

- [ ] **Step 5: Commit**

```powershell
git add apps/mission-control/src/main.tsx
git commit -m "feat: AgentPane tabbed layout — Chat/Run/Insights tabs with metrics header and tooltips"
```

---

## Task 6: AgentPane — prop reduction + call site cleanup

**Files:**
- Modify: `apps/mission-control/src/main.tsx` (AgentPaneProps type ~L3331, call site ~L2767)

- [ ] **Step 1: Remove settings-related props from `AgentPaneProps`**

Find `type AgentPaneProps` at ~L3331. Remove these props (they belong to the Settings section, not AgentPane):

```ts
// DELETE these lines from AgentPaneProps:
settingsDraft: MissionState["settings"];
onSettingsChange: (settings: MissionState["settings"]) => void;
onSaveSettings: () => void;
repoDraft: string;
onRepoDraftChange: (value: string) => void;
onOpenRepo: (repoPath?: string) => void;
cloneDraft: { url: string; destination: string };
onCloneDraftChange: (draft: { url: string; destination: string }) => void;
onCloneRepo: () => void;
cleanupResult: CleanupResult | null;
onCleanup: (dryRun: boolean) => void;
orphanCleanupResult: OrphanCleanupResult | null;
onCleanupOrphans: (dryRun: boolean) => void;
onRefreshMcpWatcher: () => void;
showSettingsPanel?: boolean;
```

- [ ] **Step 2: Remove those props from the call site**

Find the `<AgentPane` call at ~L2767. Remove the corresponding prop lines:

```tsx
// DELETE these lines from the <AgentPane ... /> call:
settingsDraft={settingsDraft}
onSettingsChange={setSettingsDraft}
onSaveSettings={saveSettings}
repoDraft={repoDraft}
onRepoDraftChange={setRepoDraft}
onOpenRepo={openRepo}
cloneDraft={cloneDraft}
onCloneDraftChange={setCloneDraft}
onCloneRepo={cloneRepo}
cleanupResult={cleanupResult}
onCleanup={cleanupArtifacts}
orphanCleanupResult={orphanCleanupResult}
onCleanupOrphans={cleanupOrphanJobs}
onRefreshMcpWatcher={loadNemoMcpStatus}
showSettingsPanel={false}
```

- [ ] **Step 3: Verify TypeScript**

```powershell
npx tsc --noEmit
```

Expected: no errors. This confirms that all removed props are no longer referenced inside AgentPane's body (from Task 5) and no longer passed from the call site.

- [ ] **Step 4: Manual test — full flow**

1. Open Runs tab → no flicker, loads content ✅
2. In Chat tab: send a message, archive it, refresh → messages gone ✅
3. Generate an artifact, delete it, refresh → artifact gone ✅
4. AgentPane Chat tab: chat thread scrolls, composer fixed at bottom ✅
5. AgentPane Run tab: run details, apply buttons with tooltips ✅
6. AgentPane Insights tab: accordions collapsed by default ✅
7. Hover over metrics, buttons, tabs → tooltip appears with delay ✅
8. Click `···` → menu opens; click outside → menu closes ✅

- [ ] **Step 5: Final TypeScript check**

```powershell
npx tsc --noEmit
```

Expected: 0 errors.

- [ ] **Step 6: Commit**

```powershell
git add apps/mission-control/src/main.tsx
git commit -m "refactor: reduce AgentPane props by 15 — settings-related props moved to Settings section"
```

---

## Summary of commits

| Commit | What |
|---|---|
| `fix: statusLoaded guard prevents Runs screen flicker` | BUG-01 |
| `fix: archiveChatSession clears session so chat does not return on refresh` | BUG-03 |
| `fix: artifact tombstones prevent deleted artifacts from reappearing` | BUG-02 |
| `style: AgentPane flex layout foundation` | CSS |
| `feat: AgentPane tabbed layout — Chat/Run/Insights with tooltips` | JSX |
| `refactor: reduce AgentPane props by 15` | Cleanup |
