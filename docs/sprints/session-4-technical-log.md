# Session 4 — Technical Log
**Fecha:** 2026-05-13  
**Rama:** claudeworks  
**Alcance:** Stop/Steer para el plan loop + limpieza completa del frontend (eliminación de mock/decorativo)

---

## 1. Stop/Steer Plan Loop Control

### Problema
El plan loop SSE corría hasta completarse o fallar sin ningún mecanismo de interrupción. Una vez lanzado, el usuario solo podía cerrar el tab.

### Solución implementada

**Backend — `mission_control_server.py`**

Control plane a nivel de módulo:
```python
_plan_jobs: dict[str, dict[str, object]] = {}
```

El handler SSE (`_handle_plan_sse`) genera un UUID antes de llamar al generador:
```python
plan_job_id = f"plan-{uuid4().hex[:12]}"
_plan_jobs[plan_job_id] = {"cancel": False, "steer": None}
try:
    for event in api_agent_plan_gen(..., job_id=plan_job_id):
        ...
finally:
    _plan_jobs.pop(plan_job_id, None)
```

El generador `api_agent_plan_gen` verifica al inicio de cada iteración:
```python
if job_id:
    _ctrl = _plan_jobs.get(job_id, {})
    if _ctrl.get("cancel"):
        yield {"type": "cancelled", "job_id": job_id, ...}
        _plan_jobs.pop(job_id, None)
        return
    _steer_directive = str(_ctrl.get("steer") or "")
    if _steer_directive:
        _ctrl["steer"] = None  # consume atómicamente
```

Endpoints nuevos en `do_POST`:
```
POST /api/agent/plan/cancel  {"job_id": "plan-xxxx"}
POST /api/agent/plan/steer   {"job_id": "plan-xxxx", "directive": "usa numpy no pandas"}
```

Funciones: `api_plan_cancel(config, payload)`, `api_plan_steer(config, payload)`.

**Frontend — `main.tsx`**

`AgentAction.kind` extendido con `"plan_cancel" | "plan_steer"`.

Cuando llega evento SSE `start` con `job_id` válido, se inyectan dos botones en el mensaje del plan activo:
```typescript
setAgentMessages((prev) => prev.map((m) =>
  m.id === planMsgId ? { ...m, actions: [
    { id: `stop-${planJobId}`, kind: "plan_cancel", label: "Stop", ... },
    { id: `steer-${planJobId}`, kind: "plan_steer", label: "Steer", ... },
  ]} : m
));
```

Al llegar `done` o `cancelled`: `actions: []` limpia los botones.

`plan_steer` usa `window.prompt()` — sin estado extra, sin modal.

**CSS — `styles.css`**
```css
.mission-action-btn.plan_cancel { border-color: #b91c1c; background: #200a0a; color: #fca5a5; }
.mission-action-btn.plan_cancel:hover { border-color: #ef4444; background: #2d1010; }
.mission-action-btn.plan_steer { border-color: #b45309; background: #1a1000; color: #fbbf24; }
.mission-action-btn.plan_steer:hover { border-color: #f59e0b; background: #251800; }
```

---

## 2. Frontend Cleanup — Audit y Limpieza

Se realizó un audit completo de congruencia del frontend. Resultado: múltiples capas de datos falsos mezclados con datos reales sin indicación visual.

### 2.1 ArtifactWorkbench.tsx

**Eliminado:**
- `ArtifactPerformanceSnapshot` type
- `artifactPerformanceSnapshot()` — seed math para FPS/CPU/GPU falsos
- `artifactSourceExcerpt()` / `artifactStandbySourceExcerpt()` — solo usadas por ArtifactRuntimeDeck
- `ArtifactRuntimeDeck` — tabs estáticos (Code/Scene/Assets/Profiler/Terminal) + barras CPU/GPU falsas
- `ArtifactViewportActions` — 4 botones (Expand/Capture/Center/More) sin efecto real
- `ARTIFACT_VIEWPORT_STATUS` constant
- `viewportAction` state, `handleViewportAction`, `viewportStatus`
- Escena 3D del `ArtifactStandbyCanvas` (torres, beam, avatar, target, reflection, HUD)
- Imports: `Activity`, `Camera`, `Crosshair`, `MoreHorizontal`

