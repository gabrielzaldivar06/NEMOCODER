# Sprint: Full Handoff Reliability Loop

## Objetivo

Convertir el smoke real de Aider + LM Studio en un loop autonomo confiable: el sistema debe poder fallar una validacion, pasarle evidencia util a Aider, reparar dentro de presupuesto, volver a validar y producir un review package con timeline suficiente para auditar la ejecucion.

## Por Que Este Sprint Tiene Maximo ROI

Ya esta probado que Aider real puede escribir codigo en un runtime aislado usando LM Studio. El mayor riesgo restante para Full Handoff no es la interfaz desktop ni mas investigacion de repos: es que el agente todavia no tiene un ciclo robusto de validacion, reparacion y evidencia. Sin eso, cualquier autonomia de horas seria fragil.

Este sprint incrementa directamente la capacidad central del producto: PRD-to-code sin interaccion humana, con validacion y reparacion medible.

## Alcance

1. Repair v2 con evidencia real
   - Pasar stdout, stderr, returncode, comando fallido, intento actual y diff previo al prompt de reparacion.
   - Mantener `runtime_path`, `repo_path`, `target_files`, `model_profile`, `provider_mode`, `timeout_seconds` y validation output en cada `MutationRequest` de repair.
   - Detener el loop si Aider no cambia archivos en un intento de repair.
   - Registrar cada intento como evento/artifact auditable.

2. Validacion robusta para Windows/local
   - Evitar comandos fragiles con quoting de PowerShell.
   - Agregar un helper que escriba scripts de validacion dentro del runtime y ejecute Python de forma estable.
   - Persistir stdout/stderr completos en artifacts de validacion.

3. Review package enriquecido
   - Incluir diff real, archivos cambiados, validaciones por comando, repair attempts, modelo usado y riesgos pendientes.
   - Marcar `blocked` si hay validacion fallida, no-op repair, o no hubo cambios de implementacion.

4. Smoke real multiarchivo
   - Ejecutar Aider real con `nvidia.agentic.coder-4b` en LM Studio para crear modulo + test.
   - Validar import/test real dentro del runtime.
   - Evaluar JSON final con `eval-run-json`.

## Fuera De Alcance

- Desktop UI.
- Container isolation completa.
- Merge/review-to-main.
- Agentes paralelos de larga duracion.
- Integracion MCP externa completa con NEMO real.

## Entregables

- `repair_engine.py` conserva contexto completo de la mutacion original y usa evidencia de validacion.
- `validation.py` soporta artifacts de script de validacion reproducibles.
- `review_package.py` muestra diff, validation details, repair attempts y model profile.
- Tests unitarios para repair con evidencia, no-op guard, validation script helper y review package enriquecido.
- Smoke real multiarchivo guardado/evaluado y luego limpiado si solo es artefacto temporal.

## Acceptance Criteria

- Un test prueba que el repair prompt contiene comando fallido, output, returncode e intento.
- Un test prueba que el repair mantiene runtime/model/target files del request base.
- Un test prueba que si un repair no cambia archivos, el loop se detiene y queda marcado como no-op/stuck.
- Un test prueba que el review package incluye diff real y resultados por comando.
- Un smoke real con Aider + LM Studio crea al menos dos archivos de implementacion/test y pasa validacion.
- `eval-run-json` retorna `ready` para el smoke exitoso y `blocked` para un fixture con no-op/failure.
- Suites verdes:
  - `$env:PYTHONPATH='src'; c:/dev/dev4/.venv/Scripts/python.exe -m unittest discover -s tests`
  - `$env:PYTHONPATH='product/aider'; c:/dev/dev4/.venv/Scripts/python.exe -m unittest discover -s product/aider/tests/nemo_platform`

## Plan De Implementacion

1. Especificar Repair v2 en tests antes de tocar implementacion.
2. Extender `MutationRequest`/repair context sin romper fake provider ni subprocess provider.
3. Agregar helper de validacion por script para eliminar quoting fragil.
4. Enriquecer review package y persistencia JSON.
5. Ejecutar smoke multiarchivo con Aider real y default LM Studio.
6. Guardar resultado en NEMO como decision/outcome del sprint.

