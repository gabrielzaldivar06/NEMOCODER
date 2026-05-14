# PRD Global: Space Code

## 1. Vision

Construir una app desktop para coding altamente autónoma, local-first y extensible por MCP, que combine:

- la fiabilidad de edición y validación de Aider
- el flujo plan/build, permisos y ergonomía de OpenCode
- las capacidades agenticas fuertes, runtime aislado y ejecución prolongada de OpenHands
- NEMO como memoria operativa, semántica y de largo plazo a través de toda su suite MCP

El producto debe sentirse como un entorno de desarrollo agentico serio: capaz de entender repos grandes, planificar, ejecutar, probar, revisar, recordar decisiones, operar con subagentes, recuperar contexto histórico y sostener tareas largas sin perder trazabilidad.

## 2. Product Thesis

La plataforma no debe ser un chat con herramientas ni un clon directo de un repo existente. Debe ser un entorno desktop donde el agente trabaja como un ingeniero autónomo supervisable: propone planes, edita con disciplina, ejecuta validaciones, usa memoria NEMO como contexto vivo, opera en sandboxes, coordina subagentes y conserva una auditoría completa de decisiones y artefactos.

El usuario debe poder delegar tareas grandes con confianza progresiva. La autonomía es objetivo central, pero debe estar instrumentada: permisos, trazabilidad, replay, evaluación, sandboxing y memoria verificable.

## 3. Target Users

1. Desarrollador individual con modelos locales en LM Studio u otros proveedores OpenAI-compatible.
2. Power user que quiere un entorno de coding independiente de VS Code.
3. Ingeniero que trabaja en repos complejos y necesita planificación, cambios multiarchivo, tests y memoria persistente.
4. Equipo pequeño que quiere agentes locales o self-hosted con políticas, auditoría y MCP.
5. Investigador/constructor de agentes que quiere una plataforma extensible con NEMO como memoria central.

## 4. Primary Goals

1. App desktop usable como superficie principal.
2. Coding fiable como base, no como feature secundaria.
3. Alta autonomía con sandboxing, subagentes y tareas de larga duración.
4. NEMO integrado como memoria total: portfolios, correcciones, conversaciones, reminders, appointments, contexto, evidencia, feedback y mantenimiento.
5. Flujo Spec Driven Development nativo: PRD -> specs ejecutables -> implementación -> evaluación -> memoria.
6. Compatibilidad con modelos locales vía LM Studio/OpenAI-compatible.
7. MCP como sistema de extensión formal, gobernado por permisos y riesgo.
8. Trazabilidad completa de plan, acciones, diffs, tests, memoria usada y decisiones.

## 5. Non-Goals Iniciales

1. No crear un fork monolítico de Aider, OpenCode, Goose u OpenHands.
2. No depender exclusivamente de VS Code.
3. No permitir autonomía irrestricta sin sandbox, permisos y replay.
4. No tratar NEMO como plugin opcional ni solo como sistema de portfolios.
5. No construir primero una nube multiusuario; la app debe ser local-first.
6. No optimizar para demo visual antes de garantizar calidad de edición y flujo agentico.

## 6. Product Form

### 6.1 App Desktop

La interfaz principal será desktop. La recomendación técnica inicial es una shell desktop moderna con UI web y backend local:

- Frontend desktop: Tauri o Electron con React/TypeScript.
- Backend local: Python service inicial reutilizando el scaffold actual, con posibilidad de mover subsistemas críticos a Rust si hace falta.
- Comunicación: HTTP local o JSON-RPC local.
- Runtime agentico: procesos locales, worktrees y posteriormente contenedores.
- MCP: cliente/host local con registry de herramientas, permisos y auditoría.

### 6.2 CLI Interna

La CLI existente no es el producto final. Debe mantenerse como:

- harness de desarrollo
- modo headless
- entrada para CI/evals
- forma de probar contratos antes de UI

## 7. Core Experience

### 7.1 Home / Mission Control Local

El usuario ve:

- repos recientes
- tareas activas
- estado de agentes y subagentes
- memoria NEMO activa
- sandboxes en ejecución
- cola de aprobaciones
- resultados de validación
- errores bloqueantes

### 7.2 Task Workspace

Cada tarea tiene:

- objetivo
- PRD/spec asociado si existe
- plan actual
- árbol de subtareas
- agentes asignados
- herramientas usadas
- archivos tocados
- diffs
- comandos ejecutados
- tests/lints
- memorias NEMO usadas y escritas
- evidencia expandida
- estado de review

### 7.3 Agent Run Timeline

Timeline auditable:

