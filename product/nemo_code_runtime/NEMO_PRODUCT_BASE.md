# NEMO Product Base

This directory is the active Aider-based fork for Space Code.

## Role

Aider provides the base quality engine:

- git-first coding workflow
- repo map and code understanding
- robust edit/diff application
- validation loops
- local model compatibility

The fork is being extended with:

- OpenCode-inspired permissions and plan/build controls
- OpenHands-inspired runtime state and strong autonomy contracts
- NEMO MCP as full memory/tool plane

## First Modification

Added `aider.nemo_platform`, a small standard-library-only integration layer that defines:

- `permissions.py` — OpenCode-style allow/deny/ask policy rules
- `autonomy.py` — high-autonomy contracts with sandbox/worktree requirements
- `runtime.py` — OpenHands-style runtime state machine
- `nemo_tools.py` — full NEMO tool registry by phase/risk/suite
- `phases.py` — plan/build/review/background phase vocabulary
- `platform_info.py` — JSON serializer for autonomy, permissions, and NEMO tool contracts

## Platform Info Entrypoint

The current non-invasive wiring command is:

```powershell
$env:PYTHONPATH = "product/aider"
c:/dev/dev4/.venv/Scripts/python.exe -m aider.nemo_platform
```

It prints a stable JSON contract that can be consumed by tests, the future headless runner, and the desktop backend.

## Validation

Focused fork tests:

```powershell
$env:PYTHONPATH = "product/aider"
c:/dev/dev4/.venv/Scripts/python.exe -m unittest discover -s product/aider/tests/nemo_platform
```

The original upstream references remain under `reference-repos/` and should be treated as read-only comparison material.