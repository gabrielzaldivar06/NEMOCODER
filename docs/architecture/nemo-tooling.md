# NEMO Tooling Model

NEMO is the platform memory plane, not a portfolio-only dependency.

## Rule

All NEMO tools enter the platform through a tool registry that declares:

- suite
- risk level
- allowed workflow phases
- purpose

The orchestrator should never call NEMO tools as anonymous side effects. Every call must map to a contract so it can be reviewed, audited, and phase-gated.

## Suites

- startup context: bootstrapping and continuity tools
- context economy: portfolio, evidence, compression, feedback, and stats tools
- memory management: corrections and curated memory updates
- conversation: session persistence and recent context inspection
- time and environment: reliable timestamps and optional environment context
- reminders: explicit follow-up and intent-anchor tools
- appointments: explicit scheduled appointment tools
- maintenance: health and diagnostics tools

## Risk Levels

- read_only: safe to use without mutating NEMO state
- memory_write: changes memory, evidence, conversation, or feedback state
- scheduling_write: creates reminders or appointments and must be user-intent gated
- destructive: reserved for future delete/cancel/reset operations

## Phase Gates

Planning may read startup context and build portfolios, but should not create reminders or appointments.

Execution may write artifact evidence and context feedback, but scheduling actions stay blocked.

Review may record final conversation state, update memory, create explicit follow-ups, and inspect health.

## Current Implementation

The registry is implemented in `src/nemo_coding_platform/core/memory.py` and exposed with:

```powershell
python -m nemo_coding_platform nemo-tools
python -m nemo_coding_platform nemo-tools --phase plan
```