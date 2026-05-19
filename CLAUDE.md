# Space Code / Mission Control — Guía de Contexto para Claude

**Última actualización: 2026-05-13 (sprint Worktrees + Quality Core)**

Este archivo resume la arquitectura, convenciones críticas, errores pasados y estado actual del repo. Leerlo ANTES de tocar cualquier cosa. Si Claude pierde contexto y vuelve, este archivo es el punto de recuperación.

---

## PROTOCOLO DE INICIO DE SESIÓN (OBLIGATORIO)

Antes de responder cualquier pregunta técnica, verificar el estado real del sistema con estos comandos:

```powershell
# 1. Backend vivo?
curl http://127.0.0.1:8787/api/health

# 2. Frontend vivo?
curl http://127.0.0.1:5173 -I

# 3. NEMO MCP vivo?
curl http://127.0.0.1:8765/mcp/sse -H "Accept: text/event-stream" -I

# 4. LM Studio modelo activo?
curl http://localhost:1234/v1/models | python -m json.tool
```

**NO asumir que algo está corriendo. Verificar antes de afirmar.**

---

## GUARDS ANTI-PÉRDIDA DE CONTEXTO

### ❌ NUNCA asumir el estado de los procesos
Si la conversación se reinicia o el contexto se comprime, verificar SIEMPRE el estado real de puertos 8787, 5173, 8765 y 1234 antes de actuar.

### ❌ NUNCA decir "el frontend no está corriendo" sin verificar los puertos
El frontend `apps/mission-control/` corre como Vite dev server en puerto **5173** (React/TypeScript). Verificado activo: 2026-05-13. Si el puerto no responde, el usuario lo puede arrancar con `start-mvp-local.ps1` o `npm run dev` en `apps/mission-control/`.

### ❌ NUNCA decir "NEMO no está disponible" sin verificar el puerto 8765
NEMO MCP SSE corre en puerto **8765** (PID 28048 en 2026-05-13). Es el VS Code extension NEMO. También disponible como `stdio://vscode/nemo` cuando VS Code está abierto.

### ❌ NUNCA abrir instancias extra del servidor o frontend
Si el puerto ya responde, el proceso YA ESTÁ corriendo. No abrir instancias duplicadas.

### ✅ PUEDO  arrancar el backend Python desde Claude
`Start-Process` mata el proceso hijo inmediatamente (sin consola). arranca con `start-mvp-local.ps1`.

### ✅ PUEDO arrancar el frontend Vite desde Claude
`npm run dev` en `apps/mission-control/` SÍ funciona como proceso background desde Claude (Bash con `run_in_background`). Validado 2026-05-13.

---

## Estado Verificado — 2026-05-13

| Componente | Puerto | Estado | Notas |
|---|---|---|---|
| Backend Python | 8787 | ✅ UP | Arrancado por el usuario con `start-mvp-local.ps1` |
| Frontend Vite (Mission Control) | 5173 | ✅ UP | React/Vite en `apps/mission-control/` |
| NEMO MCP SSE | 8765 | ✅ UP | PID 28048, VS Code extension |
| LM Studio | 1234 | ✅ UP | Modelo activo: `opus4.7-gods.ghost.codex-4b.gguf` |
| Reranker bge-reranker-v2-m3 | 8080 | ✅ UP | llama-server, parte del pipeline RAG de NEMO |
| SSE `/api/agent/plan` | — | ✅ Verificado | status=200, content-type=text/event-stream, primer evento `{type:"start"}` confirmado desde browser |

---

## Arquitectura General

```
Frontend React/Vite (port 5173)
  apps/mission-control/
  └── /api/* → proxy a backend (vite.config.ts)

Backend Python (port 8787)
  src/nemo_coding_platform/
  └── mission_control_server.py   ← ARCHIVO CENTRAL, >5000 líneas
  └── cli.py                      ← entrypoint CLI

NEMO MCP — DOS MODOS:
  stdio://vscode/nemo              ← cuando VS Code está corriendo (rápido)
  http://127.0.0.1:8765/mcp/sse   ← SSE standalone (siempre disponible)

LM Studio (port 1234)
  OpenAI-compatible API
  Modelo auto-detectado con _resolve_lmstudio_model()
```