**Añadido:**
- `FullscreenButton` — usa Browser Fullscreen API real:
  - `useRef<HTMLDivElement>` apuntando a `.artifact-stage`
  - `requestFullscreen()` / `exitFullscreen()`
  - Sincroniza estado con `document.addEventListener("fullscreenchange", ...)`
  - Icono cambia entre `<Maximize2>` y `<Minimize2>` según estado
- `ArtifactStandbyCanvas` minimalista:
  - `<Puzzle size={32} />` + "Artifact Studio" + descripción
  - CSS: `.artifact-standby-placeholder`
- `artifact-live-hud` ahora muestra `activeArtifact.kind` + recuento de líneas reales

### 2.2 TelemetryColumn.tsx

**Eliminado:**
- `sparkValues(seed: number): number[]` — 14 barras con altura calculada por seed
- `const runSeed = totalRuns + readyRuns * 3 + ...`
- `<div className="telemetry-sparkline">` con sus `<i>` de barras

**Refactorizado:**
- "Handoff chain" (Planner/Engineer/Tester/Deploy — 4 agentes ficticios) → "Pipeline" con 4 etapas reales:
  - Plan / Execute / Review / Apply
  - Estado calculado desde `phaseTelemetry.tone`: `complete` (índice < actual), `active` (coincide), `""` (waiting)
  - Muestra `phaseTelemetry.detail` en la etapa activa, "waiting" o "complete" en el resto

### 2.3 main.tsx

**Eliminado:**
- Bloque `ops-panel` "Próximo incremento" de `VersioningPanel` — roadmap hardcodeado como UI

**Reemplazado:**
- `<select>` de Provider en `HandoffComposer` (1 única opción "Space Code + LM Studio") → `<div className="composer-static-field"><span>Provider</span><code>Space Code + LM Studio</code></div>`
- `provider` eliminado de `fieldIds` en `HandoffComposer`; sigue en el draft internamente con valor `"subprocess"`

### 2.4 styles.css

**Eliminado (~200 líneas de CSS dead code):**
```
.artifact-viewport-actions, .artifact-viewport-actions button
.artifact-standby-scene, ::before, ::after
.artifact-scene-beam, .artifact-scene-horizon
.artifact-scene-tower, .artifact-scene-tower::after, .tower-a/b/c/d
.artifact-scene-avatar, span, i
.artifact-scene-target, span
.artifact-scene-reflection
.artifact-standby-hud, span, strong, small
.artifact-standby-metrics, span
.artifact-runtime-deck
.artifact-runtime-tabs, span, span.active
.artifact-code-preview, div, span, code
.artifact-performance-panel, header, div, span, b, i, em
.artifact-empty .artifact-runtime-deck
.telemetry-sparkline
.telemetry-sparkline i
```

**Añadido:**
```css
.section-home .cockpit-topbar { display: none; }
.artifact-fullscreen-btn { ... }
.artifact-fullscreen-btn:hover { ... }
.artifact-standby-placeholder { display: flex; flex-direction: column; align-items: center; ... }
.artifact-standby-placeholder strong { ... }
.artifact-standby-placeholder span { ... }
.composer-static-field { color: #8b949e; display: grid; gap: 5px; }
.composer-static-field code { ... }
```

---

## 3. Verificación Final

```
npx tsc --noEmit  →  ✅ sin errores (salida vacía = limpio)
```

Todos los cambios son puramente frontend (TypeScript/CSS). No se tocó `mission_control_server.py` en esta fase.

**Reinicio requerido:** el backend Python (`mission_control_server.py`) debe reiniciarse para que Stop/Steer esté activo (los endpoints `/api/agent/plan/cancel` y `/api/agent/plan/steer` se añadieron en sesión 3 de este ciclo).

---

## 4. Archivos Modificados

| Archivo | Tipo de cambio |
|---------|---------------|
| `src/nemo_coding_platform/mission_control_server.py` | Stop/Steer control plane, endpoints `/cancel` y `/steer` |
| `apps/mission-control/src/main.tsx` | AgentAction kinds, Stop/Steer handlers, Provider estático, sin "Próximo incremento" |
| `apps/mission-control/src/components/ArtifactWorkbench.tsx` | Reescritura completa — eliminar mock, añadir Fullscreen real |
| `apps/mission-control/src/components/TelemetryColumn.tsx` | Eliminar sparkline, Pipeline real |
| `apps/mission-control/src/styles.css` | ~200 líneas eliminadas, nuevas clases funcionales añadidas |
