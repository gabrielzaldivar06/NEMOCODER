# Mission Control — Jobs Feed & Run Results UX

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hacer visibles e intuitivos los resultados de handoffs y runs reemplazando el TelemetryColumn con un feed de jobs accionable, agregando mini-tarjetas de resultado en el chat, y un panel de detalle deslizable.

**Architecture:** Tres cambios coordinados en el frontend (React/TypeScript): (1) reemplazar `TelemetryColumn` con `TelemetryStrip` + `JobsFeed`, (2) inyectar `JobResultCard` en los mensajes del chat cuando un job termina o necesita permisos, (3) nuevo componente `JobDetailPanel` como slide-in desde el right edge. El backend no cambia. Todo funciona con el estado existente de `HandoffJob[]` y `MissionRun[]`.

**Tech Stack:** React 18, TypeScript, CSS existente en `styles.css`, lucide-react, estado global en `main.tsx`.

---

## Contexto y Principio Guía

**Lo que existe y se conserva intacto:**
- Nav izquierda (Home, Runs, Git, Terminal, Browser)
- Run tree sidebar dentro de Home
- Tabs Conversación / Lista / 0 Eventos en el área principal
- CommandDock en el bottom
- ArtifactWorkbench, MissionTimeline, sección Runs completa
- Todo el backend Python

**Lo que cambia (solo frontend):**
- `TelemetryColumn` → `TelemetryStrip` (compacta, 1 línea) + `JobsFeed` debajo
- Mensajes del chat: al completarse un job, el agente puede incluir un `JobResultCard`
- Nuevo: `JobDetailPanel` slide-in que aparece al hacer click en "Ver detalle"

**El problema que resuelve:**
- "Bloqueado" aparecía en 6 lugares sin explicar qué hacer → ahora hay UNA acción clara en el feed y en el chat
- Resultados de runs enterrados en 3 niveles de tabs → ahora visibles en el sidebar derecho y en el chat
- Información del agente desconectada de la UI → mini-tarjeta inline en el mensaje

---

## Arquitectura de Archivos

### Archivos nuevos
- `apps/mission-control/src/components/TelemetryStrip.tsx` — barra de estado compacta (1 línea)
- `apps/mission-control/src/components/JobsFeed.tsx` — feed de jobs en sidebar derecho
- `apps/mission-control/src/components/JobResultCard.tsx` — mini-tarjeta de resultado en chat
- `apps/mission-control/src/components/JobDetailPanel.tsx` — slide-in panel de detalle

### Archivos modificados
- `apps/mission-control/src/main.tsx` — reemplazar `<TelemetryColumn>` con `<TelemetryStrip>` + `<JobsFeed>`, inyectar `<JobResultCard>` en mensajes, manejar estado `detailJobId`
- `apps/mission-control/src/styles.css` — estilos para los 4 nuevos componentes

---

## Componentes

### 1. `TelemetryStrip`

Una sola fila de 28-32px que reemplaza el sidebar derecho superior. Muestra solo el estado operacional del sistema.

**Props:**
```typescript
interface TelemetryStripProps {
  nemoReady: boolean;
  llmReady: boolean;
  gitReady: boolean;
  pendingApprovals: number;
}
```

**Render:** `Sistema · 🟢 NEMO · 🟢 LLM · 🟢 Git · ⚠ 1 OK pendiente`

Cada chip es un dot de color (verde/naranja/rojo) + label corto. Si `pendingApprovals > 0`, el chip de aprobaciones es naranja y pulsa con CSS animation.

### 2. `JobsFeed`

Feed de jobs que ocupa el resto del sidebar derecho (debajo de `TelemetryStrip`). Ordena por estado: blocked primero, luego running, luego success/error recientes.

**Props:**
```typescript
interface JobsFeedProps {
  jobs: HandoffJob[];
  runs: MissionRun[];
  onSelectJob: (jobId: string) => void;
  onApprove: (jobId: string) => void;
  onDeny: (jobId: string) => void;
  onMerge: (sourceJson: string) => void;
}
```

**Items del feed** — derivados de `jobs` + `runs` fusionados por `task_id + run_id`:

| Estado | Color borde | Acciones inline |
|--------|-------------|-----------------|
| `status === "running"` | azul `#38bdf8` | (ninguna, solo indicador animado) |
| `permission_request` con `requires_user_approval.length > 0` | naranja `#f59e0b` | Aprobar / Denegar |
| job completado + run con `review_status === "awaiting_review"` | verde `#4ade80` | Merge / Ver detalle |
| job completado + run con `review_status === "blocked"` y risk_flags | rojo `#ef4444` | Ver detalle |
| job completado exitosamente | verde pálido | Ver detalle |

