# Spec: Headless Run MVP

## Purpose

Define the first end-to-end execution target before desktop UI. The headless run proves the engine can perform a real coding task with memory, permissions, isolation, patching, validation, review, and replay.

## Goal

Given a small repo and a task objective, the platform can execute a bounded run without a desktop UI.

## Required Flow

1. Create task.
2. Bootstrap NEMO context.
3. Generate plan.
4. Evaluate permissions.
5. Create worktree runtime.
6. Apply patch through Aider/Quality Core.
7. Run validation.
8. Produce review package.
9. Write NEMO summary.
10. Persist replayable timeline.

## Requirements

- The run must not require desktop UI.
- The run must not write to the main workspace directly.
- All decisions and artifacts must be inspectable after completion.
- The flow must be compatible with later Full Handoff extension.

## Acceptance Criteria

- A fixture task can complete using only CLI/headless entrypoints.
- Timeline includes context, plan, permission, mutation, validation, review, and memory events.
- Final review package exists.
- Tests or eval fixture can score the run.

## Current CLI Slice

The current deterministic implementation exposes two commands:

```powershell
$env:PYTHONPATH = "src"
c:/dev/dev4/.venv/Scripts/python.exe -m nemo_coding_platform handoff-plan "Implement PRD feature" --json
c:/dev/dev4/.venv/Scripts/python.exe -m nemo_coding_platform headless-run "Implement PRD feature" --json
```

`handoff-plan` emits the PRD-to-code step sequence. `headless-run` executes the stdlib-only simulation and returns event count, artifact count, validation status, and readiness score.