1. Context bootstrap NEMO.
2. Repo scan / repo map.
3. Plan.
4. User approval or autonomy policy decision.
5. Execution.
6. Tests and diagnostics.
7. Review.
8. Memory writeback.
9. Final artifact: patch, commit, PR draft, report or task closure.

### 7.4 Autonomy Dial

La app debe soportar niveles de autonomía:

- Manual: el usuario aprueba cada write/tool action.
- Assisted: el agente ejecuta acciones permitidas y pide aprobación en riesgo medio/alto.
- Autonomous Sandbox: el agente trabaja libremente dentro de un sandbox/worktree aislado, pero requiere aprobación para mergear/cambiar repo principal.
- Background Agent: tareas largas con checkpoints y notificaciones.
- Team/CI Mode: futuro, agentes disparados por eventos.

El objetivo del producto es llegar a alta autonomía, empezando por autonomía fuerte dentro de límites verificables.

## 8. Architecture

## 8.1 Planes

### Quality Core

Inspirado en Aider.

Responsabilidades:

- git/worktree awareness
- repo map y code graph
- edición por diff/patch con recovery
- dry-run
- aplicación de cambios
- lint/test/typecheck
- validación antes de finalizar
- commit/PR artifact preparation

Regla: solo el Quality Core puede escribir cambios al código fuente.

### Workflow Core

Inspirado en OpenCode.

Responsabilidades:

- modos plan/build/review
- agents configurables
- comandos reutilizables
- permisos por agente
- session/task hierarchy
- plan file y artifacts
- comandos headless

### Agent Runtime

Inspirado en OpenHands.

Responsabilidades:

- sandbox lifecycle
- ejecución prolongada
- terminal persistente
- browser/computer tools
- event stream
- state machine
- pause/resume/cancel
- métricas por run
- aislamiento por tarea

### MCP Tool Plane

Inspirado en Goose y NEMO.

Responsabilidades:

- registry de servidores MCP
- capability declarations
- tool risk mapping
- prompts/resources/tools
- permisos por herramienta
- auditoría
- MCP apps/UI en fases futuras

### NEMO Memory Plane

NEMO es memoria operativa y memoria de largo plazo.

Debe incluir todas sus herramientas, agrupadas por suite:

- startup context: prime_context, context_bootstrap
- context economy: portfolios, evidence, compression, stats, feedback
- memory management: corrections, curated updates, salience, redundancy, chronicle cuando estén disponibles
- conversation: store_conversation, recent context
- reminders and appointments: reminders, intent anchors, appointments
- time/environment: current time, weather cuando sea relevante
- maintenance: health, diagnostics

NEMO debe ser usado por cada fase agentica:

- antes de planificar
- durante ejecución si falta evidencia
- durante review para feedback, correcciones y decisiones
- al cerrar tarea para memoria duradera

## 9. Spec Driven Development Model

El proyecto debe continuar por SDD, con este orden estricto:

1. PRD global.
2. Architecture specs.
3. Executable contract specs.
4. Thin implementation slices.
5. Integration specs.
6. Evals and replay specs.
7. UI specs.
8. Autonomy escalation specs.

Ninguna capacidad agentica avanzada debe considerarse aceptada sin:

- spec escrita
- tests o evals ejecutables
- traza de auditoría
- política de permisos
- interacción esperada con NEMO

## 10. Functional Requirements

### FR1 Desktop App Shell

La app debe iniciar como aplicación desktop local, mostrar repos/tareas, y comunicarse con el backend local.

Acceptance:

- La app abre sin requerir VS Code.
- Puede seleccionar o abrir un repo local.
- Muestra estado de backend, NEMO y modelo.
- Puede iniciar una tarea.

### FR2 Local Model Provider

Debe conectar con LM Studio y cualquier endpoint OpenAI-compatible.

Acceptance:

- Configura base URL, model name y API key opcional.
- Detecta errores de conexión.
- Permite roles de modelo: planner, editor, reviewer, summarizer.

### FR3 Aider-Quality Editing

Debe aplicar cambios con seguridad.

Acceptance:

- Todo write pasa por Quality Core.
- Dry-run antes de write.
- Diff visible antes de merge a workspace principal.
- Lint/test commands detectables y configurables.
- Rollback disponible.

### FR4 OpenCode-Style Workflow

Debe separar planificación, construcción y revisión.

Acceptance:

- Plan mode no puede escribir código.
- Build mode puede escribir solo con política autorizada.
- Review mode verifica cambios, tests y memoria usada.
- Puede crear agentes especializados con permisos específicos.

### FR5 OpenHands-Style Autonomy

Debe ejecutar tareas largas con runtime aislado.

