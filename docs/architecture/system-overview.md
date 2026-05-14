# System Overview

**Última actualización: 2026-05-13 (sesión 2)**

Esta plataforma apunta a un entorno desktop local-first para coding autónomo. El backend Python/CLI es el harness de contratos activo. El sistema se organiza en cinco planos.

---

## Estado Operativo — 2026-05-13

| Componente | Puerto | Estado | Detalles |
|---|---|---|---|
| Backend Python | 8787 | ✅ UP | `mission_control_server.py` vía `start-mvp-local.ps1` |
| Frontend React/Vite | 5173 | ✅ UP | `apps/mission-control/` — Mission Control UI |
| NEMO MCP SSE | 8765 | ✅ UP | VS Code extension, PID 28048 |
| Reranker bge-reranker-v2-m3 | 8080 | ✅ UP | llama-server, parte del pipeline RAG de NEMO |
| LM Studio | 1234 | ✅ UP | Modelo activo: `opus4.7-gods.ghost.codex-4b.gguf` |
| SSE Plan Loop `/api/agent/plan` | — | ✅ Verificado | `text/event-stream`, evento `{type:"start"}` confirmado desde browser |

---

## Arquitectura de Capas

```
┌─────────────────────────────────────────────┐
│  Mission Control UI (React/Vite, port 5173)  │
│  apps/mission-control/                       │
│  Proxy /api/* → backend via vite.config.ts   │
└───────────────────┬─────────────────────────┘
                    │ HTTP
┌───────────────────▼─────────────────────────┐
│  Backend Python (port 8787)                  │
│  src/nemo_coding_platform/                   │
│  └── mission_control_server.py  (>5000 L)   │
│      ThreadingHTTPServer, sin frameworks     │
│      Routing manual en do_GET / do_POST      │
└──────┬────────────────────────┬─────────────┘
       │                        │
┌──────▼───────┐    ┌───────────▼──────────────┐
│ LM Studio    │    │ NEMO MCP                  │
│ port 1234    │    │ stdio://vscode/nemo        │
│ OpenAI compat│    │ http://127.0.0.1:8765/    │
│ model: auto  │    │   mcp/sse (SSE standalone) │
└──────────────┘    └──────────────────────────┘
```

---

## Los Cinco Planos

### Quality Core
Posee la mutación de workspace con git-awareness, aplicación de diffs, validación y el único path de escritura de código. Actualmente: `api_agent_plan_gen` en `mission_control_server.py`.

### Runtime
Posee el ciclo de vida del sandbox/sesión, el estado de ejecución y la estrategia de aislamiento del backend. Los jobs de larga duración se gestionan con `self.server.jobs`.

### Orchestration
Posee el workflow: plan, build, review, delegación, background runs y escalado de autonomía. El plan loop (`api_agent_plan_gen`) es el núcleo de orquestación autónoma.

### Policy
Posee las reglas allow/deny/ask, inspección y aprobaciones para acciones de riesgo.

### Memory
Posee la superficie completa de herramientas NEMO: contexto inicial, context economy portfolios, evidencia expandible, feedback loops, correcciones, conversación, recordatorios, citas, contexto de tiempo/ambiente y diagnósticos de mantenimiento.

---

## Plan Loop — Estado Implementado (2026-05-13 sesión 2)

El plan loop autónomo (`/api/agent/plan`) implementa:

1. `<think>` stripping — modelos con bloques de razonamiento internos
2. Temperature parameterizable — gen=0.6, critique=0.0
3. AST validation pre-subprocess — catch de SyntaxError con fix retry
4. Revert-to-best — usa `best_code` como base cuando el score regresa >0.5pt
5. NEMO pattern memory — `search_memories` por iteración para blacklist de patrones malos
6. Parallel candidates — `ThreadPoolExecutor` temps [0.4, 0.7, 1.0] en iter > 1
7. Visual critique — `_visual_critique_lm_call`, base64 PNG al modelo multimodal
8. SSE streaming — `_handle_plan_sse` + routing en `do_POST`
9. Cascade extractor adaptativo — `_extract_code_block` con 4 etapas + `_strip_shell_artifacts`. **Resuelve la regresión `fi`** — los bare-line shell keywords ahora se eliminan antes de ejecución.
10. Artifact copy — copia `*.png/jpg/svg` al terminar a `.spacecode-runtimes/mission-control/artifacts/images/`. Evento `done` incluye campo `artifact_file`.

**Resultado de validación (2026-05-13):** iter1=5.0/10 (exec fail), iter2=9.0/10 (exec OK) — 144s con `opus4.7-gods.ghost.codex-4b.gguf`.

**Regresión `fi` — RESUELTA (2026-05-13 sesión 2):** `_strip_shell_artifacts` elimina `fi`, `then`, `done`, `esac`, `;;`, `do` como bare lines antes del subprocess. El cascade extractor es independiente del modelo.

---

## LLM Tool Awareness — Implementado (2026-05-13 sesión 2)

El LLM del chat conoce 3 tools y puede invocarlos emitiendo JSON en su respuesta. El backend detecta ese JSON y lo convierte en un `AgentAction` con botón clickeable en la UI.

```
Usuario → chat → LLM ve _AGENT_TOOL_CATALOG en system prompt
LLM responde: texto + {"tool": "handoff_start", "params": {...}}
Backend: _parse_llm_tool_calls() extrae → _llm_tool_call_to_action() convierte → actions[]
Frontend: renderiza botón → usuario hace click → runAgentAction() despacha
```

| Tool LLM | AgentAction.kind | Endpoint |
|----------|-----------------|----------|
| `handoff_start` | `"run"` | `/api/handoff/start` |
| `plan_generate` | `"plan_generate"` | SSE `/api/agent/plan` |
| `job_status` | `"layout"` | inline status |

**Archivos modificados:**
- `mission_control_server.py`: `_AGENT_TOOL_CATALOG`, `_parse_llm_tool_calls`, `_llm_tool_call_to_action`, `api_agent_message` (firma + integración), `_lmstudio_chat_completion` (system msg)
- `apps/mission-control/src/main.tsx`: `AgentAction.kind` union + `runAgentAction` handler para `plan_generate`

---

## Reglas No Negociables

1. Planning es read-only.
2. Execution solo puede mutar después de aprobación.
3. Review es obligatorio antes de commit o PR actions.
4. Solo el quality core puede escribir archivos fuente.
5. NEMO es el plano de contexto por defecto.
6. Alta autonomía debe correr dentro de sandbox/worktree antes de modificar el workspace principal.

---

## Reglas Operativas (Claude / Agentes)

- **Backend Python**: NUNCA arrancar desde Claude con `Start-Process`. El usuario lo arranca con `start-mvp-local.ps1`.
- **Frontend Vite**: SÍ se puede arrancar desde Claude con `npm run dev` en background.
- **NEMO MCP**: NO modificar `_require_nemo_mcp_url`, `mcp_call_nemo_tool` ni el stack MCP. No envolver llamadas `_nemo()` principales en try/except.
- **Modelos**: NUNCA hardcodear. Usar `_resolve_lmstudio_model(base_url)`.
- **Settings**: `.spacecode-runtimes/mission-control/settings.json` es el autoritativo. `.nemo-runtimes/` es alternativo.
