# Permission System Design — Space Code / Mission Control
**Fecha:** 2026-05-13  
**Estado:** Aprobado — listo para implementación  
**Sprint:** GAP-03 (Phase A: Pre-Run Gate)  
**Siguiente sprint:** GAP-03 Phase B — Mid-Run Signal Protocol (ver sección 7)

---

## 1. Problema

El sistema actual tiene:
- `approval_queue` — lista filtrada del estado, sin enforcement real
- `permission_profile` en `MissionRun` — campo sin lógica
- `AutonomyMode` en frontend — UI sin backend real
- El agente (Aider subprocess) puede escribir cualquier archivo, ejecutar comandos, hacer llamadas de red — sin que el usuario lo sepa ni apruebe

**Objetivo:** Sistema donde el agente no puede lanzarse hasta que el usuario haya aprobado explícitamente las categorías de acción riesgosas que el task requiere. Todo queda auditado en el timeline de la run.

---

## 2. Alcance de esta fase (Phase A)

**En scope:**
- `PermissionAnalyzer` — analiza objetivo + target files → computa `PermissionRequest`
- `PolicyProfile` — reglas de auto-aprobación por modo (freedom / restriction)
- Gate pre-run en `HandoffJobManager` — bloquea el launch hasta aprobación
- Dos endpoints: `POST /api/run/<job_id>/permission-grant` y `/permission-deny`
- `PermissionRequestPanel.tsx` — UI de aprobación en Mission Control
- Audit trail en el timeline de la run (`permission_decision` event)
- Tests para `PermissionAnalyzer` y `PolicyProfile`

**Fuera de scope (Phase B):**
- Mid-run interrupt (señalización del subprocess en mid-ejecución)
- Permission memory en NEMO (recordar decisiones de runs anteriores)
- Perfiles de permiso personalizables por repositorio

---

## 3. Modelo de Datos

### `PermissionCategory`

Cuatro categorías, siempre fijas:

```python
class PermissionCategory(str, Enum):
    FILE_WRITE_OUTSIDE_WORKTREE = "file_write_outside_worktree"
    SHELL_COMMAND                = "shell_command"
    NETWORK_CALL                 = "network_call"
    CONFIG_FILE_WRITE            = "config_file_write"
```

**Definiciones:**
- `file_write_outside_worktree` — el agente intenta modificar archivos fuera del sandbox asignado (`.worktrees/<id>/`)
- `shell_command` — el agente ejecuta un subprocess, script, o comando de sistema
- `network_call` — el agente hace fetch, curl, o llama a APIs externas (no LM Studio, no NEMO)
- `config_file_write` — el agente modifica archivos de configuración críticos del repo: `pyproject.toml`, `.github/**`, `Dockerfile`, `docker-compose*.yml`, `scripts/**`, `*.toml`, `*.cfg`, `*.ini` en el root

### `PermissionMode`

```python
class PermissionMode(str, Enum):
    FREEDOM     = "freedom"      # afloja shell y network; escrituras siempre-ask
    RESTRICTION = "restriction"  # requiere aprobación explícita para todo
```

Mapeo desde `AutonomyMode` del frontend:
- `"aggressive"` → `PermissionMode.FREEDOM`
- `"trusted"` → `PermissionMode.RESTRICTION`
- `"manual"` → `PermissionMode.RESTRICTION`

### `PermissionRequest`

Generado por `PermissionAnalyzer` antes del launch:

```python
@dataclass(frozen=True, slots=True)
class PermissionRequest:
    job_id: str
    categories: tuple[PermissionCategory, ...]         # total de lo que necesita el task
    rationale: str                                     # explicación legible para el usuario
    auto_approved: tuple[PermissionCategory, ...]      # ya pasaron por política
    requires_user_approval: tuple[PermissionCategory, ...]  # bloquean el launch
```

### `PermissionDecision`

Resultado de la decisión del usuario (o de la política automática):

```python
@dataclass(frozen=True, slots=True)
class PermissionDecision:
    decided_at: str    # ISO 8601
    decided_by: str    # "user" | "policy_auto" | FUTURE: "mid_run_signal"
    approved: bool
    categories: tuple[PermissionCategory, ...]
    note: str          # texto libre del usuario, puede ser ""
```

### Timeline Event

En el JSON de la run, evento de tipo `"permission_decision"`:

```json
{
  "kind": "permission_decision",
  "timestamp": "2026-05-13T22:30:00Z",
  "data": {
    "approved": true,
    "decided_by": "user",
    "categories": ["shell_command", "config_file_write"],
    "auto_approved": ["network_call"],
    "note": "aprobado para task de CI"
  }
}
```

