# Spec Driven Development Roadmap

This document turns the global PRD into an implementation sequence. Every major capability must move through:

1. PRD section
2. architecture spec
3. executable contract test
4. implementation slice
5. integration/eval
6. memory writeback

## Spec Set 1: Product Direction

### Spec: Desktop Product Contract

File target: `docs/specs/desktop-product-contract.md`

Must define:

- desktop shell technology
- backend protocol
- repo picker
- task workspace
- approval queue
- artifact timeline
- memory trace panel
- model configuration

Executable tests:

- desktop product target is represented in architecture contracts
- CLI is marked as harness, not final UI
- task workspace entities exist

### Spec: Autonomy Model

File target: `docs/specs/autonomy-model.md`

Must define:

- autonomy levels
- sandbox requirements
- merge gates
- subagent limits
- background run behavior
- pause/resume/cancel semantics

Executable tests:

- each autonomy level maps to permissions
- high autonomy requires sandbox/worktree isolation
- main workspace writes still route through Quality Core

### Spec: NEMO Full Tooling

File target: `docs/specs/nemo-full-tooling.md`

Must define:

- all NEMO tool suites
- phase access
- risk levels
- writeback lifecycle
- scheduling restrictions
- evidence expansion rules

Executable tests:

- all suites are represented
- scheduling tools are review-gated
- corrections can be written in all phases
- portfolio tools are not treated as the only NEMO integration

## Spec Set 2: Backend Engine

### Spec: Aider Platform Wiring

File target: `docs/specs/aider-platform-wiring.md`

Must define:

- platform info command or entrypoint
- autonomy contract serialization
- permission default serialization
- NEMO tool contract serialization
- non-invasive Aider fork integration boundary

Executable tests:

- platform contracts can be inspected from the Aider fork
- output includes Full Handoff
- output includes NEMO tools beyond portfolios

### Spec: Task And Run Model

File target: `docs/specs/task-run-model.md`

Must define:

- Task, Run, Event, Artifact, and Memory Trace entities
- append-only timeline
- replay requirements
- checkpoint requirements for Full Handoff

Executable tests:

- records can be instantiated
- Full Handoff runs require checkpoints and review package
- CLI and desktop can consume the same model

### Spec: Quality Core

File target: `docs/specs/quality-mutation-pipeline.md`

Must define:

- workspace boundary rules
- diff/patch model
- dry-run
- apply
- rollback
- validation commands
- git/worktree integration

Executable tests:

- cannot write outside workspace
- cannot apply without dry-run
- cannot apply without approval
- can represent diff artifacts

### Spec: Runtime Core

File target: `docs/specs/worktree-runtime.md`

Must define:

- process backend
- worktree backend
- future container backend
- state machine
- event stream
- terminal runner

Executable tests:

- valid state transitions
- pause/resume/cancel
- runtime identity per task
- sandbox required by high autonomy

### Spec: Orchestration Core

File target: `docs/specs/headless-run-mvp.md`

Must define:

- planner
- builder
- reviewer
- subagent scheduler
- task graph
- artifact trace

Executable tests:

- plan mode cannot write
- builder can create mutation plans
- reviewer blocks finalize when validations fail
- subagents cannot write main workspace directly

## Spec Set 3: Desktop MVP

### Spec: NEMO Lifecycle

File target: `docs/specs/nemo-lifecycle.md`

Must define:

- start/plan/build/review/close NEMO calls
- phase gates
- memory writeback rules
- checkpoint writeback for Full Handoff

Executable tests:

- NEMO is not portfolio-only
- plan/build cannot schedule reminders or appointments
- review/close can write durable memory

### Spec: Full Handoff Autonomy

File target: `docs/specs/full-handoff-autonomy.md`

Must define:

- PRD-to-code lifecycle
- unattended execution constraints
- checkpoint cadence
- repair loop budget
- final review package
- NEMO writeback

Executable tests:

- `full_handoff` autonomy exists
- it can continue without human interaction
- it remains sandboxed and review-gated

## Spec Set 4: Desktop MVP

### Spec: Desktop UI Flows

Must define:

- launch
- connect model
- connect NEMO
- open repo
- create task
- run agent
- approve actions
- review patch
- inspect memory trace

Executable tests:

- UI contract snapshots or component tests once frontend exists
- backend API contract tests before frontend exists

## Spec Set 5: Evaluation

### Spec: Agent Evals

Must define:

- benchmark task format
- replay format
- scoring
- memory incidents
- permission pressure
- validation pass rate

Executable tests:

- replay fixtures load
- run logs can be scored
- metrics can be exported

## Immediate Implementation Order

1. Add Aider platform wiring serializer and tests.
2. Add Task/Run/Event/Artifact/Memory Trace contracts and tests.
3. Add NEMO lifecycle contract tests beyond registry coverage.
4. Add worktree runtime prototype and tests.
5. Add Headless Run MVP fixture/eval.
6. Add Full Handoff bounded simulation.
7. Add backend API boundary.
8. Start desktop UI after headless run and Full Handoff simulation are replayable.
9. Select Tauri/Electron after backend events and review package contracts are stable.