El backend NO sirve archivos estáticos del frontend. El frontend tiene su propio servidor Vite en 5173.

---

## Cómo Arrancar el Sistema (ÚNICA FORMA CORRECTA)

```powershell
.\scripts\start-mvp-local.ps1 [-ApiPort 8787] [-UiPort 5173] [-NemoMcpUrl "stdio://vscode/nemo"]
```

O en partes:
```powershell
# Backend solo (DEJAR AL USUARIO — Claude no puede arrancarlo con Start-Process)
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m nemo_coding_platform mission-control-server `
    --repo . --runtimes .spacecode-runtimes --port 8787

# Frontend (Claude SÍ puede arrancarlo en background)
cd apps/mission-control && npm run dev -- --port 5173
```

El script `start-mvp-local.ps1` hace:
1. Detecta Python (.venv → env var → PATH)
2. Arranca backend con `--runtimes .spacecode-runtimes`
3. Arranca frontend con `npm run dev`
4. Valida que NEMO MCP responda

---

## Settings — Archivo Autoritativo

**`.spacecode-runtimes/mission-control/settings.json`** — ES EL QUE USA EL SERVIDOR.

El servidor se arranca con `--runtimes .spacecode-runtimes`, por lo tanto lee ESTE archivo, no `.nemo-runtimes/`.

Campos clave:
- `nemo_mcp_url`: `"stdio://vscode/nemo"` — URL del NEMO MCP
- `default_model`: puede estar desactualizado — ignorar, usar `_resolve_lmstudio_model()`
- `model_base_url`: `"http://localhost:1234/v1"` — LM Studio

`.nemo-runtimes/mission-control/settings.json` tiene `nemo_mcp_url: http://127.0.0.1:8765/mcp/sse` — usado en contextos sin VS Code. **No es el que lee el servidor normal.**

---

## NEMO MCP — REGLA DE ORO: NO TOCAR

El sistema NEMO MCP costó mucho tiempo estabilizar. Es determinista, rápido y correcto.

- `stdio://vscode/nemo` → responde rápido cuando VS Code está corriendo.
- `http://127.0.0.1:8765/mcp/sse` → SSE standalone, siempre disponible.
- `_nemo_chat_tool_call` ya maneja skip cuando `memory_db is None`.
- **NO** agregar `try/except` alrededor de llamadas `_nemo()` — rompe el comportamiento determinista.
- **NO** cambiar `_require_nemo_mcp_url`, `mcp_call_nemo_tool`, ni nada del stack MCP.
- `_nemo_chat_tool_call` para search_memories en el plan loop SÍ puede estar en try/except porque es lectura opcional. Las llamadas principales (context_bootstrap, cognitive_ingest) NO deben estar en try/except.

El audit en `docs/prd/nemo-mcp-operational-audit-2026-05-10.md` certifica que todo funciona correctamente con 36 tools disponibles.

---

## Modelos LM Studio — Siempre Agnóstico

**NUNCA** hardcodear nombres de modelo.

Función correcta:
```python
_resolve_lmstudio_model(base_url)  # mission_control_server.py ~L1568
```
Filtra `embed|rerank|bge|nomic`, retorna el primer modelo chat disponible.

En `_plan_lm_call`:
```python
model = _resolve_lmstudio_model(base_url) or _chat_model(payload)
```

