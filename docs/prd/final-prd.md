# PRD Final: Space Code

## 1. Resumen Ejecutivo

Space Code es una app desktop local-first para coding agentico serio. La base activa del producto es `product/aider`, un fork modificable de Aider. Aider aporta el nucleo confiable de edicion, repo map, git workflow, validacion y compatibilidad con modelos locales. Sobre esa base se agregan las capacidades que faltan para convertirlo en un entorno autonomo moderno:

- OpenCode aporta el modelo de workflow plan/build/review, permisos ergonomicos, agentes configurables y experiencia de CLI/TUI seria.
- OpenHands aporta runtime agentico fuerte: sandbox, tareas largas, terminal persistente, subagentes, event stream, pause/resume/cancel y ejecucion aislada.
- NEMO aporta memoria operativa y de largo plazo por MCP: contexto inicial, portfolios, evidencia, correcciones, conversacion, feedback, reminders, appointments, health y herramientas de mantenimiento.

La estrategia optima no es reescribir Aider ni construir una app desde cero. La estrategia optima es usar Aider como motor de calidad y modificarlo por capas: primero contratos testeables, luego adapters internos, despues runtime aislado, y finalmente desktop UI.

La caracteristica principal del producto es Full Handoff: un agente capaz de recibir un PRD, convertirlo en specs ejecutables, implementar durante horas sin interaccion humana, validar, reparar fallos, producir un paquete de review y escribir memoria durable en NEMO.

## 2. Tesis del Producto

El usuario no necesita otro chat de codigo. Necesita un entorno donde un agente pueda actuar como ingeniero autonomo supervisable: entiende el repo, recupera memoria historica, diseña un plan, trabaja en un worktree aislado, aplica cambios con el motor de Aider, ejecuta pruebas, revisa resultados, pide permisos cuando corresponde y escribe memoria durable en NEMO.

La promesa central es:

> Calidad de edicion de Aider + autonomia operativa de OpenHands + flujo y permisos de OpenCode + memoria NEMO completa.

## 3. Producto Objetivo

### 3.1 Superficie Final

La superficie final es una app desktop independiente de VS Code.

Debe incluir:

- Mission Control: repos, tareas activas, agentes, sandboxes, NEMO, modelo local y cola de aprobaciones.
- Task Workspace: objetivo, specs, plan, subtareas, diffs, pruebas, memoria usada y timeline.
- Diff/Review UI: inspeccion, aprobacion, rechazo, rollback y merge desde sandbox.
- Agent Timeline: traza completa de contexto, herramientas, comandos, archivos, decisiones y validaciones.
- Settings: LM Studio/OpenAI-compatible, servidores MCP, permisos, autonomia y perfiles de agentes.

### 3.2 Superficie Inicial

La CLI y los tests son harness de desarrollo, no el producto final. Se usan para estabilizar contratos antes de construir la UI desktop.

## 4. Decisiones No Negociables

1. `product/aider` es la base activa del producto.
2. `reference-repos/*` son referencias upstream de comparacion, no fuente activa.
3. Solo el Quality Core basado en Aider puede escribir codigo fuente.
4. Alta autonomia siempre requiere aislamiento por worktree o contenedor.
5. El workspace principal nunca se modifica directamente en modo autonomo.
6. Todo merge al workspace principal requiere review gate.
7. NEMO no es plugin opcional ni solo portfolios; es el plano completo de memoria MCP.
8. Todas las llamadas MCP deben pasar por permisos, riesgo, fase y auditoria.
9. Toda capacidad nueva debe tener spec y test antes de considerarse producto.
10. La app debe ser local-first y funcionar con LM Studio u otros endpoints OpenAI-compatible.
11. Full Handoff es requisito principal: el agente debe poder continuar PRD-to-code por horas sin prompts humanos dentro de un sandbox, pero nunca mergear sin review.
12. El modelo pequeno default para pruebas locales via LM Studio es `nvidia.agentic.coder-4b`, servido desde `http://localhost:1234/v1`. Debe ser intercambiable por perfil/configuracion, no hardcodeado como unica opcion.

## 5. Usuarios Objetivo

