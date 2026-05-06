# Spec: NEMO Lifecycle

## Purpose

Define when and how the platform uses NEMO during task execution. NEMO is the operating and long-term memory plane, not a passive retrieval plugin.

## Lifecycle

### Start

- Call `prime_context` or `context_bootstrap`.
- Load corrections, durable preferences, project facts, active task memory, and relevant evidence handles.
- Record the memory context used in the run timeline.

### Plan

- Build a bounded context portfolio.
- Expand evidence only when required for planning.
- Do not create reminders or appointments.

### Build

- Expand evidence on demand.
- Record context feedback when portfolio evidence was useful or noisy.
- Store intermediate conversation only at checkpoints for Full Handoff.

### Review

- Record corrections immediately when the user corrects the agent.
- Record final context feedback.
- Inspect NEMO health if memory behavior was suspicious.
- Create reminders or appointments only when explicit user intent exists.

### Close

- Store final conversation summary.
- Persist durable decisions, outcomes, unresolved risks, and reusable lessons.
- Do not store low-value command noise.

## Requirements

- Every NEMO call maps to suite, risk, phase, and permission decision.
- Memory writes must be summarized in a reviewable artifact.
- Scheduling writes are review-gated.
- Full Handoff must emit checkpoint summaries to NEMO at configured cadence.

## Acceptance Criteria

- Tests prove NEMO tools are not portfolio-only.
- Plan/build phases cannot call scheduling tools.
- Review/close can write durable memory.
- Full Handoff has checkpoint writeback semantics.
