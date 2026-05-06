# NEMO Coding Platform

Initial implementation scaffold for a desktop-first, highly autonomous coding platform with a local CLI harness.

The final product definition is captured in [docs/prd/final-prd.md](docs/prd/final-prd.md). The broader direction is also documented in [docs/prd/global-prd.md](docs/prd/global-prd.md): desktop app for coding, Aider/OpenCode-quality workflow, OpenHands-style strong autonomy, and NEMO MCP as the full memory plane.

## Current slice

This repository starts with architecture encoded as Python contracts instead of ad hoc notes:

- quality core boundaries
- plan/execute/review workflow rules
- unified permission model
- runtime state machine
- NEMO memory portfolio contracts
- minimal CLI for inspecting and validating backend contracts before the desktop UI is built

## Run

```powershell
$env:PYTHONPATH = "src"
python -m nemo_coding_platform blueprint
python -m nemo_coding_platform nemo-tools --phase plan
python -m nemo_coding_platform handoff-plan "Implement PRD feature" --json
python -m nemo_coding_platform headless-run "Implement PRD feature" --json
python -m nemo_coding_platform dry-run-write demo.txt "hello" --workspace .
python -m nemo_coding_platform apply-write demo.txt "hello" --workspace . --approve-execution --approve-review
python -m unittest discover -s tests
```

`apply-write` intentionally fails with exit code 1 unless both execution and review gates are explicit.

## Product Direction

The active product base is [product/aider](product/aider), an Aider-based fork modified for this platform. The CLI in `src/` is not the final product surface; it is the development harness for backend contracts that will inform the desktop app.

The desktop app will use the Aider base with:

- repo/task workspace
- autonomous sandboxed agent runs
- subagents
- approval queue
- artifact timeline
- diff/review UI
- NEMO memory trace
- LM Studio/OpenAI-compatible model configuration

Focused Aider fork tests:

```powershell
$env:PYTHONPATH = "product/aider"
c:/dev/dev4/.venv/Scripts/python.exe -m unittest discover -s product/aider/tests/nemo_platform
```

Inspect active Aider platform contracts:

```powershell
$env:PYTHONPATH = "product/aider"
c:/dev/dev4/.venv/Scripts/python.exe -m aider.nemo_platform
```
