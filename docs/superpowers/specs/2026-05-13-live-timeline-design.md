# Live Timeline — Space Code / Mission Control
**Fecha:** 2026-05-13
**Estado:** Aprobado — listo para implementación
**Sprint:** GAP-04 (Live Timeline)
**Siguiente sprint:** GAP-SDD — Orquestación SDD en el agente

---

## 1. Problema

El sistema actual expone los runs del agente como `HandoffJob` con:
- `status: str` — string crudo sin state machine
- `logs: list[str]` — texto plano del subprocess
- `run_json` — path al resultado final, escrito solo al terminar el run

El frontend no puede mostrar qué está haciendo el agente mientras corre. La timeline estructurada (con `AppendOnlyTimeline` de `task_run.py`) existe en el proceso hijo pero no llega al servidor ni a la UI.

**Objetivo:** Surfacear eventos tipados en tiempo real desde el runner hacia Mission Control, via SSE, sin cambiar el mecanismo de ejecución existente.

---

## 2. Alcance

**En scope:**
- `HandoffJobStatus` enum (reemplaza `status: str` crudo)
- `HandoffJob.timeline: list[dict]` — eventos acumulados en memoria durante el run
- Protocolo `NEMO_EVENT:` en stdout del subprocess
- `event_emitter.py` — helper de emisión en el runner
- Puntos de emisión en `headless_runner.py` y `long_handoff_supervisor.py`
- `GET /api/run/<job_id>/timeline` — snapshot REST
- `GET /api/run/<job_id>/timeline/stream` — SSE live stream
- `TimelinePanel.tsx` + CSS + wiring en `main.tsx`
- Tests para el parsing del protocolo y el endpoint REST

**Fuera de scope:**
- Cambios en la lógica de ejecución del runner (eso es GAP-SDD)
- Persistencia de timeline en disco durante el run (solo al completar)
- Replay de runs históricos desde `run_json`
- Cambios en `AppendOnlyTimeline` (se usa solo como referencia de `EventKind`)

---

## 3. Protocolo `NEMO_EVENT:`

El runner escribe a stdout una línea que comienza con el prefijo literal `NEMO_EVENT:` seguida de JSON compacto. El resto de líneas son logs crudos (comportamiento actual sin cambios).

### Schema del evento

```json
{
  "kind":     "mutation_created",
  "summary":  "Editado src/foo.py (+42 líneas)",
  "phase":    "execute",
  "sequence": 3,
  "ts":       "2026-05-13T22:03:12Z",
  "payload":  { "files": ["src/foo.py"] }
}
```

| Campo | Tipo | Requerido | Descripción |
|-------|------|-----------|-------------|
| `kind` | string | ✅ | Valor de `EventKind` (`plan_created`, `mutation_created`, etc.) |
| `summary` | string | ✅ | Texto legible para el usuario |
| `phase` | string | ✅ | `"plan"` \| `"execute"` \| `"review"` |
| `sequence` | int | ✅ | Monotónico por proceso, empieza en 1 |
| `ts` | string | ✅ | ISO 8601 UTC |
| `payload` | dict | ❌ | Datos extra opcionales (archivos, score, etc.) |

### Invariantes del protocolo

- El prefijo es exactamente `NEMO_EVENT:` (10 caracteres, case-sensitive)
- Una línea por evento — no hay eventos multi-línea
- El servidor ignora silenciosamente cualquier línea `NEMO_EVENT:` cuyo JSON no parsea (JSON malformado no rompe el run)
- El campo `sequence` no es validado por el servidor (se acepta cualquier valor entero)

---

## 4. `HandoffJobStatus` enum

```python
class HandoffJobStatus(StrEnum):
    STARTING            = "starting"
    AWAITING_PERMISSION = "awaiting_permission"
    RUNNING             = "running"
    COMPLETED           = "completed"
    FAILED              = "failed"
    PERMISSION_DENIED   = "permission_denied"
    ORPHANED            = "orphaned"
    CANCELLED           = "cancelled"
```

**Archivo:** `src/nemo_coding_platform/mission_control_server.py`, junto a las otras constantes de módulo.

Como `StrEnum`, `job.status == "running"` sigue siendo `True` — no hay breaking changes en código existente que compare contra strings literales.

`HandoffJob.status` cambia de `str` a `HandoffJobStatus`. Los lugares donde se asigna usan el enum directamente: `job.status = HandoffJobStatus.RUNNING`.

---

## 5. `HandoffJob` — campo `timeline`