Modelos conocidos del usuario (2026-05-13 ses2):
- `qwen3.5-9b-deepseek-v4-flash` — **9B Q6_K, activo** ✓ — thinking model (emite `reasoning_content`), tool-aware, context 8774. Emite JSON de tool calls correctamente.
- `qwen3.6-40b-claude-4.6-opus-deckard-heretic-uncensored-thinking-neo-code-di-imatrix-max` — 40B Q4_K_S, disponible (~22GB, puede no caber en VRAM del Arc iGPU)
- `opus4.7-gods.ghost.codex-4b.gguf` — 4B Q8_0, también cargado pero el resolver lo ignora (menor context)
- `qwen3.6-35b-a3b-tq3_4s` — 35B MOE, listado pero falla al cargar (~200s timeout)
- `text-embedding-qwen3-embedding-4b` — embedding, filtrado automáticamente
- `gemma-4-e4b-it-uncensored-max-opus-4.7-i1` — 4B VLM, disponible

**Resolver mejorado (ses2):** `_resolve_lmstudio_model` ahora usa `/api/v0/models` (LM Studio management API) para filtrar solo modelos `state=loaded` y tipo `llm|vlm`, ordenando por `loaded_context_length` descendente — elige automáticamente el más capaz entre los cargados. Fallback a `/v1/models` si el endpoint no existe. `mgmt_base` extrae el host raíz quitando `/v1` del path.

---

## El Plan Loop (`api_agent_plan_gen`)

Función principal: `api_agent_plan_gen` en `mission_control_server.py` ~L4823.

Es un **generador** que yields eventos:
```
{"type": "start", "objective": ..., "max_iterations": N, "quality_threshold": X}
{"type": "iteration", "iteration": N, "score": X, "exec_ok": bool, "code_chars": N, "exec_output": "..."}
{"type": "done", "ok": true, "iterations_run": N, "final_score": X, "completed": bool}
{"type": "error", "error": "..."}  ← solo en fallos críticos
```

`api_agent_plan` (~L5070) = wrapper colector backwards-compatible → devuelve el dict final.

Para SSE streaming: cliente envía `Accept: text/event-stream` → `do_POST` redirige a `_handle_plan_sse`.

### Mejoras implementadas (2026-05-13):
1. **`<think>` stripping** en `_extract_code_block` — modelos thinking de 2026
2. **Temperature parameterizable** en `_plan_lm_call` (gen=0.6, critique=0.0)
3. **AST validation** antes del subprocess — catch inmediato de SyntaxError con fix retry
4. **Revert-to-best** — usa `best_code` como base cuando score regresa >0.5 puntos
5. **NEMO pattern memory** — `search_memories` al inicio de cada iteración (blacklist de patrones malos)
6. **Parallel candidates** — `ThreadPoolExecutor` temps [0.4, 0.7, 1.0] en iter > 1
7. **Visual critique** — `_visual_critique_lm_call` envía image a modelo multimodal si exec_ok
8. **SSE streaming** — `_handle_plan_sse` + routing en `do_POST` — verificado desde browser 2026-05-13
9. **Cascade extractor adaptativo** — `_extract_code_block` con pipeline de 4 etapas: thinking-strip → fence python → fence cualquier lenguaje → longest-ast-valid. `_strip_shell_artifacts` elimina bare-line keywords de shell (`fi`, `then`, `done`, `esac`, `;;`, `do`) que pasan AST pero fallan en runtime. Independiente del modelo.
10. **Artifact copy** — al terminar `api_agent_plan_gen`, copia `*.png/jpg/svg` del CWD a `.spacecode-runtimes/mission-control/artifacts/images/`. El evento `done` incluye `artifact_file` (nombre del archivo o null).

### Payload del plan loop:
```json
{
  "objective": "...",
  "max_iterations": 3,
  "quality_threshold": 7.0,
  "topic": "...",
  "parallel_candidates": false,
  "visual_critique": false,
  "model_base_url": "http://localhost:1234/v1",
  "nemo_mcp_url": "http://127.0.0.1:8765/mcp/sse"
}
```