## Riesgos

- El modelo pequeno puede reparar casos simples pero fallar en PRDs ambiguos; el sprint debe medir esto y mantener fallback intercambiable.
- Repair sin guard de no-op puede gastar presupuesto inutilmente; por eso el stop guard es criterio de aceptacion.
- Validacion por shell en Windows puede producir falsos negativos; por eso se prioriza script artifact.

## Siguiente Sprint Despues De Este

Con repair/validation/review confiables y bounded simulation ya validado, el siguiente sprint deberia ser el supervisor de handoff largo: budget de tiempo/tokens, heartbeats, pausa/reanudacion, escalation rules y checkpoints por intervalo para ejecuciones de horas.

## Corte Implementado 2026-05-05

- Repair v2 ahora propaga evidencia de validacion al prompt de reparacion: comando, status, returncode, output, intento y diff previo.
- Los requests de repair conservan provider mode, repo/runtime path, target files, model profile y timeout del request base.
- El repair loop se detiene si un intento no cambia archivos, evitando gastar presupuesto en no-ops.
- El provider subprocess captura timeouts y los convierte en resultados auditables sin traceback.
- `validation.py` incluye un helper para generar scripts Python de validacion reproducibles dentro del runtime.
- El review package incluye provider/model, repair attempts, changed files, diff real y resultados por comando con output.
- Suites verdes: root 86 tests OK, Aider fork 13 tests OK.
- Smoke real multiarchivo con Aider + LM Studio listo: genero `src/hello.py` y `tests/test_hello.py`, valido con `unittest`, y `eval-run-json` retorno `ready` score 1.0.

## Corte Implementado 2026-05-05: Validacion Por Script

- CLI `headless-run` acepta `--validation-python` para escribir codigo Python como script reproducible dentro del runtime.
- El runner agrega scripts `validation_script_N.py` a los comandos de validacion y los reporta en `validation.txt`.
- `validation.txt` y review package ahora incluyen reporte por comando: command, status, returncode y output.
- Si `--validation-python` se usa sin `--validation`, la CLI agrega una validacion base de version de Python y deja el assert real al script generado.
- Suites verdes: root 89 tests OK, Aider fork 13 tests OK.
- Smoke real con Aider + LM Studio listo: genero `src/hello.py`, ejecuto `validation_script_1.py` con `from src.hello import greet; assert greet('Ada') == 'Hello, Ada'`, y `eval-run-json` retorno `ready` score 1.0.

## Corte Implementado 2026-05-05: Runtime Reset Y Checkpoint Artifact

- El runner ya no borra runtimes con `rmtree` directo; usa `reset_runtime_dirs`.
- Si Windows mantiene archivos bloqueados en el runtime anterior, el sistema crea un runtime alternativo con sufijo en vez de fallar con `PermissionError`.
- Cada run Full Handoff escribe `checkpoint.md` dentro del runtime.
- El evento `checkpoint` incluye `payload_ref=checkpoint.md` para replay/auditoria.
- El checkpoint registra fase, provider/model, changed files, validation summary, failures, evidencia NEMO, siguiente accion y risk flags.
- Suites verdes: root 92 tests OK, Aider fork 13 tests OK.
- Smoke real con Aider + LM Studio listo: 7 artifacts, checkpoint incluido, `eval-run-json` retorno `ready` score 1.0.

## Corte Implementado 2026-05-05: Replay De Runs Persistidos

- Nuevo comando CLI `replay-run-json` para compilar una vista replayable desde JSON persistido.
- `build_replay_summary` enlaza timeline, artifact paths, payload refs, runtime files, changed files, validation commands y readiness.
- El replay marca `can_replay=true` solo cuando el run esta listo y tiene eventos/artifacts suficientes.
- Readiness y CLI ahora cuentan cambios efectivos hechos por repair, no solo la mutacion inicial.
- Se cerro un falso negativo real: Aider puede hacer no-op inicial, fallar validacion, reparar el archivo y terminar listo; el score ahora reconoce esa mutacion efectiva.
- Si no hay mutacion inicial ni repair con cambios, la validacion se bloquea para evitar falsos positivos por archivos stale en runtime.
- Suites verdes: root 96 tests OK, Aider fork 13 tests OK.
- Smoke real con Aider + LM Studio listo: `src/farewell.py` fue corregido por repair, `replay-run-json` retorno `can_replay=true`, `ready`, score 1.0.

