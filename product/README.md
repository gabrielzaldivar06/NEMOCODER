# Product Source

This directory contains the active product base.

## Active Base

- `nemo_code_runtime/` is the current embedded Space Code mutation runtime path. The directory name is legacy and will be migrated separately.
- It is copied from `reference-repos/aider` and evolved in-place for Space Code.
- The original upstream clone remains in `reference-repos/aider` for comparison.

## Integration Direction

The embedded runtime keeps a proven quality/editing foundation and adds:

- OpenCode-style permission contracts and plan/build separation.
- OpenHands-style autonomy and runtime state contracts.
- Full NEMO MCP tooling contracts for operating and long-term memory. NEMO remains the external memory/context service; Space Code is the coding product.

## Current Product Extension

The first active Space Code extension lives under:

```text
product/nemo_code_runtime/nemo_code_runtime/nemo_platform/
```

Focused tests live under:

```text
product/nemo_code_runtime/tests/nemo_platform/
```

Run them with:

```powershell
$env:PYTHONPATH = "product/nemo_code_runtime"
c:/dev/dev4/.venv/Scripts/python.exe -m unittest discover -s product/nemo_code_runtime/tests/nemo_platform
```