1. Desarrollador individual con modelos locales.
2. Power user que quiere una alternativa desktop a VS Code para coding agentico.
3. Ingeniero en repos grandes que necesita cambios multiarchivo confiables.
4. Constructor de agentes que quiere MCP, memoria semantica y evaluaciones.
5. Equipo pequeño que necesita politicas, auditoria y ejecucion local/self-hosted.

## 6. Reglas de Negocio

### BR1: Aider Es El Motor De Mutaciones

Toda modificacion de codigo debe convertirse en una operacion compatible con el modelo de edicion de Aider: repo awareness, diff, validacion, aplicacion controlada y artefacto git.

Razon: Aider ya resolvio una parte dificil del producto: editar repos reales de forma confiable.

### BR2: OpenCode Define El Workflow Visible

Toda tarea debe pasar por fases explicitas:

1. plan
2. build
3. review
4. close

Plan no escribe codigo. Build propone y ejecuta mutaciones permitidas. Review valida, resume, bloquea o aprueba. Close escribe memoria y artefactos finales.

### BR3: OpenHands Define El Runtime Autonomo

Toda tarea autonomica debe tener runtime propio con estado, identidad, sandbox, terminal, logs y lifecycle.

Estados minimos:

- initializing
- ready
- planning
- building
- reviewing
- paused
- completed
- failed

### BR4: NEMO Se Usa En Todo El Ciclo

Cada task run debe llamar a NEMO en puntos fijos:

- Start: `prime_context` o `context_bootstrap`.
- Plan: portfolio/contexto acotado y memoria relevante.
- Build: expansion de evidencia y feedback contextual si hace falta.
- Review: correcciones, health si hay sospecha, feedback y memoria final.
- Close: `store_conversation`, decisiones, resultados y follow-ups explicitos.

### BR5: Permisos Por Riesgo Y Fase

Cada accion debe declarar:

- actor
- fase
- permiso requerido
- target
- riesgo
- decision: allow, ask o deny
- regla que decidio

El sistema debe favorecer allow para acciones seguras y repetibles, ask para riesgo medio, deny para escapes de sandbox o acciones destructivas.

### BR6: Scheduling Siempre Requiere Intencion Explicita

Herramientas NEMO de reminders y appointments solo se ejecutan en review/close y solo si la intencion del usuario es clara.

### BR7: Memoria Durable No Debe Contaminarse

NEMO solo debe guardar memoria durable cuando exista valor futuro: correcciones, decisiones, preferencias, resultados de tarea, arquitectura y errores importantes. No debe guardar ruido de ejecucion trivial.

### BR8: Autonomia Escala Por Confianza

La autonomia aumenta por perfil y por repo, no globalmente a ciegas.

Niveles:

- Manual: todo ask.
- Assisted: allow para lectura/validacion, ask para writes.
- Autonomous Sandbox: allow dentro de worktree, ask para merge.
- Background Agent: allow dentro de contenedor/worktree con checkpoints.

### BR9: Validacion Bloquea Cierre

Una tarea con cambios de codigo no puede cerrarse como exitosa sin validacion registrada o sin una razon explicita de por que no se pudo validar.

### BR10: Cada Run Es Reproducible

El sistema debe guardar suficiente timeline para reconstruir:

- contexto NEMO usado
- plan
- permisos
- comandos
- diffs
- tests
- errores
- decisiones humanas
- memoria escrita

### BR11: Full Handoff Es Requisito Principal

El producto debe soportar un modo donde el usuario entrega un PRD/spec y el agente puede trabajar durante horas sin interaccion humana.

Full Handoff debe:

- convertir PRD en specs y tests
- crear plan de implementacion
- ejecutar cambios solo en sandbox/worktree/contenedor
- usar Aider como Quality Core para editar
- usar NEMO para contexto, evidencia, checkpoints y memoria final
- ejecutar validaciones y repair loops dentro de un presupuesto
- emitir checkpoints periodicos
- pausar o degradar autonomia si queda atascado
- producir review package antes de merge

Full Handoff no significa permiso irrestricto. Significa continuidad operativa sin prompts humanos dentro de limites preaprobados.