```python
@dataclass(slots=True)
class HandoffJob:
    # ... campos existentes ...
    timeline: list[dict[str, object]] = field(default_factory=list)
```

### `to_dict()` — incluir timeline

```python
def to_dict(self, *, include_logs: bool = True) -> dict[str, object]:
    payload = { ...campos existentes... }
    if include_logs:
        payload["logs"] = list(self.logs)
    payload["timeline"] = list(self.timeline)
    return payload
```

### `from_snapshot()` — cargar timeline

```python
@classmethod
def from_snapshot(cls, payload: dict[str, Any]) -> "HandoffJob":
    return cls(
        ...campos existentes...,
        timeline=list(payload.get("timeline") or []),
    )
```

### Persistencia en disco

La timeline se incluye en el snapshot JSON solo cuando `status` es terminal (`completed`, `failed`, `permission_denied`, `cancelled`). Durante el run, el snapshot en disco no incluye `timeline` (se omite en `_persist_job` para runs activos) para evitar writes frecuentes. Al finalizar el run, `_persist_job` es llamado una vez con la timeline completa.

---

## 6. Cambios en el servidor

### `NEMO_EVENT_PREFIX` — constante de módulo

```python
NEMO_EVENT_PREFIX = "NEMO_EVENT:"
```

### `HandoffJobManager._run_job` — parsing de stdout

El loop de lectura de stdout ya existe. Se agrega detección del prefijo antes de append a logs:

```python
for raw_line in process.stdout:
    line = raw_line.rstrip("\n")
    if line.startswith(NEMO_EVENT_PREFIX):
        try:
            event = json.loads(line[len(NEMO_EVENT_PREFIX):])
            with self._lock:
                job.timeline.append(event)
                job.updated_at = datetime.now(timezone.utc).isoformat()
        except (json.JSONDecodeError, ValueError):
            pass  # línea malformada — ignorar, no interrumpir el run
    else:
        with self._lock:
            if len(job.logs) < JOB_LOG_LIMIT:
                job.logs.append(line)
```

### `api_run_timeline` — endpoint REST

```python
def api_run_timeline(config: MissionControlServerConfig, job_id: str) -> dict:
    job = _get_handoff_job(config, job_id)
    if not job:
        return {"error": f"job not found: {job_id}"}
    return {
        "job_id": job_id,
        "status": str(job.status),
        "timeline": list(job.timeline),
        "event_count": len(job.timeline),
    }
```

Ruta: `GET /api/run/<job_id>/timeline` en `do_GET`, mismo patrón que `/worktree-diff`.

### `_handle_timeline_sse` — SSE live stream

```python
def _handle_timeline_sse(
    config: MissionControlServerConfig,
    job_id: str,
    send_response,   # callable que escribe HTTP response headers
    send_event,      # callable que escribe un SSE data line
) -> None:
    job = _get_handoff_job(config, job_id)
    if not job:
        send_response(404, {"error": "job not found"})
        return

    send_response(200, content_type="text/event-stream")
    offset = 0
    POLL_INTERVAL = 0.5  # segundos

    while True:
        with _handoff_job_manager._lock:
            current_job = _handoff_job_manager._jobs.get(job_id)
            if current_job is None:
                break
            events_slice = current_job.timeline[offset:]
            current_status = str(current_job.status)

        for event in events_slice:
            send_event(json.dumps(event))
            offset += 1

        terminal = current_status in ("completed", "failed", "permission_denied", "cancelled", "orphaned")
        if terminal and not events_slice:
            send_event(json.dumps({"kind": "stream_end", "status": current_status}))
            break

        time.sleep(POLL_INTERVAL)
```

Ruta: `GET /api/run/<job_id>/timeline/stream` — detectada en `do_GET` por regex, igual que las rutas dinámicas existentes.

---

## 7. `event_emitter.py` (nuevo módulo)

**Archivo:** `src/nemo_coding_platform/core/event_emitter.py`

```python
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

_seq: int = 0


def emit_event(kind: str, summary: str, phase: str, payload: dict | None = None) -> None:
    """Write a NEMO_EVENT line to stdout for the Mission Control server to parse."""
    global _seq
    _seq += 1
    event: dict[str, object] = {
        "kind": kind,
        "summary": summary,
        "phase": phase,
        "sequence": _seq,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    if payload:
        event["payload"] = payload
    print(f"NEMO_EVENT:{json.dumps(event, separators=(',', ':'))}", flush=True)


def reset_sequence() -> None:
    """Reset sequence counter — call at start of each run (tests)."""
    global _seq
    _seq = 0
```

