# Gap Analysis — Space Code vs. Objetivo Global
**Fecha:** 2026-05-13  
**Referencia:** `docs/prd/global-prd.md`, `docs/prd/final-prd.md`  
**Estado del sistema:** Mission Control UI funcional + backend Python + NEMO + LM Studio

---

## Resumen Ejecutivo

El proyecto tiene una **interfaz de misión sólida** y un **chat con NEMO integrado**, pero carece de casi todos los componentes del núcleo autónomo que el PRD define. La brecha principal no es de UI — es de **motor**: no existe Quality Core wired, no hay worktrees, no hay permisos, no hay Task/Run model real, no hay Full Handoff real.

**Posición actual en el roadmap del PRD:**
- Phase 0 (PRD y contratos): ✅ PRD escrito, specs exist como docs y test stubs
- Phase 1 (Core Engine): 🔴 **No iniciado en serio** — existe Aider fork pero sin wiring
- Phase 2 (Desktop MVP): 🟡 Parcialmente — UI existe, pero sin backend Task/Run real
- Phase 3 (Agent Runtime): 🔴 No iniciado
- Phase 4 (Agentic Layer): 🔴 No iniciado
- Phase 5 (Evaluation/Trust): 🔴 No iniciado

---

## Gaps por Área

### 🔴 GAP-01: Quality Core / Aider no conectado
**PRD:** SPEC-01, SPEC-07, BR1 — Aider es el único que puede escribir código fuente. Dry-run, diff, validación, rollback.

**Estado actual:**  
`product/nemo_code_runtime/` existe como fork de Aider pero no está wired a nada. El handoff actual ejecuta un subprocess Python arbitrario — no hay repo map, no hay search/replace disciplinado, no hay dry-run, no hay aplicación de diff. El agente puede sobrescribir archivos directamente sin ninguna capa de protección.

**Lo que falta:**
- Adapter en Python que llame a `product/nemo_code_runtime/aider/coders/` para aplicar cambios
- Integración de repo map (tree-sitter) para contexto de archivos
- Pipeline: draft patch → dry-run → permission check → apply → validate
- Rollback: `git stash` o `git checkout` si validación falla

**Impacto:** Bloqueante para Full Handoff real y para cualquier claim de "calidad Aider".

---

### 🔴 GAP-02: Worktree/Sandbox — no existe aislamiento
**PRD:** SPEC-04, BR3, FR5 — toda tarea autónoma debe correr en worktree/contenedor aislado. El workspace principal nunca se modifica en modo autónomo.

**Estado actual:**  
El handoff job corre en el directorio del repo principal (`--repo .`). No hay `git worktree add`. Si el agente escribe un archivo, lo escribe directamente en el árbol de trabajo principal. No hay merge gate.

**Lo que falta:**
- `git worktree add .worktrees/<task_id> <branch>` al iniciar un handoff
- Ejecutar Aider/subprocess dentro del worktree
- Merge gate: diff entre worktree y main antes de aprobar
- Cleanup del worktree al cerrar/cancelar

**Impacto:** Bloqueante para seguridad y para cualquier nivel de autonomía mayor que "asistido".

---

### 🔴 GAP-03: Permission System — no existe
**PRD:** SPEC-02, BR5, FR3 — cada acción declara actor, fase, permiso, target, riesgo, decisión.

**Estado actual:**  
No hay ninguna capa de permisos. El handoff puede ejecutar cualquier comando. No hay audit trail de permisos. La "approval queue" del frontend muestra `review_status` de runs pero no hay enforcement de policy — es solo un estado de campo.

**Lo que falta:**
- Modelo `PermissionDecision` con actor, fase, target, riesgo, decisión (allow/ask/deny)
- Motor de evaluación: reglas → decisión
- Defaults: red externa = deny, writes fuera de sandbox = deny, merge = ask
- Timeline events por cada decision de permiso
- API para que el usuario apruebe/rechace decisiones ask

**Impacto:** Bloqueante para autonomía segura. Sin esto, más autonomía = más peligro.

---

### 🔴 GAP-04: Task/Run Data Model — stubs sin implementación real
**PRD:** SPEC-06, FR8, BR10 — Task, Run, Timeline reproducible, Artifacts indexados.

**Estado actual:**  
Los handoff jobs guardan estado como JSON files en `.spacecode-runtimes/`. No hay entidad `Task` con specs linkadas, autonomy level, historial de runs. No hay `Run` con state machine observable. No hay timeline de eventos indexada y reproducible. El `AgentRun` que muestra el frontend es solo el JSON plano del handoff.

**Lo que falta:**
- `Task`: id, repo_path, title, objective, autonomy_level, status, linked_specs, created_at
- `Run`: id, task_id, runtime_id, state, model_profile, sandbox_path, started_at, completed_at
- State machine con transiciones: initializing → ready → planning → building → reviewing → paused → completed → failed
- `ToolEvent`, `PermissionDecision`, `MemoryTrace`, `Artifact` asociados a cada Run
- API para listar/filtrar Tasks y Runs
- Timeline reproducible: dado un run_id, reconstruir el historial de eventos