## 7. Specs Funcionales

### SPEC-01: Active Aider Fork

Objetivo: convertir `product/aider` en el nucleo activo del producto.

Requisitos:

- Mantener compatibilidad con Aider upstream cuando sea razonable.
- Agregar extensiones bajo `aider.nemo_platform`.
- No modificar `reference-repos/aider`.
- Proteger cambios con tests focalizados.

Acceptance:

- `product/aider/aider/nemo_platform` existe.
- Tests del fork pasan.
- Documentacion declara que Aider es base activa.

### SPEC-02: Permission Core

Objetivo: implementar permisos estilo OpenCode dentro de Aider.

Requisitos:

- Reglas allow/ask/deny.
- Precedencia deterministica.
- Permisos por comando, tool, edit, network, external directory y merge.
- Configurable por perfil/agente/repo.

Acceptance:

- Acciones de red externas son deny por defecto.
- Merge al main workspace es ask.
- Validaciones seguras pueden ser allow.
- Tests cubren precedencia y defaults.

### SPEC-03: Autonomy Model

Objetivo: definir niveles de autonomia y gates.

Requisitos:

- Manual, Assisted, Autonomous Sandbox, Background Agent.
- Alta autonomia requiere worktree o contenedor.
- Subagentes no escriben main workspace directamente.
- Review antes de merge.

Acceptance:

- Cada nivel mapea a permisos e isolation.
- Autonomous Sandbox no puede escribir main workspace directo.
- Background Agent requiere aislamiento fuerte.

### SPEC-04: Runtime Core

Objetivo: incorporar runtime estilo OpenHands.

Requisitos:

- State machine.
- Task/run identity.
- Terminal persistente.
- Event stream.
- Pause/resume/cancel.
- Sandbox lifecycle.

Acceptance:

- Transiciones invalidas fallan.
- Runs pueden pausarse y resumirse.
- Logs y eventos quedan asociados al run.

### SPEC-05: NEMO Full MCP Memory Plane

Objetivo: usar toda la suite NEMO como memoria operativa y durable.

Requisitos:

- Registry por suite, riesgo, fase y proposito.
- Herramientas de startup context, portfolios, evidence, feedback, corrections, conversation, reminders, appointments, health y maintenance.
- Scheduling review-gated.
- Corrections disponibles en todas las fases.

Acceptance:

- Tests prueban que NEMO no es portfolio-only.
- Herramientas de scheduling no aparecen en build.
- Start y close de tarea tienen writeback definido.

### SPEC-06: Task And Run Model

Objetivo: introducir entidades de tarea y ejecucion.

Requisitos:

- Task: objetivo, repo, specs, estado, owner, autonomia.
- Run: runtime, modelo, NEMO context, permisos, artefactos, eventos.
- Artifacts: plan, patch, diff, test output, memory trace, final report.

Acceptance:

- Una tarea puede tener multiples runs.
- Un run puede generar timeline reproducible.
- UI y CLI consumen el mismo contrato.

### SPEC-07: Quality Mutation Pipeline

Objetivo: enrutar todos los writes por Aider/Quality Core.

Requisitos:

- Draft patch.
- Dry run.
- Permission evaluation.
- Apply in sandbox/worktree.
- Validate.
- Review.
- Merge gate.

Acceptance:

- No hay write directo fuera del pipeline.
- Cambios fuera de workspace fallan.
- Aplicacion sin aprobacion falla.

### SPEC-08: MCP Tool Plane

Objetivo: hacer de MCP una extension gobernada, no herramientas anonimas.

Requisitos:

- Registry de servers/tools/resources/prompts.
- Risk mapping.
- Phase gates.
- Audit trail.
- Per-tool permission decisions.

Acceptance:

- Cada tool call registra server, tool, args resumidos, fase, permiso y resultado.
- Tools destructivas o externas requieren ask/deny por default.

### SPEC-09: Desktop Mission Control

Objetivo: construir la UI desktop cuando backend contracts esten estables.

Requisitos:

- Repo picker.
- Task list.
- Agent status.
- Sandbox status.
- NEMO status.
- Approval queue.
- Timeline.
- Diff review.