## Corte Implementado 2026-05-05: Bounded Full Handoff Simulation

- `headless-run` acepta `--bounded-simulation` para ejecutar una simulacion local con checkpoints por fase.
- En bounded mode el timeline tiene 10 eventos replayables: context, plan, checkpoint plan, permission, mutation, validation, checkpoint execute, review package, checkpoint review y memory written.
- El runtime escribe tres checkpoints: `checkpoint-plan.md`, `checkpoint-execute.md` y `checkpoint-review.md`.
- Cada evento `checkpoint` publica su `payload_ref`, de modo que `replay-run-json` puede reconstruir la ruta de handoff por fase.
- Cada checkpoint bounded prepara un writeback `store_conversation` en NEMO, ademas del writeback final de review.
- El modo normal conserva compatibilidad con `checkpoint.md` unico y 8 eventos.
- Suites verdes: root 98 tests OK, Aider fork 13 tests OK.
- Smoke real bounded con Aider + LM Studio listo: genero `src/wave.py`, produjo 9 artifacts y 10 eventos, `replay-run-json` retorno `can_replay=true`, `ready`, score 1.0.

## Corte Implementado 2026-05-05: Supervisor De Handoff Largo

- Nuevo modulo `long_handoff_supervisor.py` para envolver un bounded Full Handoff con budget de runtime, heartbeats, token budget, pausa y escalamiento.
- Nuevo comando CLI `long-handoff-run` para ejecutar el supervisor sin activar suites largas ni requerir una UI.
- El supervisor agrega eventos `heartbeat`, `escalation` y `paused` al timeline replayable.
- El runtime persiste `supervisor-report.md` con budgets, heartbeats y escalation flags.
- Si se configura `--pause-after-minutes`, el runtime persiste `resume-token.txt` y el replay expone el payload ref.
- Validacion acotada segun pedido: focales `tests.test_long_handoff_supervisor tests.test_cli_headless tests.test_persistence` pasaron 15 OK.
- Smoke fake replayable listo: `long-handoff-run` produjo 11 artifacts, 14 events, 2 heartbeats, pause token, `replay-run-json` retorno `can_replay=true`, `ready`, score 1.0.

## Corte Implementado 2026-05-05: Resume Plan Para Handoff Largo

- Nuevo `LongHandoffResumePlan` para reconstruir una reanudacion desde JSON persistido y `resume-token.txt`.
- Nuevo comando CLI `long-handoff-resume <run.json>`.
- El resume plan valida que el run este pausado, que exista artifact `resume-token.txt`, que el archivo siga presente en el runtime y que el token tenga minuto parseable.
- El output incluye `can_resume`, `resume_token`, `resume_minute`, checkpoint refs y `next_action`.
- Validacion acotada: `tests.test_long_handoff_supervisor` paso 4 OK.
- Smoke fake listo: `long-handoff-run` + `long-handoff-resume` reconstruyo `resume_token=task-1:run-1:minute-20`, `resume_minute=20`, `can_resume=true`.

## Corte Implementado 2026-05-05: Continuacion Ejecutable De Handoff Largo

- Nuevo comando CLI `long-handoff-continue <paused-run.json>`.
- Nueva funcion `execute_long_handoff_continuation` para crear un run nuevo enlazado al token de reanudacion anterior, sin mutar el JSON pausado.
- La continuacion usa ids derivados: `task-1-resume` y `run-1-resume-20` para preservar trazabilidad.
- El runtime de continuacion escribe `continuation-link.md` con source task/run, resume token, resume minute y checkpoint refs.
- El timeline agrega evento `resumed` con `payload_ref=continuation-link.md`.
- Validacion acotada: `tests.test_long_handoff_supervisor` paso 6 OK.
- Smoke fake listo: `long-handoff-run` + `long-handoff-continue` + `replay-run-json` produjo 11 artifacts, 12 events, `resumed=true`, `can_replay=true`, `ready`, score 1.0.