Cada item muestra: nombre del objetivo (truncado a 28 chars), estado, archivos cambiados si existe run, tiempo relativo.

### 3. `JobResultCard`

Mini-tarjeta que el chat inyecta en el mensaje del agente cuando un job termina o cuando el agente solicita permisos. Es el equivalente visual de lo que el agente dice en texto.

**Props:**
```typescript
interface JobResultCardProps {
  kind: "success" | "blocked" | "error";
  jobId: string;
  objective: string;
  durationSeconds?: number;
  changedFiles?: string[];
  branch?: string;
  permissionCategories?: string[];  // solo si kind === "blocked"
  onViewDetail: (jobId: string) => void;
  onMerge?: (jobId: string) => void;
  onApprove?: (jobId: string) => void;
  onDeny?: (jobId: string) => void;
}
```

**Render por kind:**
- `success`: fondo verde oscuro, "✅ [objetivo]", "N archivos · 🌿 [rama] · ✓ validación", botones Merge / Ver archivos / Log
- `blocked`: fondo naranja oscuro, "⚠ [objetivo] — necesita tu OK", lista de categorías (red, shell, config), botones Aprobar / Denegar
- `error`: fondo rojo oscuro, "❌ [objetivo] — falló", botón Ver log

**Cuándo se inyecta:** En `MissionHome`, para cada `AgentMessage` con `role === "assistant"` que tenga `actions[]` donde alguna action tiene `kind === "run"` o `kind === "handoff"`, se busca el `HandoffJob` cuyo `job_id` coincide con `action.payload.job_id`. Si ese job existe y tiene `status === "completed"` o `permission_request.requires_user_approval.length > 0`, se renderiza el `JobResultCard` al final del mensaje. Si no existe aún (job todavía en curso), no se renderiza nada.

### 4. `JobDetailPanel`

Slide-in desde el borde derecho que aparece encima del sidebar de Jobs (no lo reemplaza). Ancho fijo de 280px. Se cierra con × o clickando fuera.

**Props:**
```typescript
interface JobDetailPanelProps {
  jobId: string;
  jobs: HandoffJob[];
  runs: MissionRun[];
  onClose: () => void;
  onMerge: (sourceJson: string) => void;
  onApprove: (jobId: string) => void;
  onDeny: (jobId: string) => void;
}
```

**Secciones:**
1. Header: nombre del objetivo + × para cerrar
2. Status row: Estado (chip de color) · Duración · Rama git
3. Archivos cambiados: lista con nombre de archivo + `+N -M` en color
4. Botones de acción: Merge (si awaiting_review) / Aprobar (si permission_request) / Ver log completo
5. Log inline: últimas 10 líneas del log del job (desde `job.logs`)

**Comportamiento:**
- Se abre con `setDetailJobId(jobId)` en `main.tsx`
- Se cierra con `setDetailJobId(null)`
- CSS: `position: fixed`, `right: 0`, `top: 36px` (bajo el topbar), `bottom: 28px` (sobre el statusbar), `z-index: 50`, transición `transform: translateX(0)` ↔ `translateX(100%)`

---

## Cambios en `main.tsx`

### Estado nuevo
```typescript
const [detailJobId, setDetailJobId] = useState<string | null>(null);
```

### Render en `MissionHome`
Dentro del JSX de `MissionHome`, en el lugar donde hoy está `<TelemetryColumn>`:
```tsx
<div className="right-col">
  <TelemetryStrip
    nemoReady={nemoState?.available ?? false}
    llmReady={state.statusLoaded}
    gitReady={state.is_git_repo ?? false}
    pendingApprovals={state.jobs.filter(j =>
      j.permission_request?.requires_user_approval?.length > 0 &&
      !j.permission_request?.decision
    ).length}
  />
  <JobsFeed
    jobs={state.jobs}
    runs={state.runs}
    onSelectJob={setDetailJobId}
    onApprove={(jobId) => postJson(`/api/run/${jobId}/permission-grant`, {}).then(refreshState)}
    onDeny={(jobId) => postJson(`/api/run/${jobId}/permission-deny`, {}).then(refreshState)}
    onMerge={(job) => {
      // Si el job tiene worktree: POST /api/run/${job.job_id}/worktree-merge
      // Si no: usar applyRun(run) con la MissionRun correspondiente
      const run = state.runs.find(r => r.task_id === job.task_id && r.run_id === job.run_id);
      if (job.run_json && run) applyRun(run);
      else fetch(`/api/run/${job.job_id}/worktree-merge`, { method: "POST" }).then(refreshState);
    }}
  />
</div>
{detailJobId && (
  <JobDetailPanel
    jobId={detailJobId}
    jobs={state.jobs}
    runs={state.runs}
    onClose={() => setDetailJobId(null)}
    onMerge={(sourceJson) => { handleMerge(sourceJson); setDetailJobId(null); }}
    onApprove={(jobId) => postJson(`/api/run/${jobId}/permission-grant`, {}).then(refreshState)}
    onDeny={(jobId) => postJson(`/api/run/${jobId}/permission-deny`, {}).then(refreshState)}
  />
)}
```