Acceptance:

- Una tarea puede correr en sandbox/worktree separado.
- Tiene state machine observable.
- Puede pausar, reanudar y cancelar.
- Puede ejecutar shell, leer archivos, correr tests y reportar progreso.
- Puede delegar subtareas a subagentes con límites de profundidad y permisos.

### FR6 NEMO Full Tool Integration

Debe poder usar toda la suite NEMO, no solo portfolios.

Acceptance:

- Registry de herramientas NEMO con suite, riesgo, fase y propósito.
- Plan/execute/review tienen tool access distinto.
- Scheduling/reminders requieren intención explícita o review gate.
- Correcciones de usuario se guardan inmediatamente.
- Conversaciones y decisiones se guardan al cerrar tarea.
- Evidence handles se expanden bajo presupuesto.

### FR7 MCP Extension Governance

Debe permitir MCPs externos bajo permisos.

Acceptance:

- Cada servidor MCP declara capacidades.
- Cada tool tiene riesgo y política.
- Acciones peligrosas piden aprobación.
- Las llamadas MCP aparecen en timeline.

### FR8 Artifact Trace

Cada tarea debe producir una traza completa.

Acceptance:

- Plan aprobado.
- Acciones ejecutadas.
- Diffs.
- Validaciones.
- Memorias usadas/escritas.
- Evidencia expandida.
- Errores y decisiones.

### FR9 Autonomous Background Tasks

Debe soportar ejecución autónoma controlada.

Acceptance:

- La tarea puede seguir trabajando en background.
- El usuario ve checkpoints.
- El agente no puede fusionar cambios al repo principal sin política permitida.
- Si falla, deja reporte y estado reproducible.

### FR10 Evaluation and Replay

Debe poder evaluar y reproducir runs.

Acceptance:

- Cada run tiene log estructurado.
- Se puede replayar una tarea con el mismo contexto base.
- Se miden success rate, validation pass rate, override rate, stale-memory incidents y tool failure rate.

## 11. Non-Functional Requirements

### Reliability

- Nunca escribir fuera del workspace autorizado.
- Nunca saltar el Quality Core para editar código.
- Nunca borrar o resetear sin aprobación explícita.
- Preferir cambios pequeños y trazables.

### Security

- Sandbox por tarea para autonomía fuerte.
- Permisos allow/deny/ask.
- Red y directorios externos restringidos por defecto.
- Secret handling explícito.
- MCP externo con riesgo declarado.

### Performance

- UI responsiva mientras agentes corren.
- Tool calls y subagentes streamable/observables.
- Context budgets por fase.
- Cache de repo map y snapshots.

### Local-First

- Funciona con modelo local.
- NEMO local como memoria primaria.
- No requiere nube para tareas base.

### Extensibility

- Workflows declarativos.
- Agentes configurables.
- MCPs externos.
- Provider abstraction.

## 12. Data Model

Entidades principales:

- Workspace
- Repository
- Task
- AgentRun
- Subtask
- Session
- Plan
- Artifact
- Patch
- ValidationResult
- ToolCall
- PermissionDecision
- NemoMemoryEvent
- EvidenceHandle
- Sandbox
- WorkflowRecipe
- ModelProfile

## 13. NEMO Memory Lifecycle

### Task Start

- prime_context
- context_bootstrap
- build_context_portfolio
- retrieve corrections/preferences/project facts

### Planning

- portfolio safe/thorough
- maybe expand evidence
- store plan decision if accepted

### Execution

- portfolio fast
- evidence handles for diffs/logs
- store conversation checkpoints
- compress large artifacts

### Review

- portfolio thorough
- expand final evidence
- record feedback
- create corrections if user rejects behavior
- update memory if stale facts found
- create reminders/intent anchors only if explicit

### Task Close

- store conversation summary
- record context feedback
- write durable decisions
- mark open loops

## 14. Autonomy Model

### Agent Types

- Planner
- Builder
- Reviewer
- Debugger
- Test Repair Agent
- Code Search Agent
- Security Auditor
- Dependency Updater
- Documentation Agent
- NEMO Memory Curator
- Runtime Operator

### Subagent Rules

- Subagents get scoped context portfolios.
- Subagents inherit restricted permissions.
- Subagents cannot write directly to main workspace.
- Subagents return findings, patches, evidence or reports.
- Depth and concurrency limits are configured per autonomy mode.

### High Autonomy Target

The long-term platform should support:

- multi-hour coding tasks
- parallel subagents
- sandboxed implementation attempts
- automated test repair loops
- autonomous repo exploration
- MCP-powered external context
- final human merge gate or policy-governed auto-merge