## Corte Implementado 2026-05-05: Link De Memoria NEMO Para Continuaciones

- Cada continuacion ahora prepara un writeback NEMO `store_conversation` que enlaza source task/run, continuation task/run, resume token y resume minute.
- El runtime de continuacion escribe `continuation-memory.md` con el mismo resumen persistido en NEMO.
- El resultado agrega un `MemoryTrace` `mem-continuation-link` para auditar la escritura de memoria.
- El replay incluye `continuation-memory.md` como artifact, junto con `continuation-link.md`.
- Validacion acotada: `tests.test_long_handoff_supervisor` paso 6 OK.
- Smoke fake listo: `long-handoff-run` + `long-handoff-continue` + `replay-run-json` produjo 12 artifacts, `can_replay=true`, `ready`, score 1.0.

## Corte Implementado 2026-05-05: Lineage Replay De Handoff Largo

- Nuevo `build_long_handoff_lineage` en persistencia para reconstruir cadenas multi-run desde JSONs persistidos.
- Nuevo comando CLI `long-handoff-lineage <source.json> <continuation.json> ...`.
- El lineage reporta nodos, links, source/continuation run ids, resume token, missing sources/continuations, completitud y readiness de cadena.
- La cadena se reconstruye desde `MemoryTrace`/`continuation-memory.md`, no solo desde nombres de archivo.
- Validacion acotada: `tests.test_long_handoff_supervisor` paso 7 OK.
- Smoke CLI con artifacts existentes listo: 2 nodos, 1 link, `complete=true`, `ready=true`, sin missing sources/continuations.

## Corte Implementado 2026-05-05: Lineage Multi-Hop Y Fork Detection

- `build_long_handoff_lineage` ahora calcula `roots`, `leaves`, `max_depth`, `branches`, `branch_count` y `forked`.
- Soporta cadenas lineales multi-hop como source -> continuation -> continuation.
- Detecta forks cuando un mismo `source_run` tiene mas de una continuation distinta.
- Mantiene compatibilidad del comando `long-handoff-lineage` y agrega los nuevos campos al JSON.
- Validacion acotada: `tests.test_long_handoff_supervisor` paso 9 OK.
- Smoke CLI con artifacts existentes listo: `roots=[run-1]`, `leaves=[run-1-resume-20]`, `max_depth=2`, `forked=false`, `branch_count=0`, `ready=true`.

## Corte Implementado 2026-05-05: Persistent NEMO Memory Store MVP

- Nuevo modulo `memory_persistence.py` con store SQLite local para atoms NEMO, evidence handles y feedback.
- El store reutiliza `MemoryAtom` y `MemoryAtomType` como contrato central, sin crear una memoria paralela incompatible.
- Las consultas soportan filtro por topic, tags y tipos, con orden determinista por importancia, utilidad, acceso y recencia.
- `prime_atoms` prioriza corrections, preferences, decisions, project facts, open loops y session summaries para handoffs largos.
- Evidence handles guardan contenido completo, compact claim, source task/run, hash y retrieval count.
- Feedback registra eventos y actualiza contadores `useful_count` / `not_useful_count` en atoms.
- Validacion acotada: `tests.test_memory_persistence` paso 5 OK.

## Corte Implementado 2026-05-05: Persistent NEMO Adapter MVP