### Tests disponibles:
```powershell
# Test directo sin servidor HTTP (más rápido):
c:\dev\dev4\.venv\Scripts\python.exe scripts\_quick_plan_test.py

# Test vía HTTP:
python scripts\test_plan_mode_loop.py
```

Resultado validado (2026-05-13): iter1=5.0/10 (exec fail), iter2=9.0/10 (exec OK) — 144s total.

---

## LLM Tool Awareness (implementado 2026-05-13 sesión 2, verificado sesión 3)

El LLM del chat conoce un catálogo de tools y puede invocarlos directamente desde la conversación. El ciclo completo es:

**LLM decide → JSON en respuesta → backend detecta → AgentAction → botón en UI → usuario lanza → dispatcher existente ejecuta**

### Constantes y helpers nuevos (~L4798 en `mission_control_server.py`):

```python
_AGENT_TOOL_CATALOG   # string con los 3 tools — inyectado en _lmstudio_chat_completion como system msg
_TOOL_CALL_RE         # regex r'\{[^{}]*"tool"\s*:\s*"([^"]+)"[^{}]*\}'
_parse_llm_tool_calls(text) → list[dict]  # extrae {tool, params} del texto LLM
_llm_tool_call_to_action(inv, server, payload) → AgentAction | None
```

### Los 3 tools disponibles para el LLM:
| Tool | Kind resultante | Dispatcher |
|------|----------------|------------|
| `handoff_start` | `"run"` | `/api/handoff/start` (HandoffJob existente) |
| `plan_generate` | `"plan_generate"` | SSE `/api/agent/plan` (nuevo handler frontend) |
| `job_status` | `"layout"` | muestra estado inline |

### Cambios en `api_agent_message`:
- Firma: `api_agent_message(config, payload, server=None)` — `server` ahora se pasa desde `do_POST`
- Después de obtener `response`, llama `_parse_llm_tool_calls(response)` y convierte cada invocación a `AgentAction`
- Degradación graceful: si el LLM no incluye JSON, la conversación funciona exactamente igual que antes

### Cambios en frontend (`main.tsx` + `MissionTimeline.tsx`):
- `AgentAction.kind` union extendido con `"plan_generate"`
- `runAgentAction` — nuevo branch para `plan_generate`: inyecta mensaje de progreso en la conversación, abre SSE stream a `/api/agent/plan`, actualiza por iteración, muestra imagen artifact en `done`
- `MissionTimeline` — renderiza `actions[]` como botones `.mission-action-btn` en cada evento del timeline
- `MissionTimelineAction` type incluye `payload?: Record<string, unknown>` — se castea a `AgentAction` al llamar `onRunAction`
- CSS en `styles.css` — `.mission-event-actions` y `.mission-action-btn` con variantes de color por `kind` (plan_generate=azul, run/handoff=verde, revise=púrpura)

### Arc iGPU — Serialización de inferencia (sesión 3):
- `_LLM_SEM = threading.Semaphore(1)` — serializa todas las llamadas a LM Studio
- `time.sleep(2.0)` — cooldown entre NEMO embedding y LLM inference en el plan loop
- Evita freeze por contención Vulkan con 3 modelos simultáneos (chat + embedding + reranker)

### Artifact copy — robustez (sesión 3):
- La copia de imágenes al artifacts dir se hace ANTES del `yield done` para que funcione incluso si el SSE client desconecta

### Cómo probar:
```
Chat: "genera un script Python que dibuje una espiral con matplotlib"
→ LLM incluye {"tool": "plan_generate", "params": {...}}
→ Botón azul "Generate: ..." aparece en el timeline
→ Click → SSE stream → iteraciones visibles → imagen en artifact studio

Chat: "implementa una función fibonacci en src/ con tests"
→ LLM incluye {"tool": "handoff_start", "params": {...}}
→ Botón verde "Start Handoff: ..." aparece
→ Click → /api/handoff/start → job activo en TelemetryColumn
```