`flush=True` es crítico — sin él los eventos quedan en el buffer del proceso y no llegan al servidor en tiempo real.

---

## 8. Puntos de emisión en el runner

Los puntos de emisión se agregan con llamadas a `emit_event(...)`. No cambia la lógica existente.

### `headless_runner.py`

| Punto | `kind` | `phase` | `payload` ejemplo |
|-------|--------|---------|-------------------|
| Después de NEMO `context_bootstrap` exitoso | `context_bootstrapped` | `plan` | `{"nemo_url": "..."}` |
| Al construir el plan de ejecución | `plan_created` | `plan` | `{"objective": "..."}` |
| Después de cada mutación de código | `mutation_created` | `execute` | `{"files": [...]}` |
| Después de cada validación | `validation_run` | `execute` | `{"passed": true, "output": "..."}` |
| Al crear un checkpoint | `checkpoint` | `execute` | `{"checkpoint_path": "..."}` |
| Al crear review package | `review_package_created` | `review` | `{"path": "..."}` |
| Al terminar exitosamente | `heartbeat` | `review` | `{"final": true, "grade": "..."}` |

### `long_handoff_supervisor.py`

| Punto | `kind` | `phase` | `payload` ejemplo |
|-------|--------|---------|-------------------|
| Al inicio de cada iteración del supervisor | `heartbeat` | `execute` | `{"iteration": N, "elapsed_minutes": X}` |
| Al pausar (timeout o budget) | `paused` | `execute` | `{"reason": "timeout"}` |
| Al resumir desde checkpoint | `resumed` | `execute` | `{"resume_token": "..."}` |

---

## 9. Frontend — `TimelinePanel.tsx`

**Archivo:** `apps/mission-control/src/components/TimelinePanel.tsx` (nuevo)

```tsx
interface TimelineEvent {
  kind: string;
  summary: string;
  phase: string;
  sequence: number;
  ts: string;
  payload?: Record<string, unknown>;
}

interface Props {
  jobId: string;
  isLive: boolean;     // true cuando job.status === "running"
  onStreamEnd?: () => void;
}
```

**Comportamiento:**
- `isLive=true` → abre SSE a `/api/run/<jobId>/timeline/stream`, acumula eventos en state local, auto-scroll al último
- `isLive=false` → fetch REST a `/api/run/<jobId>/timeline` una sola vez (job completado)
- Al recibir evento `kind="stream_end"` → cierra el SSE, llama `onStreamEnd()`
- Cleanup: `useEffect` retorna función que cierra el `EventSource`

**Icono por `kind`:**

| `kind` | Icono |
|--------|-------|
| `context_bootstrapped` | `🧠` |
| `plan_created` | `📋` |
| `mutation_created` | `⚙` |
| `validation_run` | `✓` / `✗` según `payload.passed` |
| `checkpoint` | `💾` |
| `review_package_created` | `📦` |
| `heartbeat` | `◉` |
| `paused` | `⏸` |
| `resumed` | `▶` |
| `permission_decided` | `🔒` |
| `stream_end` | — (no se renderiza) |

**Layout:**

```
┌─────────────────────────────────────────────────────┐
│  Timeline                              ● Live        │
│  ──────────────────────────────────────────────────  │
│  📋 plan    plan_created    Plan: 3 tareas           │
│             22:01:45                                 │
│  ⚙  execute mutation_created  Editado src/foo.py    │
│             22:03:12  ·  src/foo.py                  │
│  ✓  execute validation_run  Tests: 5/5               │
│             22:04:01                                 │
│  ◉  execute heartbeat  Iteración 2/3…  ●            │
│             22:04:30                                 │
└─────────────────────────────────────────────────────┘
```

### Wiring en `main.tsx`

```tsx
// Memo junto a jobAwaitingPermission:
const jobRunning = useMemo(
  () => state.jobs.find((j) => j.status === "running") ?? null,
  [state.jobs]
);
```

En `AgentPane`:
- Si `jobAwaitingPermission` → muestra `PermissionRequestPanel` (ya existe)
- Si `jobRunning` → muestra `TimelinePanel` con `isLive={true}`
- Si job completado seleccionado → muestra `TimelinePanel` con `isLive={false}`

`AgentPaneProps` extiende con `runningJob?: HandoffJob | null`.

---

## 10. CSS (nuevo, append a `styles.css`)

