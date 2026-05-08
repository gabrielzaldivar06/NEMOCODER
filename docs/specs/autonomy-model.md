# Spec: Autonomy Model

## Purpose

Define autonomy levels, safety boundaries, and runtime behavior for unattended execution. Higher autonomy increases execution scope but never bypasses isolation, review gates, or Quality Core mutation rules.

## Autonomy Levels

- `manual`: human-directed, no unattended progression.
- `sandbox`: isolated execution with human-paced progression.
- `autonomous_sandbox`: isolated unattended execution with checkpoints and strict merge gates.
- `background_agent`: unattended continuation in background with pause/resume/cancel controls.
- `full_handoff`: PRD-to-code lifecycle with SDD requirements, checkpoints, and review package.
- `team_ci`: CI-oriented autonomous mode with isolation and review constraints.

## Permission Matrix

- High-autonomy levels require isolation (`autonomous_sandbox`, `background_agent`, `full_handoff`, `team_ci`).
- No autonomy level can write directly to main workspace bypassing Quality Core.
- Merge to main requires review approval across all levels.
- Subagent spawning is allowed only for autonomous levels with bounded depth and explicit policy.

## Runtime Controls

- `pause`: stop active execution safely and persist resumable state.
- `resume`: continue from a valid paused state and preserve lineage.
- `cancel`: stop run and preserve final reproducible state for audit.
- `heartbeat`: periodic progress/status signal during long runs.

## Merge Gates

- All main-workspace integration must route through review-to-main gate.
- Apply without explicit review approval is forbidden unless a policy profile explicitly permits safe auto-apply and records that decision.
- Validation failures block close/merge unless explicitly waived and recorded.

## Full Handoff Requirements

- Must be isolation-backed.
- Must emit periodic checkpoints.
- Must produce final review package.
- Must persist memory writeback summary for continuity.

## Acceptance Criteria

- Each autonomy level maps to a stable contract.
- High autonomy requires isolation and review gate constraints.
- Background/full-handoff runs support pause/resume/cancel semantics.
- Main workspace writes remain mediated by Quality Core and review gates.