## 15. SDD Roadmap

### Phase 0: PRD and Contracts

Deliverables:

- global PRD
- architecture specs
- memory/tooling specs
- permission specs
- runtime specs
- desktop app spec
- executable contract tests

Exit Criteria:

- All core entities defined.
- All phase gates defined.
- All NEMO tool suites mapped.
- Desktop architecture selected.

### Phase 1: Core Engine

Deliverables:

- Quality Core v1
- workspace safety
- diff/patch application
- validation runner
- policy evaluator
- NEMO registry
- task/run model

Exit Criteria:

- Can create, dry-run, approve, apply and review a patch through backend APIs.

### Phase 2: Desktop MVP

Deliverables:

- desktop shell
- repo picker
- task screen
- plan/build/review timeline
- approval queue
- diff viewer
- NEMO memory panel
- model settings

Exit Criteria:

- User can run a complete coding task from desktop UI.

### Phase 3: Agent Runtime

Deliverables:

- sandbox/worktree backend
- terminal runner
- event stream
- pause/resume/cancel
- background run support
- runtime metrics

Exit Criteria:

- Agent can run long tasks in isolated workspace and report progress.

### Phase 4: Strong Agentic Layer

Deliverables:

- subagent orchestration
- autonomous test repair loop
- code search agent
- reviewer agent
- workflow recipes
- MCP external tools

Exit Criteria:

- Agent can autonomously complete multi-step tasks in sandbox and produce reviewable patch.

### Phase 5: Evaluation and Trust

Deliverables:

- replay engine
- benchmark tasks
- autonomy scorecards
- stale memory detection
- permission pressure metrics
- regression dashboard

Exit Criteria:

- Autonomy can be increased based on measured behavior, not guesswork.

### Phase 6: Team/CI Mode

Deliverables:

- headless service
- PR integration
- CI checks
- team rules
- shared recipe packs
- optional remote runners

Exit Criteria:

- Platform can support team workflows while preserving local-first mode.

## 16. Executable Specs To Write Next

1. `test_desktop_product_contract.py`
   Verify desktop app contract references backend, model config, task workspace and approval queue.

2. `test_nemo_full_tool_contract.py`
   Verify all NEMO suites are represented and phase-gated.

3. `test_autonomy_policy_contract.py`
   Verify autonomy modes map to permissions, sandbox level and merge gates.

4. `test_quality_core_contract.py`
   Verify only Quality Core can write and all writes require dry-run.

5. `test_agent_runtime_contract.py`
   Verify runtime state transitions, pause/resume/cancel and sandbox identity.

6. `test_sdd_lifecycle_contract.py`
   Verify every feature has PRD/spec/test/implementation status.

## 17. Acceptance Criteria for Global MVP

The global MVP is accepted when:

1. Desktop app launches locally.
2. User can configure LM Studio.
3. User can open a repo.
4. User can create a coding task.
5. Agent uses NEMO context at task start.
6. Agent creates a plan.
7. Agent executes in isolated workspace.
8. Agent applies changes only through Quality Core.
9. Agent runs validation commands.
10. User can review diff and timeline.
11. NEMO records session outcome.
12. The task can be replayed or inspected after completion.

## 18. Key Risks

1. Combining too many inspirations into one incoherent system.
   Mitigation: keep planes separate and specs executable.

2. Autonomy causing unsafe writes.
   Mitigation: sandbox first, merge gate always, Quality Core as sole writer.

3. NEMO becoming noisy or stale.
   Mitigation: portfolio budgets, evidence handles, feedback, corrections, review-time memory hygiene.

4. Desktop UI outpacing backend reliability.
   Mitigation: backend contracts and CLI harness remain source of truth.

5. MCP tools expanding risk surface.
   Mitigation: tool registry, risk levels, phase access, user approval and audit logs.

## 19. Decisions

- Product target is desktop app for coding.
- Current CLI/backend scaffold becomes internal engine and test harness.
- Base coding quality comes from Aider-like discipline.
- Workflow and permission ergonomics borrow from OpenCode.
- Strong autonomy and sandbox/runtime patterns borrow from OpenHands.
- NEMO MCP is required and covers all tool suites.
- SDD is mandatory before expanding implementation.

## 20. Immediate Next Steps

1. Add spec documents for desktop architecture, autonomy model, NEMO full tooling, and SDD lifecycle.
2. Add executable contract tests for those specs.
3. Update current code contracts to reflect desktop-first/high-autonomy target while preserving safe quality-core invariants.
4. Choose desktop shell technology and backend protocol.
5. Implement backend task/run API before building UI.