Acceptance:

- La app inicia sin VS Code.
- Puede abrir repo, crear tarea y lanzar run.
- Puede aprobar/rechazar acciones.

### SPEC-10: Evaluation And Replay

Objetivo: medir fiabilidad antes de aumentar autonomia.

Requisitos:

- Fixtures de tareas.
- Replay de timeline.
- Scoring: pass rate, validation rate, permission incidents, memory usefulness, rollback rate.
- Regression suite.

Acceptance:

- Cada feature autonomica tiene eval o test.
- No se sube nivel de autonomia sin metricas basicas.

### SPEC-11: Full Handoff PRD-To-Code

Objetivo: permitir que un agente genere codigo desde PRD y Spec Driven Development durante horas sin interaccion humana.

Requisitos:

- Input PRD/objective.
- Generacion o actualizacion de specs.
- Tests o contract checks antes de implementacion cuando sea posible.
- Runtime aislado.
- Checkpoints periodicos.
- Repair loops con presupuesto.
- Final review package.
- Memory writeback NEMO.

Acceptance:

- El nivel `full_handoff` existe en contratos de autonomia.
- Full Handoff requiere SDD, checkpoints y aislamiento fuerte.
- Full Handoff puede continuar sin interaccion humana.
- Full Handoff no puede escribir directo al workspace principal.
- Full Handoff requiere review antes de merge.

## 8. Specs De Datos

### Task

- id
- repo_path
- title
- objective
- autonomy_level
- status
- created_at
- updated_at
- linked_specs

### Run

- id
- task_id
- runtime_id
- state
- model_profile
- sandbox_path
- started_at
- completed_at
- failure_reason

### Permission Decision

- id
- run_id
- phase
- permission
- target
- action
- matched_rule
- created_at

### Tool Event

- id
- run_id
- phase
- server
- tool
- risk
- permission_decision
- started_at
- completed_at
- status
- summary

### Memory Trace

- id
- run_id
- nemo_tool
- suite
- risk
- evidence_handle
- memory_ids
- writeback_type
- summary

### Artifact

- id
- run_id
- type
- path
- content_hash
- summary
- created_at

## 9. Arquitectura Objetivo

### 9.1 Quality Core

Base: Aider.

Responsable de repo map, patching, diff, git, validation y apply.

### 9.2 Workflow Core

Inspirado en OpenCode.

Responsable de fases, permisos, comandos, agentes configurables y task hierarchy.

### 9.3 Runtime Core

Inspirado en OpenHands.

Responsable de sandbox, terminal, long-running tasks, subagentes, browser/computer tools futuros y event stream.

### 9.4 NEMO Memory Plane

Responsable de memoria MCP, contexto, writeback, evidencia y continuidad.

### 9.5 Desktop Shell

Responsable de UX, aprobaciones, timeline, diff review y configuracion.

## 10. Roadmap Optimo Para Llegar Rapido

### Slice 1: Contratos En Aider Fork

Estado: iniciado.

Entregar:

- `aider.nemo_platform.permissions`
- `aider.nemo_platform.autonomy`
- `aider.nemo_platform.runtime`
- `aider.nemo_platform.nemo_tools`
- tests focalizados

### Slice 2: Wiring Minimo En Aider CLI

Entregar:

- comando/flag para inspeccionar contratos NEMO platform
- carga de perfil de permisos
- modo plan/build/review no invasivo
- audit log local por run

### Slice 3: Task/Run Backend

Entregar:

- entidades Task y Run
- timeline persistente
- permission events
- tool events
- memory trace

### Slice 4: NEMO MCP Client Adapter

Entregar:

- adapter MCP para llamar NEMO real
- phase/risk gating
- start/close lifecycle
- evidence expansion
- writeback controlado

### Slice 5: Worktree Runtime

Entregar:

- creacion de worktree por tarea
- ejecucion de comandos en sandbox
- apply/review/merge gate
- pause/resume basico

### Slice 6: Desktop MVP

Entregar:

- repo picker
- task workspace
- timeline
- approval queue
- diff review
- NEMO memory trace
- model settings para LM Studio