- `PersistentNemoAdapter` conecta el store SQLite con el contrato NEMO existente sin reemplazar el adapter in-memory de tests/smokes previos.
- El adapter conserva `tool_allowed_in_lifecycle`, asi que scheduling sigue bloqueado durante build y solo se aceptan tools permitidas por fase.
- `prime_context` y `context_bootstrap` leen atoms persistidos y devuelven contexto compacto auditable desde `persistent_store`.
- `build_context_portfolio` infiere `ExecutionPhase` desde la fase lifecycle y alimenta el portfolio builder con atoms persistidos filtrados por topic, tags y tipos de la fase.
- `store_conversation`, `create_correction`, `record_context_feedback`, `expand_context_evidence`, `compress_context_artifact` y stats ya tienen comportamiento persistente MVP.
- Validacion acotada: `tests.test_nemo_lifecycle tests.test_nemo_adapter tests.test_memory_persistence` paso 17 OK.

## Corte Implementado 2026-05-05: NEMO Memory DB Wiring

- La CLI ahora acepta `--memory-db` en `headless-run`, `long-handoff-run` y `long-handoff-continue`.
- Cuando se pasa `--memory-db`, el runner usa `PersistentNemoAdapter(PersistentMemoryStore(path))` en lugar del adapter in-memory.
- Los handoffs reales pueden leer contexto desde NEMO persistente y escribir checkpoints, review summaries y continuation links en SQLite.
- `long-handoff-continue` reutiliza el adapter persistente recibido para guardar el link de continuacion, en vez de volver a un store in-memory.
- Esto mantiene NEMO como base de memoria operativa/durable del handoff, mientras el adapter in-memory queda solo como default liviano para tests y simulaciones sin DB.
- Validacion acotada: `tests.test_cli_headless tests.test_long_handoff_supervisor tests.test_nemo_adapter tests.test_memory_persistence` paso 33 OK.

## Corte Implementado 2026-05-05: NEMO Desde El Inicio

- Decision corregida: NEMO persistente es el default de producto desde el arranque del run, no una opcion que haya que recordar activar.
- `headless-run`, `long-handoff-run` y `long-handoff-continue` usan `.nemo-runtimes/nemo-memory.sqlite` por defecto.
- `--memory-db <path>` permite escoger otra base NEMO; `--no-memory-db` queda como opt-out explicito para simulaciones livianas.
- El adapter in-memory deja de ser el default de CLI y queda reservado para llamadas directas de bajo nivel/tests que no quieran persistencia.
- Validacion acotada: `tests.test_cli_headless tests.test_long_handoff_supervisor tests.test_nemo_adapter tests.test_memory_persistence` paso 36 OK.

## Corte Implementado 2026-05-05: Structured NEMO Writeback And Reuse

- El writeback del runner ya no guarda solo summaries genericos: escribe atoms NEMO tipados con topic, tags, source scope, importance y evidence handle cuando aplica.
- Los checkpoints bounded se guardan como `artifact_state` ligados al topic `Headless Handoff` y tags de task/run/fase.
- El review final comprime el validation report como evidence recuperable y enlaza ese handle al `session_summary` del run.
- `store_conversation` en `PersistentNemoAdapter` acepta `atom_type`, `source_scope`, `tags`, `importance` y `evidence_handle` para memoria durable estructurada.
- Un segundo handoff con el mismo store recupera memoria del run anterior desde `prime_context`, cerrando el loop de memoria operativa entre runs.
- Validacion acotada: `tests.test_headless_runner tests.test_nemo_adapter tests.test_memory_persistence tests.test_cli_headless tests.test_long_handoff_supervisor` paso 51 OK.

## Corte Implementado 2026-05-05: Lineage Autonomy Gate

- `build_long_handoff_lineage` ahora expone `autonomy_ready` y `policy_reasons` para separar replay/readiness de seguridad autonoma.
- Nueva politica `evaluate_long_handoff_continuation_policy` detecta si un source run ya tiene continuaciones y bloquea forks no autorizados.
- `long-handoff-continue` acepta `--lineage-context <run.json>` repetible para evaluar contexto de lineage antes de iniciar una continuacion.
- `--allow-fork` queda como permiso explicito para crear ramas cuando el operador lo decide.
- Si la politica bloquea, la CLI retorna codigo 1 con `continuation_blocked_by_lineage_policy`, razones y continuaciones existentes, sin gastar presupuesto de ejecucion/modelo.
- Validacion acotada: `tests.test_long_handoff_supervisor tests.test_cli_headless tests.test_headless_runner tests.test_memory_persistence` paso 46 OK.