**Nota importante:** El servidor Python debe reiniciarse para cambios en `mission_control_server.py`. El frontend Vite recarga automáticamente con HMR.

---

## Stop/Steer del Plan Loop (sesión 4)

El plan loop ahora tiene control en tiempo real via `_plan_jobs: dict[str, dict]` a nivel de módulo.

### Mecanismo:
- `_handle_plan_sse` genera un UUID `plan_job_id = f"plan-{uuid4().hex[:12]}"` antes de llamar al generador.
- El generador recibe `job_id` y al inicio de cada iteración consulta `_plan_jobs[job_id]`.
- Si `cancel=True` → yield `{"type": "cancelled"}` y retorna.
- Si `steer != None` → extrae el string, lo inyecta en el prompt como `USER DIRECTIVE`, consume atómicamente (`_ctrl["steer"] = None`).
- El handler HTTP limpia `_plan_jobs` en un bloque `finally`.

### Endpoints nuevos:
```
POST /api/agent/plan/cancel  {"job_id": "plan-xxxx"}
POST /api/agent/plan/steer   {"job_id": "plan-xxxx", "directive": "..."}
```

### Frontend (main.tsx):
- `AgentAction.kind` union: `"plan_cancel" | "plan_steer"` agregados.
- Cuando llega el evento SSE `start` con `job_id`, se inyectan dos botones (Stop rojo, Steer ámbar) en el mensaje del plan.
- Al llegar `done` o `cancelled`, los botones se limpian (`actions: []`).
- `plan_steer` usa `window.prompt()` para el input — sin estado extra.

### CSS (styles.css):
```css
.mission-action-btn.plan_cancel { border-color: #b91c1c; background: #200a0a; color: #fca5a5; }
.mission-action-btn.plan_steer  { border-color: #b45309; background: #1a1000; color: #fbbf24; }
```

---

## Frontend Cleanup — Sesión 4

El frontend fue auditado y limpiado de todos los elementos mock/decorativos. `npx tsc --noEmit` pasa limpio.

### ArtifactWorkbench.tsx — Cambios definitivos:
- **Eliminado:** `ArtifactRuntimeDeck` (tabs estáticos Code/Scene/Assets/Profiler/Terminal + barras CPU/GPU falsas), `ArtifactViewportActions` (Expand/Capture/Center/More sin efecto real), `artifactPerformanceSnapshot()` (FPS/CPU/GPU con seed math), escena 3D de standby (torres, beam, avatar, target, reflection).
- **Añadido:** `FullscreenButton` con `requestFullscreen()` real — usa `useRef<HTMLDivElement>` apuntando a `.artifact-stage`. Sincroniza estado con evento `fullscreenchange`.
- **Añadido:** `ArtifactStandbyCanvas` simple — solo `<Puzzle>` + texto. CSS: `.artifact-standby-placeholder`.
- El `artifact-live-hud` ahora muestra `activeArtifact.kind` + líneas reales en lugar de FPS falso.

### TelemetryColumn.tsx — Cambios definitivos:
- **Eliminado:** `sparkValues(seed)`, `const runSeed`, `<div className="telemetry-sparkline">`.
- **Refactorizado:** "Handoff chain" (4 agentes ficticios) → "Pipeline" con 4 etapas reales (Plan/Execute/Review/Apply) marcadas por `phaseTelemetry.tone`. El estado `complete`/`active`/`waiting` se calcula comparando el índice de la fase actual.

### main.tsx — Cambios definitivos:
- **Eliminado:** bloque `ops-panel` "Próximo incremento" (roadmap hardcodeado como UI).
- **Reemplazado:** `<select>` de Provider en `HandoffComposer` (1 sola opción) → `<div className="composer-static-field">` con `<code>Space Code + LM Studio</code>`. El campo `provider` sigue en el draft con valor `"subprocess"` internamente.

