# Frontend State + AgentPane Refactor — Spec

**Fecha:** 2026-05-14  
**Scope:** Bugs de estado persistente + layout/ergonomía de AgentPane  
**Approach:** B — fix de raíz + centralizar persistencia + rediseño moderado

---

## Contexto

`main.tsx` tiene 4913 líneas con ~40 `useState` independientes y persistencia a localStorage dispersa. El `AgentPane` hace 12 cosas distintas apiladas sin jerarquía. Cuatro bugs reportados por el usuario.

---

## Bugs a resolver

### BUG-01: Runs screen flicker/redirect
**Causa:** `useEffect` en L2529 redirige a Home si `state.runs.length === 0`, pero `state.runs` arranca vacío antes de que el backend responda. No hay distinción entre "cargando" y "vacío real".

**Fix:** Añadir `statusLoaded: boolean` a `MissionState` (default `false`). Se activa a `true` tras el primer poll exitoso al backend. El guard cambia a:
```ts
if (activeSection === "runs" && state.statusLoaded && state.runs.length === 0) {
  setActiveSection("home");
}
```
Mientras `statusLoaded === false`, la pantalla Runs muestra un skeleton en lugar de rebotar.

### BUG-02: Artifacts reaparecen al refrescar
**Causa:** `mergeArtifactsIntoRegistry` re-escanea los mensajes del chat en cada render y re-agrega cualquier artifact presente en un mensaje, aunque haya sido borrado del registry. El delete solo afecta localStorage, no la fuente.

**Fix:** Sistema de tombstones. Al borrar un artifact, su `registryId` se añade a `ARTIFACT_TOMBSTONES_KEY` en localStorage. `mergeArtifactsIntoRegistry` recibe `isTombstoned` y omite los IDs tombstoneados. El tombstone es permanente — el artifact no reaparece aunque el mensaje siga en el chat.

### BUG-03: Chat regresa al archivar
**Causa:** `archiveAgentChat` escribe en `CHAT_ARCHIVE_STORAGE_KEY` pero no limpia `CHAT_SESSION_STORAGE_KEY`. Al refrescar, la sesión actual se recarga con los mismos mensajes.

**Fix:** Al archivar, también llamar `clearSession()` que borra `CHAT_SESSION_STORAGE_KEY`. La sesión queda vacía.

### BUG-04: AgentPane stacking / layout roto
**Causa:** `aside.agent-pane` es una columna sin altura fija ni overflow controlado. 12 áreas funcionales apiladas sin jerarquía. En tab "file" compite en grid de 2 columnas con `EditorPane`.

**Fix:** Rediseño completo del AgentPane (ver sección siguiente).

---

## Cambio 1: `usePersistenceStore` hook

**Archivo nuevo:** `apps/mission-control/src/hooks/usePersistenceStore.ts`

Única capa que toca localStorage para chat y artifacts. Centraliza:

```ts
interface PersistenceStore {
  // Chat
  loadSession(): AgentMessage[]
  saveSession(messages: AgentMessage[]): void
  clearSession(): void
  archiveSession(messages: AgentMessage[]): void  // escribe archive + clearSession()
  loadArchives(): ChatArchive[]

  // Artifact tombstones
  addTombstone(registryId: string): void
  isTombstoned(registryId: string): boolean
  clearTombstones(): void  // para debug/reset
}
```

**Claves de localStorage:**
- `mission-control-chat-session-v1` — sesión actual (existente)
- `mission-control-chat-archives-v1` — archivos (existente)
- `mission-control-artifact-tombstones-v1` — nuevo, Set de registryIds borrados

**`main.tsx`:** reemplaza los `localStorage.getItem/setItem` directos por el hook. `archiveAgentChat` pasa a llamar `store.archiveSession(agentMessages)`.

---

## Cambio 2: Artifact tombstones en `artifactRegistry.ts`

`mergeArtifactsIntoRegistry` acepta una opción nueva:
```ts
mergeArtifactsIntoRegistry(
  generatedArtifacts: GeneratedArtifact[],
  options?: { isTombstoned?: (registryId: string) => boolean }
): PersistedGeneratedArtifact[]
```

Si `isTombstoned(registryId)` retorna `true`, el artifact se omite aunque aparezca en los mensajes.

`useGeneratedArtifacts` pasa `isTombstoned` desde `usePersistenceStore`. `removeArtifact` llama `addTombstone` además de `removeArtifactFromRegistry`.

---

## Cambio 3: `statusLoaded` en MissionState

```ts
// En MissionState:
statusLoaded: boolean  // default: false en initialState

// En el primer poll exitoso (la función que llama /api/status y hace setState):
// statusLoaded se preserva con spread y se fuerza a true — NO viene del backend
setState(prev => ({ ...normalizeState(payload), statusLoaded: true }))

// Guard en useEffect:
if (activeSection === "runs" && state.statusLoaded && state.runs.length === 0) {
  setActiveSection("home");
}

// Skeleton en Runs mientras !statusLoaded:
{activeSection === "runs" && !state.statusLoaded && <RunsSkeleton />}
// RunsSkeleton: tres filas de placeholder animadas (CSS animation), sin lógica
```

---

## Cambio 4: AgentPane — rediseño ergonómico

### Estructura