```css
.timeline-panel { display: flex; flex-direction: column; gap: 0; border: 1px solid #30363d; border-radius: 6px; overflow: hidden; }
.timeline-panel-header { display: flex; align-items: center; justify-content: space-between; padding: 8px 12px; background: #161b22; border-bottom: 1px solid #30363d; font-size: 11px; color: #8b949e; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; }
.timeline-live-dot { width: 7px; height: 7px; border-radius: 50%; background: #f85149; display: inline-block; animation: pulse-dot 1.2s ease-in-out infinite; }
@keyframes pulse-dot { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }
.timeline-events { display: flex; flex-direction: column; max-height: 380px; overflow-y: auto; }
.timeline-event { display: grid; grid-template-columns: 20px 56px 1fr; gap: 6px; align-items: start; padding: 7px 12px; border-bottom: 1px solid #21262d; font-size: 11px; }
.timeline-event:last-child { border-bottom: none; }
.timeline-event-icon { color: #8b949e; font-size: 12px; padding-top: 1px; }
.timeline-phase-badge { display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; }
.timeline-phase-badge.plan    { background: #1c2d4a; color: #79c0ff; }
.timeline-phase-badge.execute { background: #1a2d1a; color: #56d364; }
.timeline-phase-badge.review  { background: #2d1a3a; color: #d2a8ff; }
.timeline-event-body { display: flex; flex-direction: column; gap: 2px; }
.timeline-event-summary { color: #e6edf3; line-height: 1.4; }
.timeline-event-meta { color: #8b949e; font-size: 10px; }
.timeline-empty { padding: 24px; text-align: center; color: #8b949e; font-size: 12px; }
```

---

## 11. Tests

**`tests/test_live_timeline.py`** (nuevo)

| Test | Qué verifica |
|------|-------------|
| `test_nemo_event_prefix_detected` | Línea `NEMO_EVENT:{...}` → event en `job.timeline`, resto en `job.logs` |
| `test_malformed_json_ignored` | `NEMO_EVENT:not-json` → no rompe el run, timeline vacío |
| `test_event_fields_preserved` | Todos los campos del JSON se preservan en `job.timeline[0]` |
| `test_api_run_timeline_not_found` | Job inexistente → `{"error": "..."}` |
| `test_api_run_timeline_returns_events` | Job con timeline → `{"timeline": [...], "event_count": N}` |
| `test_handoff_job_status_enum` | `HandoffJobStatus.RUNNING == "running"` → `True` (StrEnum) |
| `test_emit_event_writes_prefix` | `emit_event(...)` escribe `NEMO_EVENT:` a stdout con todos los campos |
| `test_emit_event_sequence_increments` | Llamadas consecutivas incrementan `sequence` |
| `test_reset_sequence` | `reset_sequence()` vuelve `sequence` a 0 |

---

## 12. Archivos modificados / creados

| Archivo | Cambio |
|---------|--------|
| `src/nemo_coding_platform/core/event_emitter.py` | Nuevo — `emit_event`, `reset_sequence` |
| `src/nemo_coding_platform/mission_control_server.py` | `HandoffJobStatus` enum; `HandoffJob.timeline`; parsing NEMO_EVENT en `_run_job`; `api_run_timeline`; `_handle_timeline_sse`; rutas en `do_GET` |
| `src/nemo_coding_platform/core/headless_runner.py` | Import `emit_event`; 7 puntos de emisión |
| `src/nemo_coding_platform/core/long_handoff_supervisor.py` | Import `emit_event`; 3 puntos de emisión |
| `apps/mission-control/src/components/TimelinePanel.tsx` | Nuevo componente |
| `apps/mission-control/src/styles.css` | CSS `TimelinePanel` |
| `apps/mission-control/src/main.tsx` | `jobRunning` memo; `TimelinePanel` en AgentPane |
| `tests/test_live_timeline.py` | 9 tests nuevos |

---

## 13. Verificación

```powershell
python -m pytest tests/test_live_timeline.py -v   # → 9/9 ✅
npx tsc --noEmit  # apps/mission-control/          # → ✅ sin errores
```

Smoke test manual:
1. Lanzar un handoff con objetivo real (modo subprocess)
2. Verificar `GET /api/run/<id>/timeline` → `{"timeline": [...], "event_count": N}`
3. Abrir Mission Control → job running → `TimelinePanel` visible con `● Live`
4. Eventos aparecen en tiempo real mientras el run corre
5. Al completar → badge `● Live` desaparece, timeline queda estática