### styles.css — CSS eliminado:
`.artifact-viewport-actions`, `.artifact-standby-scene`, `.artifact-scene-beam`, `.artifact-scene-tower`, `.artifact-scene-horizon`, `.artifact-scene-avatar`, `.artifact-scene-target`, `.artifact-scene-reflection`, `.artifact-standby-hud`, `.artifact-standby-metrics`, `.artifact-runtime-deck`, `.artifact-runtime-tabs`, `.artifact-code-preview`, `.artifact-performance-panel`, `.artifact-empty .artifact-runtime-deck`, `.telemetry-sparkline`, `.telemetry-sparkline i`

### styles.css — CSS añadido:
`.artifact-fullscreen-btn`, `.artifact-standby-placeholder`, `.composer-static-field`, `.section-home .cockpit-topbar { display: none }` (evita header duplicado en Home)

---

## Archivos Críticos

| Archivo | Propósito |
|---------|-----------|
| `src/nemo_coding_platform/mission_control_server.py` | Servidor HTTP completo, >5000 líneas. Todo el backend. |
| `src/nemo_coding_platform/cli.py` | CLI entrypoint. Subcomando `mission-control-server`. |
| `src/nemo_coding_platform/core/vscode_mcp_config.py` | Descubrimiento del NEMO MCP URL (env var → SSE probe → VS Code config). |
| `.spacecode-runtimes/mission-control/settings.json` | Settings autoritativos del servidor. |
| `.nemo-runtimes/mission-control/settings.json` | Settings alternativos para contexto sin VS Code. |
| `scripts/start-mvp-local.ps1` | Script oficial de arranque completo. |
| `apps/mission-control/` | Frontend React/Vite (puerto 5173). |
| `apps/mission-control/src/main.tsx` | Frontend principal — AgentAction kinds, runAgentAction, chat UI. |
| `apps/mission-control/vite.config.ts` | Proxy `/api/*` → `SPACE_CODE_MISSION_CONTROL_API_URL`. |
| `scripts/_quick_plan_test.py` | Test directo del plan loop sin HTTP. |
| `scripts/test_plan_mode_loop.py` | Test del plan loop vía HTTP. |
| `docs/prd/nemo-mcp-operational-audit-2026-05-10.md` | Audit de correctitud del NEMO MCP. |
| `CLAUDE.md` | **Este archivo** — fuente de verdad para Claude. |

---

## Sprint Worktrees + Quality Core — 2026-05-13

Real git worktrees + Aider git-aware + merge gate API + Review tab UI. Doc completo en `docs/sprints/sprint-worktrees-quality-core-2026-05-13.md`.

### Nuevas capacidades implementadas

**`--use-git-worktree`** — flag en `long-handoff-run` y `long-handoff-continue`. Cuando activo:
- Cada task crea una rama `wt/<task_slug>-<run_slug>` con `git worktree add`
- Worktree en `<repo_root>/.worktrees/<runtime_id>/`
- Aider commitea dentro del worktree (sin `--no-git --no-auto-commits`)
- Worktree queda en pie para review al completar. Fallo/cancelación → cleanup automático

**Merge Gate API** (3 endpoints nuevos en `mission_control_server.py`):
```
GET  /api/run/<job_id>/worktree-diff    → {diff, branch, runtime_id, worktree_exists}
POST /api/run/<job_id>/worktree-merge   → {approved:true} → merge --no-ff + cleanup
POST /api/run/<job_id>/worktree-cleanup → elimina worktree y branch sin merge
```

**WorktreeDiffPanel.tsx** — nuevo componente en Review tab de Mission Control. Muestra el diff del branch, botones "Approve & Merge to main" y "Reject & Remove". Job identificado por `task_id`+`run_id` en `state.jobs`.

**Gaps cerrados:**
- GAP-02 (sin worktree): ✅ `git worktree add` real
- GAP-08 (sin merge gate real): ✅ API + UI funcional
- GAP-01 parcial (Aider sin git): ✅ `build_git_engine_command` habilita commits

