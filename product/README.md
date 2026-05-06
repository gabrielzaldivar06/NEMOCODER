# Product Source

This directory contains the active product base.

## Active Base

- `aider/` is the active Aider-based product fork.
- It is copied from `reference-repos/aider` and modified in-place for the NEMO Desktop Coding Platform.
- The original upstream clone remains in `reference-repos/aider` for comparison.

## Integration Direction

The active fork keeps Aider's quality/editing foundation and adds:

- OpenCode-style permission contracts and plan/build separation.
- OpenHands-style autonomy and runtime state contracts.
- Full NEMO MCP tooling contracts for operating and long-term memory.

## Current Product Extension

The first active extension lives under:

```text
product/aider/aider/nemo_platform/
```

Focused tests live under:

```text
product/aider/tests/nemo_platform/
```

Run them with:

```powershell
$env:PYTHONPATH = "product/aider"
c:/dev/dev4/.venv/Scripts/python.exe -m unittest discover -s product/aider/tests/nemo_platform
```