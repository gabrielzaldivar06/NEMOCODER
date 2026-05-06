# Spec: Aider Platform Wiring

## Purpose

Expose the NEMO platform layer inside the active Aider fork without disrupting Aider's existing coding workflow. This is the first implementation bridge from contracts to product behavior.

## Scope

This spec covers a minimal, non-invasive platform entrypoint in `product/aider` that can report platform capabilities, permission defaults, autonomy levels, phase gates, and NEMO tool availability.

It does not yet replace Aider's normal chat/edit loop.

## Requirements

- Add an Aider-accessible platform info command or flag.
- Print autonomy levels and their safety constraints.
- Print default permission rules.
- Print NEMO tools grouped by phase and risk.
- Emit output in a stable machine-readable format for tests and future desktop backend use.
- Keep imports light enough to run without installing the full desktop stack.

## Acceptance Criteria

- Running the platform info entrypoint succeeds with `PYTHONPATH=product/aider`.
- Output includes `full_handoff`, `autonomous_sandbox`, and `background_agent`.
- Output includes NEMO tools beyond context portfolios.
- Output shows that main workspace writes are blocked for autonomous modes.
- Existing `aider.nemo_platform` focused tests remain green.

## Current Implementation

The initial command is intentionally isolated from `aider.main` to avoid loading Aider's heavier runtime dependencies before the platform contract is stable:

```powershell
$env:PYTHONPATH = "product/aider"
c:/dev/dev4/.venv/Scripts/python.exe -m aider.nemo_platform
```

It emits stable JSON with:

- schema version
- product base
- extension package
- autonomy contracts
- default permission rules
- NEMO tools
- NEMO tools by phase

## First Implementation Slice

1. Create a small module under `aider.nemo_platform` that serializes platform contracts. Done.
2. Add tests against the serializer. Done.
3. Wire it to an Aider CLI flag or module entrypoint. Done with `python -m aider.nemo_platform`.
4. Avoid changing Aider's core edit loop until the serializer is stable. Active constraint.

## Out Of Scope

- Running autonomous tasks.
- Calling real NEMO MCP tools.
- Creating worktrees.
- Desktop UI.