### Funciones/helpers clave en worktree_runtime.py

```python
worktree_branch_name(runtime_id)          # → "wt/<id>"
create_git_worktree(repo, path, branch)   # subprocess git worktree add -b
cleanup_git_worktree(repo, path, branch)  # remove + branch -D + prune
worktree_diff(repo, branch, base="main")  # git diff main...branch (3-dot)
merge_worktree_to_main(repo, branch, msg) # git merge --no-ff → bool
initialize_git_worktree_runtime(spec, repo, branch)
```

### headless_runner.py — worktree override

`headless_runner` NO usa `create_worktree_runtime(use_git=True)` para el path.
Lo hace manualmente con `dataclasses.replace()` usando `request.repo_path`:
```python
repo_root = Path(request.repo_path).resolve()
wt_path = repo_root / ".worktrees" / runtime.runtime_id
runtime = replace(runtime, worktree_path=wt_path)
```
Razón: evita dependencia de la profundidad del `runtime_root`.

### Tests

`tests/test_worktree_runtime.py` — 17 tests, todos ✅. Helper `_make_git_repo(tmp_path)` para repos git temporales reales.

### Reinicio requerido

Los 3 endpoints del merge gate requieren reiniciar el backend (`start-mvp-local.ps1`).

---

## Errores Pasados — No Repetir

### ❌ Arrancar el backend Python desde Claude
`Start-Process` mata el proceso hijo inmediatamente. El frontend Vite SÍ se puede arrancar desde Claude (Bash background), el backend NO.

### ❌ Hardcodear nombres de modelos
`nvidia.agentic.coder-4b` está en settings pero NO es el modelo activo. Siempre `_resolve_lmstudio_model()`.

### ❌ Tocar la integración NEMO MCP
Agregar `try/except` alrededor de `_nemo()` en calls principales, cambiar `_require_nemo_mcp_url`, modificar timeouts MCP — PROHIBIDO.

### ❌ Confundir los dos settings.json
`.spacecode-runtimes/` es el autoritativo. `.nemo-runtimes/` es para contextos sin VS Code.

### ❌ Truncar código con max_tokens bajo
Menos de 512 tokens → código cortado a la mitad → SyntaxError. No bajar de 512 para generación de código.

### ❌ `plt.show()` en código generado
El proceso subprocess se cuelga esperando GUI. El gen_sys prompt fuerza `matplotlib.use("Agg")` y `plt.savefig()`.

### ❌ Asumir que el frontend es una app desktop o webview de VS Code
Es un Vite dev server en http://127.0.0.1:5173. Abrir en Chrome directamente.

### ❌ Asumir que NEMO está caído porque `nemo.available: false` en /api/health
El campo `nemo.available` del health check verifica la BD SQLite (`memory_db`). Si el servidor se arrancó sin `memory_db` (modo test), ese campo es false aunque NEMO MCP SSE esté activo en 8765.

---

## Convenciones del Proyecto

- **PYTHONPATH**: debe incluir `src/` al correr el módulo.
- **Python**: `.venv/Scripts/python.exe` (Windows).
- **Frontend**: `npm run dev` en `apps/mission-control/`.
- **Tests**: `python -m pytest tests/` desde la raíz.
- **Rama activa**: `claudeworks`.
- **No hay `package.json` en la raíz** — el frontend está en `apps/mission-control/`.

---

## Stack Técnico

- Backend: Python 3.11+, `http.server.ThreadingHTTPServer`, sin frameworks externos.
- Frontend: React 18 + TypeScript + Vite (puerto 5173).
- LM Studio: `http://localhost:1234/v1` (OpenAI-compatible API).
- NEMO MCP: protocolo MCP sobre stdio (VS Code extension) o SSE (`http://127.0.0.1:8765/mcp/sse`).
- Intel Arc iGPU (Meteor Lake) con Vulkan backend en LM Studio.
- Windows 11, PowerShell 7+.
