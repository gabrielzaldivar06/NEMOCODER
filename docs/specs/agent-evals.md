# Spec: Agent Evals

## Purpose

Define reproducible evaluation for persisted run payloads, replay summaries, readiness scoring, and FR10 metrics export.

## Required Inputs

1. Benchmark task format
- Objective
- Acceptance criteria
- Validation policy/profile
- Optional linked PRD/spec references

2. Replay payload format
- Task and run metadata
- Timeline events
- Artifacts
- Validation results
- Mutation result

## Benchmark Task Contract

Minimum benchmark payload fields:

- `task.id`
- `task.objective`
- `run.id`
- `run.sandbox_path`
- `timeline[]`
- `artifacts[]`
- `validation.results[]`
- `mutation_result.changed_files`

## Replay Contract

- Replay summary must expose events, artifact paths, payload refs, and replayability boolean.
- Replay fails readiness when mandatory evidence is missing (checkpoint/review package/validation pass conditions).

## Scoring Contract

- Readiness score and grade are computed from persisted payload checks.
- Validation pass rate is derived from validation result statuses.
- Permission pressure is derived from permission decision/denial signals.

## Memory Incident Contract

- Stale-memory incidents are counted from readiness reasons and event/validation summaries containing stale-memory markers.
- Incident counts must be deterministic for identical payloads.

## FR10 Metrics Export Contract

Export must include at least:

- `success_rate`
- `validation_pass_rate`
- `override_rate`
- `stale_memory_incidents`
- `tool_failure_rate`

Supporting diagnostics:

- total/failed validation checks
- total permission events and overrides
- total tool invocations and failures
- readiness grade and reasons

## Current Implementation Anchors

- Readiness scoring: `score_headless_result` / `score_persisted_result`
- Replay summary: `build_replay_summary`
- FR10 metrics export: `build_run_metrics_report` and `export_run_metrics`

## Executable Tests

- Replay fixtures load from persisted JSON.
- Run logs can be scored via readiness scorer.
- Metrics export returns FR10 fields and stable numeric ranges.

## Acceptance Criteria

- Contract tests prove replay fixture loading and replay summary generation.
- Contract tests prove persisted run scoring is executable.
- Contract tests prove FR10 metrics export includes required fields.