### Inyección de `JobResultCard` en mensajes
En el render de mensajes del chat, para cada `AgentMessage` donde `role === "assistant"`, detectar si hay un job completado correspondiente:

```typescript
function findJobForMessage(msg: AgentMessage, jobs: HandoffJob[]): HandoffJob | null {
  // Busca en msg.actions un action con kind "run" o "handoff"
  // Extrae el job_id del payload
  // Retorna el job correspondiente si existe y está completed o tiene permission_request
}
```

Si `findJobForMessage` retorna un job, renderizar `<JobResultCard>` al final del mensaje.

---

## Estilos nuevos en `styles.css`

### `.right-col`
```css
.right-col {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
  border-left: 1px solid var(--border);
}
```

### `.telemetry-strip`
```css
.telemetry-strip {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 10px;
  height: 30px;
  flex-shrink: 0;
  border-bottom: 1px solid var(--border);
  background: var(--bg-darker);
  font-size: 10px;
}
.tstrip-dot { width: 6px; height: 6px; border-radius: 50%; }
.tstrip-dot.ok { background: #4ade80; }
.tstrip-dot.warn { background: #f59e0b; }
.tstrip-dot.warn { animation: pulse-warn 1.5s ease-in-out infinite; }
@keyframes pulse-warn { 0%,100% { opacity:1; } 50% { opacity:0.4; } }
```

### `.jobs-feed`
```css
.jobs-feed { flex: 1; overflow-y: auto; padding: 6px; }
.jobs-feed-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; padding: 0 2px; }
.jobs-feed-title { color: #0ea5e9; font-size: 11px; font-weight: 600; }
.job-feed-item { border-radius: 4px; padding: 7px 8px; margin-bottom: 5px; border-left: 3px solid; cursor: pointer; transition: filter 0.1s; }
.job-feed-item:hover { filter: brightness(1.2); }
.job-feed-item.success { background: #031a0d; border-color: #4ade80; }
.job-feed-item.blocked { background: #1a0e00; border-color: #f59e0b; }
.job-feed-item.running { background: #060d18; border-color: #38bdf8; }
.job-feed-item.error   { background: #1a0505; border-color: #ef4444; }
```

### `.job-result-card` (en chat)
```css
.job-result-card { margin-top: 8px; border-radius: 5px; padding: 10px; }
.job-result-card.success { background: #031a0d; border: 1px solid #166534; }
.job-result-card.blocked { background: #1a0e00; border: 1px solid #92400e; }
.job-result-card.error   { background: #1a0505; border: 1px solid #7f1d1d; }
```

### `.job-detail-panel`
```css
.job-detail-panel {
  position: fixed;
  right: 0; top: 36px; bottom: 28px;
  width: 280px;
  background: #0a1421;
  border-left: 1px solid #0ea5e9;
  z-index: 50;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  transform: translateX(0);
  transition: transform 0.2s ease;
  box-shadow: -4px 0 24px rgba(0,0,0,0.5);
}
.job-detail-panel.closed { transform: translateX(100%); }
```

---

## Qué NO cambia

- `PermissionRequestPanel.tsx` — se mantiene como está (usado en sección Runs)
- `TelemetryColumn.tsx` — se deja en el codebase, solo se deja de usar en `MissionHome`
- `TimelinePanel.tsx`, `JobLiveLog.tsx`, `WorktreeDiffPanel.tsx` — sin cambios
- Backend Python — sin cambios
- Sección "Runs" completa — sin cambios
- La lógica de `runAgentAction`, `postJson`, `refreshState` — sin cambios

---

## Criterios de Éxito

1. El sidebar derecho muestra TelemetryStrip + JobsFeed en lugar de TelemetryColumn
2. Cuando un handoff completa, aparece una tarjeta verde en el chat con botones Merge/Ver archivos
3. Cuando un job requiere permiso, aparece una tarjeta naranja en el chat con Aprobar/Denegar
4. Click en "Ver detalle" abre el panel slide-in con archivos, estado y acciones
5. Aprobar/Denegar desde cualquiera de los tres puntos (chat, feed, panel) funciona igual
6. `npx tsc --noEmit` pasa limpio
7. La sección Runs, Git, Terminal, Browser funcionan igual que antes
