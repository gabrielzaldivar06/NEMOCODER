# System Overview

This platform targets a desktop app for highly autonomous coding. The current Python/CLI implementation is the backend contract harness. The system is organized into five planes.

## Quality Core

Owns git-aware workspace mutation, diff application, validation, and the only code-writing path.

## Runtime

Owns sandbox/session lifecycle, execution state, and backend isolation strategy.

## Orchestration

Owns the workflow: plan, build, review, delegation, background runs, and autonomy escalation.

## Policy

Owns allow/deny/ask rules, inspection, and approvals for risky actions.

## Memory

Owns the full NEMO tool surface: startup context, context economy portfolios, evidence expansion, feedback loops, corrections, conversation storage, reminders, appointments, time/environment context, and maintenance diagnostics.

## Non-negotiable rules

1. Planning is read-only.
2. Execution can mutate only after approval.
3. Review is mandatory before commit or PR actions.
4. Only the quality core may write source files.
5. NEMO is the default context plane.
6. High autonomy must run inside sandbox/worktree boundaries before changing the main workspace.