## Corte Implementado 2026-05-05: Auto Lineage Gate Desde NEMO

- `long-handoff-continue` ya no requiere `--lineage-context` para protegerse contra forks accidentales.
- Si NEMO persistente esta activo, la CLI lee summaries desde el SQLite store y detecta links `Long handoff continuation linked ...` asociados al source run.
- Nueva politica `evaluate_long_handoff_memory_policy` bloquea continuaciones duplicadas usando memoria NEMO durable.
- `--lineage-context` sigue disponible para evaluacion manual desde JSONs; `--allow-fork` sigue siendo el override explicito.
- Tests antiguos de lineage manual usan `--no-memory-db` cuando quieren evitar la compuerta automatica, dejando claro que el default de producto es memoria NEMO con gate activo.
- Validacion acotada: `tests.test_long_handoff_supervisor tests.test_cli_headless tests.test_headless_runner tests.test_memory_persistence` paso 49 OK.

## Corte Implementado 2026-05-05: Review-To-Main Gate MVP

- Nuevo modulo `review_gate.py` para convertir un run JSON listo en un merge plan seguro desde sandbox hacia repo principal.
- `build_merge_plan` valida readiness, validation, changed files, source files y path containment antes de marcar un run como mergeable.
- `apply_merge_plan` requiere aprobacion explicita de review y solo copia archivos listados en el plan; no aplica archivos no planeados.
- Nuevo comando `review-run-json <run.json>` para inspeccionar el plan en JSON o markdown.
- Nuevo comando `apply-run-json <run.json> --approve-review` para aplicar creates/updates desde sandbox al repo principal tras gate explicito.
- `--save-plan` permite persistir `merge-plan.md` como artefacto auditable.
- Este corte cierra el flujo PRD -> sandbox -> validacion -> review -> apply seguro, sin permitir merge automatico sin aprobacion.
- Validacion acotada: `tests.test_review_gate tests.test_review_gate_cli tests.test_cli_headless tests.test_headless_runner tests.test_long_handoff_supervisor tests.test_persistence` paso 58 OK.

## Corte Implementado 2026-05-05: Apply Audit And NEMO Writeback

- `MergeApplyResult` ahora renderiza `apply-report.md` con task/run, aprobacion y archivos aplicados.
- `apply-run-json` acepta `--save-apply-report` para persistir el reporte de aplicacion despues del gate.
- `apply-run-json` escribe memoria NEMO por defecto sobre el outcome `Review-to-main apply completed ...` como atom `decision` con tags de task/run/apply.
- `--memory-db` permite elegir la base NEMO del writeback; `--no-memory-db` omite la escritura cuando se quiere una simulacion sin memoria.
- La aplicacion al repo principal queda ahora auditable por plan, reporte y memoria durable.
- Validacion acotada: `tests.test_review_gate tests.test_review_gate_cli tests.test_cli_headless tests.test_headless_runner tests.test_long_handoff_supervisor tests.test_persistence tests.test_nemo_adapter tests.test_memory_persistence` paso 75 OK.

## Corte Implementado 2026-05-05: Apply Backup And Hash Evidence

- `MergePlan` ahora registra hashes SHA-256 de source y target para cada archivo planeado, exponiendolos en JSON y markdown.
- `apply_merge_plan` crea backups automaticos antes de sobrescribir archivos existentes, preservando la ruta relativa bajo `.nemo-apply-backups/<task>-<run>` por default.
- `apply-run-json` acepta `--backup-dir` para dirigir esos backups a una ruta explicita cuando el operador quiere controlar el paquete de rollback.
- `MergeApplyResult` y `apply-report.md` ahora reportan `backup_dir` y `backup_files`, haciendo reversible un update aprobado sin depender de memoria informal.
- El comportamiento de creates se mantiene simple: no generan backup, pero siguen auditados por plan, reporte y memoria NEMO.
- Validacion acotada: `tests.test_review_gate tests.test_review_gate_cli` paso 16 OK.
- Validacion focal ampliada: `tests.test_review_gate tests.test_review_gate_cli tests.test_cli_headless tests.test_headless_runner tests.test_long_handoff_supervisor tests.test_persistence tests.test_nemo_adapter tests.test_memory_persistence` paso 77 OK.