```
aside.agent-pane  (display:flex; flex-direction:column; height:100%; overflow:hidden)
│
├── .agent-pane-header  (flex-shrink:0)
│     [si permissionJob]  PermissionBanner (rojo, prioritario)
│     [si runningJob]     LiveBadge compacto (expandible con click)
│     .agent-metrics-strip   Runs · Ready · Blocked · NEMO  (una línea, con tooltips)
│     .agent-tab-bar         [Chat★] [Run] [Insights]  (con tooltips)
│
├── Tab Chat (default)
│     .agent-chat-body  (flex:1; overflow-y:auto)
│       AgentLiveStatus  (sticky top)
│       planObjective card  (si activo)
│       PlanProgress        (si activo)
│       .chat-thread        (mensajes)
│     .agent-chat-footer  (flex-shrink:0)
│       .chat-steering  (Continuar · Plan · Reformular · [Stop])
│       .chat-composer  (textarea + send)
│       .chat-queue     (solo si hay mensajes en cola)
│     .agent-chat-menu  (··· dropdown en header del tab)
│       Nuevo chat · Limpiar chat
│       ─────
│       Definir objetivo · Generar plan
│
├── Tab Run
│     run-empty-state  (si !run)
│     run details card  (task_id · status · archivos · riesgos)
│     AutopilotFlowPanel
│     .run-action-row   Revisar · Aplicar · Aplicar rápido · Deshacer  (con tooltips)
│     Apply History  (acordeón colapsado)
│     Limpiar runs · Limpiar todo  (zona destructiva, al fondo)
│
└── Tab Insights  (acordeón, todo colapsado por default)
      Self-Improvement · Risk Map · NEMO Memory · Apply Plan
```

### Tooltips

Implementación en dos niveles:
- **`title` nativo:** botones simples donde el texto cabe en una línea
- **`<Tooltip>` component:** para texto largo o con formato. Componente pequeño declarado dentro de `main.tsx` (no archivo separado). CSS absoluto posicionado sobre el trigger vía `position:relative` en el padre. Aparece en hover con delay de 300ms (CSS `transition-delay`) para evitar flash accidental. No depende de librerías externas.

Ejemplos de contenido:
| Elemento | Tooltip |
|---|---|
| Botón Limpiar chat | "Borra el historial de esta sesión. No afecta runs ni el backend." |
| Botón Aplicar | "Escribe los cambios al repo. Solo disponible si el run es mergeable y sin risk flags bloqueantes." |
| Botón Deshacer | "Revierte el último apply. Requiere que el run tenga snapshot previo." |
| Steering → Continuar | Muestra el prompt completo que se enviará al agente |
| Metrics → Blocked | "Runs con risk flags que requieren revisión manual antes de aplicar." |
| Badge LIVE | "Job en ejecución — click para expandir el timeline completo." |
| Tab Run | "Controla el run seleccionado: revisa el diff, aplica o deshace cambios." |
| Tab Insights | "Paneles de análisis: Self-Improvement, Risk Map, NEMO Memory, Apply Plan." |

### Props eliminadas de AgentPane

Las siguientes props salen de `AgentPane` porque su contenido se mueve a la sección Settings o se elimina:

`settingsDraft`, `onSettingsChange`, `onSaveSettings`, `repoDraft`, `onRepoDraftChange`, `onOpenRepo`, `cloneDraft`, `onCloneDraftChange`, `onCloneRepo`, `cleanupResult`, `onCleanup`, `orphanCleanupResult`, `onCleanupOrphans`, `onRefreshMcpWatcher`, `showSettingsPanel`

**De ~40 props → ~25 props.**

`RepoSettingsPanel` permanece en la sección Settings de la app (ya existe como `activeSection === "settings"`).

---

## Archivos que cambian

| Archivo | Cambio |
|---|---|
| `src/hooks/usePersistenceStore.ts` | **Nuevo** — centraliza localStorage |
| `src/services/artifactRegistry.ts` | Acepta `isTombstoned` en `mergeArtifactsIntoRegistry` |
| `src/hooks/useGeneratedArtifacts.ts` | Consume `isTombstoned` y `addTombstone` |
| `apps/mission-control/src/main.tsx` | `statusLoaded`, replace localStorage directo, AgentPane props reducidas, skeleton Runs |
| `apps/mission-control/src/styles.css` | `.agent-pane-header`, `.agent-tab-bar`, `.agent-chat-body`, `.agent-chat-footer`, `.agent-metrics-strip`, `.agent-controls-bar`, `.tooltip`, `min-width:0` en grid |

---

## Lo que NO cambia

- Backend Python — cero cambios
- Lógica de polling y fetch — sin tocar
- `artifactUtils.ts`, `planNemoClient.ts`, `usePlanNemoSync.ts` — sin tocar
- Otros paneles (VersioningPanel, TerminalPanel, BrowserPanel, etc.) — sin tocar
- El chat de Home (`MissionHome`) — sin tocar

---

## Testing

- Verificar que borrar un artifact no lo regresa al refrescar (tombstone persiste)
- Verificar que archivar chat limpia la sesión (no regresa al refrescar)
- Verificar que hacer click en Runs no rebota a Home cuando hay runs en el backend
- Verificar que `AgentPane` scrollea correctamente en tab "file" (2 columnas)
- Verificar que los tooltips aparecen en hover con delay y no flashean
- `npx tsc --noEmit` debe pasar limpio
