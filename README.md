# Space Code

Initial implementation scaffold for Space Code: a desktop-first, highly autonomous coding platform with a local CLI harness and external NEMO MCP memory integration.

The final product definition is captured in [docs/prd/final-prd.md](docs/prd/final-prd.md). The broader direction is also documented in [docs/prd/global-prd.md](docs/prd/global-prd.md): desktop app for coding, high-trust code editing workflow, OpenHands-style strong autonomy, and NEMO MCP as the full memory plane.

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

The active embedded mutation runtime currently lives in [product/nemo_code_runtime](product/nemo_code_runtime). That path is legacy naming during the Space Code rebrand. The CLI in `src/` is not the final product surface; it is the development harness for backend contracts that inform the Space Code desktop app.

The desktop app uses the Space Code runtime stack with:

- repo/task workspace
- autonomous sandboxed agent runs
- subagents
- approval queue
- artifact timeline
- diff/review UI
- NEMO memory trace
- LM Studio/OpenAI-compatible model configuration

## Desktop Mission Control Spike

The first UI spike lives in [apps/mission-control](apps/mission-control). It is now shaped like a compact Codex/VS Code-style workbench: activity bar, explorer/run tree, central hunk-based diff review, conversational agent control pane, bottom timeline/terminal panel, New Full Handoff composer and model/MCP/NEMO settings. The review surface shows line-by-line repo-vs-sandbox hunks, accept/reject controls per file and hunk, a risk checklist, selected-hunk apply through the review gate, a pre-apply plan confirmation, and apply history with backup/rollback artifacts. The agent pane accepts prompts, shows visible tool calls, and proposes Continue/Revise/Apply actions for the selected run. The NEMO Memory panel exposes operational context directly in the UI: context portfolio, memory traces/used atoms, corrections, evidence handles, feedback events, and store health. The settings panel persists LM Studio URL, model, provider, NEMO DB, runtime path, timeout and handoff budgets, supports opening recent git repos or cloning from git, and includes artifact cleanup scan/delete controls. The composer starts async Full Handoff jobs through the local bridge, runs the real Space Code local engine through LM Studio via the subprocess provider, streams incremental job logs in the bottom panel, and exposes cancel/pause/resume controls. Execution stdout/stderr is persisted as `engine-output.txt` and linked from the timeline.

For the local MVP flow, start NEMO first and then use the one-command launcher:

```powershell
.\scripts\start-mvp-local.ps1
```

The launcher starts the Mission Control API on `http://127.0.0.1:8787`, starts the Vite UI on `http://127.0.0.1:5173`, and writes logs under `.spacecode-runtimes\logs`. Space Code should prefer the VS Code NEMO MCP transport `stdio://vscode/nemo`; legacy `/mcp/sse` endpoints are compatibility-only and must not be called directly from the browser.

Run the live local bridge for review/apply/rollback actions:

```powershell
$env:PYTHONPATH = "src"
c:/dev/dev4/.venv/Scripts/python.exe -m nemo_coding_platform mission-control-server --repo . --runtimes .spacecode-runtimes
```

Run the UI in another terminal:

```powershell
cd apps/mission-control
npm install
npm run dev -- --port 5173
```

The Vite dev server proxies `/api/*` to `http://127.0.0.1:8787`. If you only need a static snapshot, refresh its state from persisted runs:

```powershell
$env:PYTHONPATH = "src"
c:/dev/dev4/.venv/Scripts/python.exe -m nemo_coding_platform mission-control-state --repo . --runtimes .spacecode-runtimes --save-json apps/mission-control/public/mission-control-state.sample.json --json
```

Focused embedded runtime tests:

```powershell
$env:PYTHONPATH = "product/nemo_code_runtime"
c:/dev/dev4/.venv/Scripts/python.exe -m unittest discover -s product/nemo_code_runtime/tests/nemo_platform
```

Inspect active embedded platform contracts:

```powershell
$env:PYTHONPATH = "product/nemo_code_runtime"
c:/dev/dev4/.venv/Scripts/python.exe -m nemo_code_runtime.nemo_platform
```