El campo `decided_by` es el extension point de Phase B: los valores futuros `"mid_run_signal"` y `"policy_auto_mid_run"` serán interpretados por el mismo código consumidor.

---

## 4. Reglas de Auto-Aprobación

| Categoría | Freedom | Restriction |
|-----------|---------|-------------|
| `file_write_outside_worktree` | ❌ always-ask | ❌ always-ask |
| `shell_command` | ✅ auto | ❌ always-ask |
| `network_call` | ✅ auto | ❌ always-ask |
| `config_file_write` | ❌ always-ask | ❌ always-ask |

Las categorías de escritura (`file_write_outside_worktree`, `config_file_write`) son always-ask en ambos modos. El modo freedom solo afloja shell y network.

Si `requires_user_approval == ()` → launch inmediato sin gate interactivo, se registra `decided_by="policy_auto"` en el timeline.

---

## 5. Arquitectura y Componentes

### `src/nemo_coding_platform/core/permission_engine.py` (nuevo)

Puro Python, sin I/O, 100% testeable:

```python
# Enums: PermissionCategory, PermissionMode
# Dataclasses: PermissionRequest, PermissionDecision
# PolicyProfile: define las reglas por modo
# PermissionAnalyzer: analiza objective + target_files → PermissionRequest

class PermissionAnalyzer:
    def analyze(
        self,
        job_id: str,
        objective: str,
        target_files: tuple[str, ...],
        mode: PermissionMode,
    ) -> PermissionRequest:
        ...
```

Heurísticas del analyzer (basadas en keywords + paths):
- Detecta `shell_command` si objective contiene: `run`, `execute`, `pytest`, `npm`, `pip install`, `script`, `bash`, `cmd`
- Detecta `network_call` si objective contiene: `fetch`, `api`, `http`, `curl`, `request`, `download`, `upload`, `webhook`
- Detecta `config_file_write` si target_files incluye `*.toml`, `.github/**`, `Dockerfile`, `scripts/**`, o si objective contiene `workflow`, `ci`, `dockerfile`, `config`
- Detecta `file_write_outside_worktree` si target_files contiene paths absolutos fuera del repo o `..` escape patterns

### `src/nemo_coding_platform/mission_control_server.py` (modificado)

**En `HandoffJobManager.start_handoff`** — antes de construir el command:

```python
from nemo_coding_platform.core.permission_engine import PermissionAnalyzer, PermissionMode

analyzer = PermissionAnalyzer()
mode = PermissionMode.FREEDOM if payload.get("autonomy_mode") == "aggressive" else PermissionMode.RESTRICTION
request = analyzer.analyze(job_id, objective, tuple(target_files), mode)

if request.requires_user_approval:
    job = HandoffJob(..., status="awaiting_permission", permission_request=request.to_dict())
    self._jobs[job_id] = job
    return job  # no lanza el subprocess

# auto-approved path: registra en timeline y continúa
```

**Dos endpoints nuevos** en `do_POST`:

```
POST /api/run/<job_id>/permission-grant  body: {"note": "..."}
POST /api/run/<job_id>/permission-deny   body: {"note": "..."}
```

`api_permission_grant`: actualiza `job.status = "approved"`, registra `PermissionDecision` en timeline, lanza el subprocess.  
`api_permission_deny`: actualiza `job.status = "permission_denied"`, registra decisión, no lanza.

**`HandoffJob`** — campo nuevo:
```python
permission_request: dict | None = None  # serialización de PermissionRequest.to_dict()
```
`HandoffJob.to_dict()` ya serializa todos los campos a JSON para persistencia en disco — `permission_request` es un dict plano, compatible sin cambios adicionales.

### `apps/mission-control/src/components/PermissionRequestPanel.tsx` (nuevo)

Aparece en `AgentPane` cuando `selectedJob?.status === "awaiting_permission"`.

```tsx
interface Props {
  job: HandoffJob;
  onGrant: (note: string) => void;
  onDeny: (note: string) => void;
}
```

Layout:

```
┌─────────────────────────────────────────────────────┐
│  ⚠  Permiso requerido antes de ejecutar             │
│                                                     │
│  Task: "<objective truncado a 80 chars>"            │
│  Razón: <rationale del PermissionRequest>           │
│                                                     │
│  Requiere aprobación:                               │
│  [⚙ config_file_write]  [🌐 network_call]           │
│                                                     │
│  Auto-aprobadas (política):                         │
│  [▶ shell_command]                                  │
│                                                     │
│  Nota (opcional): ________________________          │
│                                                     │
│  [ Aprobar y lanzar ]        [ Denegar ]            │
└─────────────────────────────────────────────────────┘
```