### Slice 7: Autonomia Alta

Entregar:

- background runs
- subagents
- checkpointing
- replay/evals
- contenedor opcional

### Slice 8: Full Handoff PRD-To-Code

Entregar:

- PRD parser/ingestion contract
- spec generation/update artifacts
- unattended run loop con checkpoints
- validation and repair budget
- final review package
- NEMO writeback de decisiones y resultados
- replay de una sesion completa

## 11. Definition Of Done

Una capacidad se considera lista solo si cumple:

1. Spec escrita.
2. Test unitario o contract test.
3. Integracion minima en `product/aider` o backend harness.
4. Politica de permisos declarada.
5. Interaccion NEMO definida si aplica.
6. Validacion ejecutada.
7. Documentacion corta actualizada.
8. Resultado registrado en memoria NEMO cuando sea una decision duradera.

## 12. MVP Realista

El MVP no debe intentar copiar todo OpenHands ni todo OpenCode. Debe demostrar el camino correcto:

1. Abrir repo.
2. Crear tarea.
3. Recuperar contexto NEMO.
4. Generar plan.
5. Ejecutar build en worktree.
6. Aplicar cambios con Aider.
7. Correr validaciones.
8. Mostrar diff y timeline.
9. Aprobar merge.
10. Escribir memoria final en NEMO.

Si eso funciona de punta a punta, el producto ya tiene forma real.

## 13. Riesgos Principales

### Riesgo: Fork De Aider Dificil De Mantener

Mitigacion: extensiones bajo `aider.nemo_platform`, cambios pequenos, tests focalizados, comparar con upstream reference.

### Riesgo: Autonomia Sin Control

Mitigacion: worktrees, permisos, review gates, audit trail y replay.

### Riesgo: NEMO Guarda Ruido

Mitigacion: writeback por fases, reglas de valor durable y feedback.

### Riesgo: Desktop UI Antes Del Backend

Mitigacion: backend contracts primero, UI despues de Task/Run API estable.

### Riesgo: MCP Tools Demasiado Poderosas

Mitigacion: registry, risk mapping, explicit permission decision y default deny para red/destructivas.

## 14. Proxima Implementacion

La siguiente tarea de desarrollo debe ser `Slice 2: Wiring Minimo En Aider CLI`.

Primeras acciones:

1. Agregar comando o flag en Aider para imprimir platform info.
2. Exponer permisos default, niveles de autonomia y tools NEMO por fase.
3. Agregar tests dentro del fork.
4. Crear audit event model minimo.
5. Mantener todo sin invadir todavia el flujo principal de Aider.

Este orden maximiza velocidad porque mantiene el producto sobre Aider, evita UI prematura, y convierte OpenCode/OpenHands/NEMO en capacidades reales incrementalmente testeables.

## 15. Correcciones De Rumbo

La reflexion critica del PRD esta documentada en `docs/prd/prd-critical-reflection.md`. Sus correcciones principales son obligatorias para evitar sobreconstruccion:

1. Aider y NEMO son motores maduros, pero su integracion no es trivial; debe hacerse con adapters y contratos.
2. Desktop es la superficie final, no el primer riesgo tecnico; primero debe existir un run headless reproducible.
3. De OpenCode y OpenHands se deben importar principios y capacidades especificas, no copiar sistemas completos de golpe.
4. Alta autonomia debe ganarse con metricas, replay y validation pass rate, no activarse por confianza teorica.
5. NEMO debe usarse siempre, pero con economia: contexto compacto al inicio, evidencia bajo demanda y writeback durable solo en review/close.

Por tanto, antes del Desktop MVP debe existir un Headless Run MVP:

1. tarea real en repo local
2. bootstrap NEMO
3. plan
4. permisos
5. worktree sandbox
6. patch con Aider
7. validacion
8. review gate
9. memory writeback revisable
10. timeline reproducible

Despues del Headless Run MVP, el siguiente hito critico es Full Handoff MVP: el mismo flujo debe poder ejecutarse desde un PRD/spec durante un periodo prolongado sin prompts humanos, con checkpoints, presupuesto de reparacion, aislamiento fuerte y review package final.