# Spec: Full Handoff Autonomy

## Purpose

Full Handoff is the product's defining high-autonomy mode. It allows an agent to take a PRD and executable specs, then work for hours without human interaction while remaining isolated, auditable, interruptible, and review-gated.

This is not ordinary background execution. It is a PRD-to-code operating mode.

## Product Requirement

The user must be able to give the system:

- a PRD or product objective
- spec driven development rules
- a target repository
- an autonomy profile
- validation commands
- allowed MCP/tool permissions

The agent must be able to continue independently through planning, implementation, validation, repair loops, documentation, and memory writeback.

## Required Inputs

- PRD or objective
- acceptance criteria
- repo path
- model profile
- validation profile
- permission profile
- autonomy level: `full_handoff`
- NEMO topic/tags
- time budget or checkpoint cadence

## Mandatory Lifecycle

1. Bootstrap NEMO context.
2. Parse PRD into implementation objectives.
3. Create or update specs.
4. Create executable tests or contract checks.
5. Build an implementation plan.
6. Create isolated worktree/container runtime.
7. Execute changes through Aider/Quality Core.
8. Run validation.
9. Repair failures within budget.
10. Emit checkpoint events.
11. Produce final review package.
12. Write durable NEMO memory for decisions, corrections, and outcomes.

## Non-Negotiable Safety Rules

- Full Handoff cannot write directly to the main workspace.
- Full Handoff cannot merge without review gate.
- Full Handoff must run in container isolation once available; worktree-only is acceptable only for early local prototypes.
- Full Handoff must emit periodic checkpoints even when the user is absent.
- Full Handoff must preserve enough timeline to replay decisions and actions.
- Full Handoff must stop or downgrade when repeated validation failures indicate it is stuck.

## Checkpoints

Checkpoint events must include:

- elapsed time
- current phase
- files changed
- tests run
- failures encountered
- NEMO evidence used
- next planned action
- risk flags

Default cadence: every 15 minutes or every major phase transition, whichever comes first.

## Success Criteria

- Given a small PRD and repo, the agent can create specs, implement code, and run validation without human prompts.
- The user can inspect the final timeline and understand what happened.
- The final output includes patch/diff, validation results, memory writeback summary, and unresolved risks.
- No direct main workspace writes occur.

## Metrics

- unattended runtime minutes
- validation pass rate
- repair loop success rate
- permission incidents
- checkpoint completeness
- rollback rate
- NEMO memory usefulness

## First Implementation Slice

Do not start with multi-hour execution. Start with a bounded 10-minute local full-handoff simulation:

1. input PRD fixture
2. generated spec artifact
3. generated test fixture
4. patch through Quality Core
5. validation command
6. checkpoint log
7. final review package

Then increase task duration and autonomy only after replay and metrics are stable.