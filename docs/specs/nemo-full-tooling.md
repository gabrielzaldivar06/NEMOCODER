# Spec: NEMO Full Tooling

## Purpose

Define the complete NEMO tool plane used by the platform. NEMO integration is full-spectrum memory and context governance, not portfolio-only retrieval.

## Required Tool Suites

- startup context
- context economy
- memory management
- conversation
- time and environment
- reminders
- appointments
- maintenance

Every suite must have at least one registered tool and explicit phase access.

## Phase Access Rules

- Plan: startup/context tools, memory read+light feedback, no scheduling writes.
- Execute: context/evidence expansion, controlled memory feedback, no scheduling writes.
- Review: full writeback, memory curation, optional scheduling operations with explicit user intent.

## Risk Levels

- `read_only`: no mutation of durable/scheduled state.
- `memory_write`: writes memory atoms or conversation artifacts.
- `scheduling_write`: modifies reminders/appointments.
- `destructive`: deletes or irreversible scheduling state changes.

## Scheduling Restrictions

- Scheduling writes are review-gated.
- Plan/build must not create, cancel, or reschedule reminders/appointments.
- Review can perform scheduling writes only when user intent is explicit.

## Corrections And Writeback Lifecycle

- Corrections are writable in all phases when user correction occurs.
- Build/review may record context feedback and checkpoint artifacts.
- Close/review persists durable session summary, decisions, and unresolved risks.

## Evidence Expansion Rules

- Portfolio entries may carry evidence handles.
- Evidence expansion is allowed in execute/review and should be query-scoped where possible.
- Expansion and feedback events must be traceable for replay/eval.

## Acceptance Criteria

- All declared NEMO suites are represented in the runtime tool registry.
- Scheduling and destructive tools are review-gated.
- Corrections are allowed across plan/execute/review.
- Portfolio/context tools are part of, but not equal to, total NEMO integration.