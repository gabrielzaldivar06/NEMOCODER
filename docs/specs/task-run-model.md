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
- workflow_mode
- started_at
- completed_at
- failure_reason

## Workflow Mode

`workflow_mode` is an optional field on any job payload that restricts which actions the agent may perform.

| Mode | Allowed actions | Prohibited actions |
| --- | --- | --- |
| `plan` | read, search, inspect | write mutations, handoff_start, apply |
| `build` | write mutations, handoff_start, self_modify_start | review-gate operations |
| `review` | review, apply, apply_selection | write mutations |

Rules:
- If `workflow_mode` is absent, no restriction is applied (backward-compatible default).
- A resumed job inherits `workflow_mode` from its parent payload so plan-mode write prohibitions propagate to child runs.
- Policy violations return `error_code="workflow_policy_violation"` with HTTP 400.

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
- `workflow_mode=plan` must prevent handoff_start and apply; contract test: `test_handoff_start_rejects_plan_mode_for_write_actions`.
- `workflow_mode=review` must prevent build-mode mutations; contract test: `test_apply_endpoint_rejects_build_mode`.
- `workflow_mode` is preserved through job payload round-trips; contract test: `test_workflow_mode_survives_job_snapshot_round_trip`.
- Resumed jobs inherit parent `workflow_mode`; contract test: `test_resumed_job_inherits_parent_workflow_mode`.