## Corte Implementado 2026-05-05: Apply Conflict Detection And Rollback

- `apply_merge_plan` ahora revalida el hash del target justo antes de copiar; si el archivo cambio despues de construir el plan, bloquea con `target changed since merge plan was built`.
- `MergeApplyResult` ahora incluye `repo_path`, `created_files` y `updated_files`, ademas de backups, para que el resultado sea rollbackable de forma estructurada.
- `apply-run-json` acepta `--save-apply-json` para guardar un resultado JSON reproducible que puede usarse despues para rollback.
- Nuevo comando `rollback-apply-json <apply.json> --approve-review` restaura archivos actualizados desde backup y elimina archivos creados por el apply.
- `rollback-apply-json` puede guardar `rollback-report.md` con archivos restaurados y deletes aplicados.
- El rollback tambien requiere aprobacion explicita de review, manteniendo la misma frontera de seguridad que apply.
- Validacion acotada: `tests.test_review_gate tests.test_review_gate_cli` paso 20 OK.
- Validacion focal ampliada: `tests.test_review_gate tests.test_review_gate_cli tests.test_cli_headless tests.test_headless_runner tests.test_long_handoff_supervisor tests.test_persistence tests.test_nemo_adapter tests.test_memory_persistence` paso 81 OK.

## Corte Implementado 2026-05-05: Desktop Mission Control Spike

- Se documento la busqueda de interfaces reutilizables en `docs/research/desktop-ui-candidates.md`.
- Decision de producto: usar direccion tipo Codeg para mission-control, patrones de Async IDE para loop/aprobaciones/diff, OpenCowork para settings/MCP/permisos y OpenCove para canvas/timeline futuro.
- Nuevo core `mission_control.py` proyecta runs persistidos en un JSON de UI: repos, runs, approval queue, timeline preview, review status y settings default.
- Nuevo comando `mission-control-state` exporta estado desde `.nemo-runtimes` y puede guardar JSON para el frontend.
- Nuevo spike `apps/mission-control` con Vite/React: workbench tipo Codex/VS Code con activity bar, explorer de runs, editor central repo-vs-sandbox, panel agente, timeline inferior y settings de modelo/NEMO/Aider.
- Nuevo comando `mission-control-server` expone un bridge local stdlib HTTP para `/api/state`, `/api/file`, `/api/handoff`, `/api/review`, `/api/apply` y `/api/rollback`.
- El frontend consume el bridge local con fallback a `apps/mission-control/public/mission-control-state.sample.json`, generado desde el backend real.
- La UI ya puede refrescar estado real, construir planes de merge, aplicar runs aprobados con JSON de rollback y ejecutar rollback desde ese apply JSON.
- El boton New Handoff abre un composer tipo Codex y crea un long handoff supervisado en sandbox, persistido como run JSON discoverable por Mission Control.
- El composer permite elegir `Fake smoke runner` o `Aider + LM Studio`; el backend valida provider/timeout y ejecuta `provider_mode=subprocess` contra `product/aider` cuando se elige el runner real.
- La salida stdout/stderr/returncode de Aider se persiste como `aider-output.txt`, se registra como artifact `aider_output` y queda enlazada desde el evento `mutation_created` del timeline.

## Corte Implementado 2026-05-06: Handoff Asincrono En Mission Control