**Wiring en `main.tsx`:** junto a `selectedJob`, computar `jobAwaitingPermission`. En `AgentPane`, si `jobAwaitingPermission`, mostrar `<PermissionRequestPanel>` con handlers que hacen fetch a los endpoints grant/deny.

---

## 6. Tests (`tests/test_permission_engine.py`)

| Test | Qué verifica |
|------|-------------|
| `test_config_write_always_asks_in_both_modes` | objetivo con ".github/workflows" → `config_file_write` en `requires_user_approval` en freedom Y restriction |
| `test_shell_auto_in_freedom_ask_in_restriction` | objetivo con "run pytest" → auto en freedom, ask en restriction |
| `test_network_auto_in_freedom_ask_in_restriction` | objetivo con "fetch API" → auto en freedom, ask en restriction |
| `test_no_risky_actions_empty_requires` | "add docstring to function" → `requires_user_approval == ()` |
| `test_file_outside_worktree_always_asks` | target_files con `../../etc/passwd` → always-ask |
| `test_permission_decision_serializable` | `PermissionDecision` → `to_dict()` → JSON round-trip |
| `test_auto_approved_path_records_policy_auto` | cuando requires vacío → `decided_by == "policy_auto"` |

---

## 7. Phase B — Mid-Run Signal Protocol (próximo sprint)

Documentado aquí para que Phase A deje los extension points correctos.

**Mecanismo previsto:**
1. `nemo_code_runtime` escribe `.nemo-permission-request.json` en el worktree cuando detecta que necesita realizar una acción riesgosa.
2. El supervisor (headless_runner o una nueva `PermissionPoller` goroutine) hace polling de ese archivo durante la ejecución del subprocess.
3. Al detectarlo: señaliza SIGSTOP al proceso Aider, construye un `PermissionRequest` con `decided_by="mid_run_signal"`, lo surfacea via los mismos endpoints `/permission-grant` y `/permission-deny`.
4. El usuario responde desde Mission Control (mismo `PermissionRequestPanel`, sin cambios de UI).
5. En grant: escribe `.nemo-permission-decision.json` en el worktree, envía SIGCONT al proceso.
6. En deny: mata el proceso, registra `PermissionDecision(approved=False, decided_by="mid_run_signal")` en timeline.

**Extension points ya en Phase A:**
- `decided_by: str` (no enum) → acepta `"mid_run_signal"` sin cambios
- `PermissionRequest.to_dict()` / `PermissionDecision.to_dict()` — mismos formatos
- Signal file path: `WorktreeRuntimeSpec.worktree_path / ".nemo-permission-request.json"` (constante ya nombrada en `permission_engine.py`)
- Placeholder comentado en `permission_engine.py` (ver sección de código)

**Lo que requiere Phase B que no existe hoy:**
- Cambios en `nemo_code_runtime` para emitir el signal file
- `PermissionPoller` (nuevo módulo o método en `headless_runner`)
- Manejo SIGSTOP/SIGCONT en `SubprocessEngineProvider` (Windows: `subprocess.Popen.suspend()/resume()` via psutil)
- Tests de integración con subprocess real

---

## 8. Archivos Modificados / Creados

| Archivo | Cambio |
|---------|--------|
| `src/nemo_coding_platform/core/permission_engine.py` | Nuevo — enums, dataclasses, `PermissionAnalyzer`, `PolicyProfile` |
| `src/nemo_coding_platform/mission_control_server.py` | Gate pre-run en `start_handoff`, 2 endpoints grant/deny, `HandoffJob.permission_request` |
| `apps/mission-control/src/components/PermissionRequestPanel.tsx` | Nuevo componente |
| `apps/mission-control/src/styles.css` | CSS `PermissionRequestPanel` |
| `apps/mission-control/src/main.tsx` | Import + `jobAwaitingPermission` memo + wiring en AgentPane |
| `tests/test_permission_engine.py` | 7 tests nuevos |

---

## 9. Verificación

```powershell
python -m pytest tests/test_permission_engine.py -v   # → 7/7 ✅
npx tsc --noEmit  # apps/mission-control/              # → ✅ sin errores
```

Smoke test manual:
1. Lanzar handoff con objective "añadir CI workflow a .github/" en restriction mode
2. Verificar job status = `"awaiting_permission"` en `/api/state`
3. `PermissionRequestPanel` visible en Mission Control con `config_file_write` como categoría
4. Click "Aprobar y lanzar" → job arranca, timeline tiene evento `permission_decision`
5. Lanzar mismo task en freedom mode → sin gate, `decided_by="policy_auto"` en timeline