**Impacto:** Bloqueante para replay, evaluación, y para la sección Task Workspace del PRD.

---

### 🔴 GAP-05: Full Handoff PRD-to-Code — no existe
**PRD:** SPEC-11, BR11 — el agente recibe un PRD, genera specs, implementa durante horas, checkpointa, repara, produce review package.

**Estado actual:**  
El handoff actual es un wrapper que corre un subprocess con un objective string. No parsea PRDs, no genera specs, no usa Aider para editar, no checkpointa en NEMO periódicamente, no tiene repair loop con presupuesto, no produce un review package.

**Lo que falta:**
- PRD parser/ingestion: extraer objetivos, aceptance criteria, linked files
- Spec generation: convertir objetivo en tests/contracts ejecutables antes de codear
- Long-running loop: iterar build/validate/repair sin prompts humanos
- Checkpoint NEMO: `store_conversation` cada N minutos o cada milestone
- Repair budget: máximo K intentos de reparación por fallo de validación
- Review package: diff final, test results, memory trace, decision log
- Replay: dado el package, reproducir el run

**Impacto:** Este es el feature principal del producto según el PRD ("Full Handoff is main requirement"). No existe en ninguna forma real.

---

### 🟡 GAP-06: NEMO Integration — parcial, no completo
**PRD:** SPEC-05, BR4, FR6 — NEMO en todas las fases: start, plan, build, review, close. Evidence handles, corrections desde UI, store_conversation al cerrar.

**Estado actual:**  
NEMO se usa en:
- Chat: `context_bootstrap` + `search_memories` por mensaje (✅)
- Plan loop: `search_memories` por iteración para blacklist de patrones (✅)
- Handoff: NO usa NEMO en absoluto (🔴)

**Lo que falta:**
- `store_conversation` al cerrar una tarea/handoff
- `create_correction` cuando el usuario rechaza un resultado desde la UI
- Evidence handles en place of storing large diffs inline
- `build_context_portfolio` por fase (plan/build/review con budgets distintos)
- Memory trace por Run: qué herramientas NEMO se usaron, qué memories se leyeron/escribieron
- Handoff wired a NEMO: bootstrap al inicio, cognitive_ingest al cerrar

---

### 🟡 GAP-07: Desktop App Shell — falta Tauri/Electron
**PRD:** SPEC-09, FR1 — la app debe iniciar como aplicación desktop independiente de VS Code.

**Estado actual:**  
La UI corre como Vite dev server en `http://127.0.0.1:5173`. Para usarla el usuario debe: (1) tener Node.js, (2) correr `start-mvp-local.ps1`, (3) abrir Chrome manualmente. No hay instalador, no hay icono, no hay ventana desktop.

**Lo que falta:**
- Wrapper Tauri (Rust) o Electron alrededor del frontend React existente
- Bundle del backend Python (PyInstaller o sidecar process)
- Instalador (`.msi` en Windows, `.dmg` en Mac)
- Auto-arranque del backend al abrir la app
- Sin depender de Node.js en el sistema del usuario final

**Nota:** El PRD dice "Tauri o Electron con React/TypeScript" — la base React ya existe y es reutilizable. Tauri sería más liviano dado el backend Python ya existe por separado.

---

### 🟡 GAP-08: Diff/Review UI — básica, sin merge gate real
**PRD:** SPEC-09, FR3 — diff visible antes de merge, aprobación/rechazo, rollback.

**Estado actual:**  
Existe una pestaña "Review" en el Runs Workbench que muestra un diff. Pero no hay: aprobación por hunk, rechazo de líneas específicas, merge gate que bloquee el write hasta aprobación, rollback desde la UI.

**Lo que falta:**
- Botón "Approve and merge" que dispare merge del worktree al main
- Botón "Reject" que elimine el worktree sin aplicar
- Selección de hunks individuales para aprobación parcial (v2)
- Rollback desde UI: `git checkout` de archivos específicos

---

### 🟡 GAP-09: Repo Map — sin integración
**PRD:** FR3, Quality Core — el agente debe entender el repo para planear cambios multiarchivo.

**Estado actual:**  
El chat y el plan loop no tienen acceso a un repo map. El LLM trabaja con el texto del objetivo únicamente. Aider tiene tree-sitter repo map en `product/nemo_code_runtime/aider/` pero no está integrado.

**Lo que falta:**
- Llamada a Aider repo map al inicio de cada tarea: listar símbolos, archivos relevantes
- Incluir repo map summary en el system prompt del plan loop
- Cache del repo map con invalidación por cambios git

---

### 🟡 GAP-10: Model Roles — un modelo para todo
**PRD:** FR2 — roles distintos: planner, editor, reviewer, summarizer.

**Estado actual:**  
Hay un solo modelo para todo. Chat, plan loop, visual critique, embedding y reranker comparten el mismo endpoint LM Studio. No hay configuración de perfiles por rol.