- Nuevo `HandoffJobManager` en `mission_control_server.py`: lanza `long-handoff-run` como subprocess local, captura stdout/stderr incrementalmente, guarda `run_json`, y mantiene estado `starting/running/completed/failed/cancelled/paused`.
- Nuevos endpoints del bridge local: `POST /api/handoff/start`, `GET /api/jobs`, `POST /api/job`, `POST /api/job/cancel`, `POST /api/job/pause`, `POST /api/job/resume`.
- `long-handoff-run` acepta `--task-id` y `--run-id` para que los jobs async tengan trazabilidad estable entre proceso, archivo JSON y Mission Control.
- La UI deja de bloquearse con `/api/handoff`: ahora inicia jobs async, hace polling de estado, muestra logs incrementales en el bottom panel y ofrece Pause/Resume/Cancel.
- Validacion acotada: `tests.test_mission_control_server tests.test_mission_control tests.test_headless_runner tests.test_aider_interface` paso 32 OK; `npm run build` paso OK.

## Corte Implementado 2026-05-06: Chat Agent Loop Tipo Codex

- Nuevo endpoint `POST /api/agent/message` en el bridge local: valida prompt, inspecciona contexto Mission Control/NEMO, revisa el run seleccionado y devuelve mensaje assistant con `tool_calls` visibles.
- Las respuestas proponen acciones estructuradas `continue`, `revise` y `apply`; `apply` queda conectado al review gate y `continue/revise` lanzan handoff async con payload prellenado.
- El panel Agent Control ahora tiene hilo conversacional, prompt box, tool calls renderizadas y botones de acciones propuestas, manteniendo la interfaz densa tipo IDE.
- Validacion acotada: `tests.test_mission_control_server tests.test_mission_control` paso 11 OK; `npm run build` paso OK; smoke en bridge vivo confirmo `nemo.prime_context`, `mission_control.inspect_run`, `mission_control.build_merge_plan` y acciones `apply/continue/revise`.

## Corte Implementado 2026-05-06: Diff Review Real

- `POST /api/file` ahora devuelve hunks linea por linea, metadata de mergeability y checklist de riesgos por plan/archivo.
- Nuevo endpoint `POST /api/apply-selection`: aplica solo hunks aceptados, verifica hashes del target antes de escribir, crea backups para updates y guarda apply JSON compatible con rollback.
- La UI reemplaza el preview repo-vs-sandbox simple por una superficie de review con hunks visuales, checkboxes por hunk, Accept/Reject file y Apply selected.
- Validacion acotada: `tests.test_mission_control_server tests.test_mission_control` paso 12 OK; `npm run build` paso OK; smoke en navegador confirmo diff con 2 hunks, rechazo de hunk y contador de aceptados.

## Corte Implementado 2026-05-06: NEMO Operativo En UI

- Nuevo endpoint `POST /api/nemo`: combina run seleccionado, context portfolio, memory traces, atom IDs usados, corrections, evidence, feedback y health del store persistente.
- La UI agrega panel `NEMO Memory` en Agent Control con health del DB, portfolio visible, memorias usadas, corrections, evidence handles y eventos de feedback.
- El panel se refresca al cambiar de run y usa el mismo bridge local, manteniendo NEMO como base de memoria operativa y largo plazo desde la interfaz.
- Validacion acotada: `tests.test_mission_control_server tests.test_mission_control` paso 13 OK; `npm run build` paso OK; smoke en navegador confirmo 36 atoms, 1 evidence, 5 feedback, portfolio y memories usadas visibles.

## Corte Implementado 2026-05-06: Settings, Repo Picker Y Apply UX

- Settings persistibles en `.nemo-runtimes/mission-control/settings.json`: LM Studio URL, modelo, provider, NEMO DB, runtime path, timeouts y presupuestos de handoff.
- Repo picker real: abrir path validando `.git`, mantener recientes y clonar desde git hacia una carpeta destino antes de abrirla.
- Apply/Rollback endurecido: el apply construye y muestra plan antes de confirmar, el backend conserva bloqueo por hash cambiado, y la UI lista historial de apply con backups/rollback artifacts.
- Limpieza de artefactos: endpoint de scan/delete para artefactos temporales antiguos y controles visibles en Settings.
- Validacion acotada: `tests.test_mission_control_server tests.test_mission_control` paso 15 OK; `npm run build` paso OK.