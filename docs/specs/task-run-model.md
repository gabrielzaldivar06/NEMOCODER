# Spec: Task And Run Model

## Purpose

Define the durable task/run entities required for headless execution, desktop UI, replay, Full Handoff, NEMO memory tracing, and auditability.

## Concepts

- Task: user's durable objective for a repository.
- Run: one execution attempt for a task.
- Event: append-only timeline entry.
- Artifact: file, patch, spec, report, log, or validation result created by a run.
- Memory Trace: NEMO context used or memory written during a run.

## Task Fields

- id
- repo_path
- title
- objective
- autonomy_level
- status
- linked_prd
- linked_specs
- created_at
- updated_at

## Run Fields

- id
- task_id
- state
- phase
- runtime_id
- sandbox_path
- model_profile
- permission_profile
- validation_profile
- started_at
- completed_at
- failure_reason

## Event Fields

- id
- run_id
- sequence
- timestamp
- phase
- kind
- summary
- payload_ref

## Artifact Fields

- id
- run_id
- type
- path
- content_hash
- summary
- created_at

## Requirements

- Events are append-only.
- A run can be replayed from task metadata, events, artifacts, permission decisions, tool events, and memory traces.
- A task can have multiple runs.
- Full Handoff runs must include checkpoint events.
- Final review packages are artifacts.

## Acceptance Criteria

- Contract tests can instantiate Task, Run, Event, Artifact, and Memory Trace records.
- Run state transitions follow the runtime state machine.
- Full Handoff runs cannot complete without at least one checkpoint and final review package.
- Desktop and CLI consume the same model.