**Lo que falta:**
- `ModelProfile`: rol → base_url + model_id + temperatura + max_tokens
- Al menos 2 roles: chat/planner (modelo grande, temperatura alta) y editor (modelo pequeño/rápido, temperatura baja)
- Settings UI para configurar roles

---

### 🔴 GAP-11: MCP Tool Plane Governance — no existe
**PRD:** SPEC-08, FR7 — registry de servers/tools, risk mapping, phase gates, audit trail.

**Estado actual:**  
NEMO es el único MCP server. No hay registry de herramientas externas, no hay risk mapping, no hay phase gates para MCPs externos, no hay audit de calls MCP en el timeline.

**Lo que falta:**
- Registry: `MCPServer` con tools declaradas, risk level, capabilities
- Phase gate: qué tools pueden llamarse en plan/build/review/close
- Audit trail: cada tool call aparece en el Run timeline
- UI en Settings para agregar/remover MCP servers

---

### 🔴 GAP-12: Evaluation/Replay — no funciona de punta a punta
**PRD:** SPEC-10, FR10 — fixtures de tareas, replay de timeline, scoring.

**Estado actual:**  
Hay muchos archivos de test en `tests/` pero la mayoría son contract/spec tests (verifican estructura de datos). No hay fixtures de tareas reales, no hay replay engine, no hay autonomy scorecards.

**Lo que falta:**
- Fixtures de tareas reproducibles con repo snapshot
- Replay: dado un run_id con timeline completo, re-ejecutar y comparar
- Scoring automático: pass rate, validation rate, override rate
- Regression suite: un run que antes pasaba sigue pasando

---

## Priorización — Orden de Implementación Óptimo

Basado en el roadmap del PRD (headless run MVP → full handoff MVP) y el principio "backend contracts primero, UI después":

### Nivel 1 — Bloqueantes para cualquier autonomía real
1. **Worktree isolation** (GAP-02) — 2-3 días — sin esto todo lo demás es peligroso
2. **Quality Core wiring** (GAP-01) — 3-5 días — conectar Aider para ediciones controladas
3. **Permission system mínimo** (GAP-03) — 2-3 días — allow/ask/deny por acción, sin UI aún

### Nivel 2 — Necesarios para el Headless Run MVP
4. **Task/Run data model** (GAP-04) — 2-3 días — entidades reales, state machine, timeline
5. **NEMO full wiring en handoff** (GAP-06 parcial) — 1-2 días — bootstrap + close + corrections

### Nivel 3 — Necesarios para Full Handoff MVP
6. **Full Handoff loop** (GAP-05) — 5-7 días — el feature principal
7. **Diff/Review merge gate** (GAP-08) — 2-3 días — prerequisito de cualquier autonomía alta
8. **Repo map** (GAP-09) — 2-3 días — sin esto el agente trabaja ciego

### Nivel 4 — Desktop y experiencia final
9. **Desktop shell** (GAP-07) — 3-5 días — Tauri wrapper
10. **Model roles** (GAP-10) — 1-2 días — quick win en settings
11. **MCP governance** (GAP-11) — 3-5 días — seguridad de extensibilidad
12. **Evaluation/Replay** (GAP-12) — 5-7 días — prerequisito para escalar autonomía

---

## Lo que SÍ Funciona Bien

Estos componentes están sólidos y no necesitan rework sustancial:

| Componente | Estado | Notas |
|-----------|--------|-------|
| Chat + LLM + NEMO | ✅ Sólido | context_bootstrap, search_memories, cognitive_ingest wired |
| Plan loop SSE | ✅ Sólido | Stop/Steer, iteraciones, visual critique, artifact copy |
| LLM tool awareness | ✅ Sólido | JSON → AgentAction → botón en UI → dispatcher |
| NEMO MCP integration | ✅ Sólido | NO TOCAR — costó mucho estabilizar |
| Mission Control UI | ✅ Limpia | Audit completado, todo mock eliminado, TypeScript limpio |
| Git operations | ✅ Funcional | status, diff, checkout, branches |
| Settings persistence | ✅ Funcional | settings.json autoritativo |
| Arc iGPU serialization | ✅ Sólido | Semaphore(1) + sleep(2.0) evita freeze Vulkan |

---

## Conclusión

El proyecto tiene una **superficie de interacción excelente** (UI limpia, chat inteligente, plan loop con control) pero le falta el **motor de autonomía real**. 

El objetivo del PRD es un entorno donde un agente trabaja como ingeniero autónomo supervisable. Actualmente tenemos la "supervisión" pero no el "autónomo". La prioridad absoluta es construir Worktree → Quality Core → Permissions → Task Model — ese stack es el que convierte Mission Control de "demo inteligente" en "plataforma de coding agentico".

El camino más corto al MVP real es el Headless Run MVP definido en `final-prd.md`: una tarea real en repo local, bootstrap NEMO, plan, permisos, worktree sandbox, patch con Aider, validación, review gate, memory writeback, timeline reproducible. Si eso funciona de punta a punta, el producto tiene forma real.
