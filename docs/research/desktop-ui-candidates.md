# Desktop UI Candidates For Mission Control

## Decision

Use a Codeg-like mission-control shell as the primary direction, while keeping Aider as the active quality core and NEMO as the required memory plane.

The UI should not replace `product/aider` or the Python runtime. It should orchestrate the existing backend contracts: Full Handoff runs, persisted runtime JSON, review plans, apply reports, rollback JSON, model settings and NEMO memory state.

## Ranked Candidates

### 1. Codeg

- Repo: https://github.com/xintaofei/codeg
- License: Apache-2.0
- Stack: Tauri 2, Next.js, React, Rust, SQLite
- Fit: strongest candidate for a reusable mission-control shell.
- Useful pieces: project picker, multi-agent workspace, git worktree flows, file tree, diff, terminal, MCP management, settings and web/server mode.
- Integration direction: fork or study UI/backend boundary, then replace its agent runtime with NEMO platform commands and Aider-backed handoff runs.

### 2. Async IDE

- Repo: https://github.com/ZYKJShadow/Async
- License: Apache-2.0
- Stack: Electron, React, Monaco, xterm.js
- Fit: best reference for agent-first execution UI.
- Useful pieces: Think/Plan/Execute/Observe loop, approval gates, Monaco diff/editor flows, model settings, MCP support, terminal and Git UI.
- Integration direction: copy patterns for timeline, plan approval, diff review and settings. Avoid becoming a full IDE too early.

### 3. OpenCowork

- Repo: https://github.com/OpenCoworkAI/open-cowork
- License: MIT
- Stack: Electron, React, Tailwind
- Fit: strong desktop shell and MCP/settings reference.
- Useful pieces: model provider settings, MCP connectors, permission dialog, trace panel, path sandbox, WSL2/Lima isolation UX.
- Integration direction: use as UX reference for permissions, trace and settings.

### 4. OpenCove

- Repo: https://github.com/DeadWaveWave/opencove
- License: MIT
- Stack: Electron, React, TypeScript, xterm.js, node-pty, XYFlow
- Fit: best spatial/timeline inspiration.
- Useful pieces: infinite canvas for agents, tasks, terminals and notes; persistent workspaces; control center.
- Integration direction: future visual Run Board or Handoff Map after the review/apply workflow is reliable.

### 5. Superset

- Repo: https://github.com/superset-sh/superset
- License: Elastic License 2.0
- Fit: conceptually excellent, legally awkward as a base.
- Useful pieces: parallel CLI agents, worktree isolation, built-in diff viewer, workspace presets and agent monitoring.
- Integration direction: reference only.

## Current Spike

The first local spike lives at `apps/mission-control`.

It consumes state exported by:

```powershell
$env:PYTHONPATH='src'
c:/dev/dev4/.venv/Scripts/python.exe -m nemo_coding_platform mission-control-state --repo . --runtimes .nemo-runtimes --save-json apps/mission-control/public/mission-control-state.sample.json --json
```

The spike includes:

- repo picker
- task workspace list
- approval queue count
- agent timeline preview
- diff/review file list
- model/NEMO/Aider settings panel

Next step is wiring interactive actions from the UI to backend commands:

- `review-run-json`
- `apply-run-json --save-apply-json`
- `rollback-apply-json`
- `long-handoff-run`