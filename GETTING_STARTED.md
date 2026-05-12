# Getting Started with Spacecode

This guide walks you through pointing Spacecode at an existing git repository and running your first autonomous coding handoff.

---

## Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| Python | 3.12+ | `python --version` |
| Node.js | 18+ | `node --version` |
| Git | any | Repository must be a git repo |
| LM Studio | latest | [lmstudio.ai](https://lmstudio.ai) — for real local Spacecode runs |

---

## 1. Backend setup

```powershell
# From the repo root
python -m venv .venv
.venv\Scripts\Activate.ps1          # Windows
# source .venv/bin/activate          # macOS/Linux

pip install -e .
```

Verify:
```powershell
$env:PYTHONPATH = "src"
python -m nemo_coding_platform blueprint
```

---

## 2. Configure LM Studio (for real Spacecode runs)

1. Open LM Studio → **Local Server** tab → load `nvidia/agentic-coder-4b` (or any OpenAI-compatible chat model).
2. Start the server on `http://localhost:1234`.
3. In Mission Control **Settings → Model**, set:
   - Provider URL: `http://localhost:1234/v1`
   - Model: `nvidia.agentic.coder-4b`
   - Provider mode: `subprocess`

Mission Control uses provider mode `subprocess` for real local execution. If LM Studio is unavailable, handoff execution will not start successfully.

---

## 3. Launch the Mission Control backend

```powershell
$env:PYTHONPATH = "src"
c:/dev/dev4/.venv/Scripts/python.exe -m nemo_coding_platform mission-control-server `
    --repo "C:\path\to\your\project" `
    --runtimes ".spacecode-runtimes"
```

The server starts on `http://127.0.0.1:8787`. Pass any valid git repository path as `--repo`. The `--runtimes` path is where agent sandboxes and checkpoints are stored (created automatically).

To start without a pre-configured repo (open one from the UI instead):
```powershell
$env:PYTHONPATH = "src"
c:/dev/dev4/.venv/Scripts/python.exe -m nemo_coding_platform mission-control-server --runtimes ".spacecode-runtimes"
```

---

## 4. Launch the Mission Control UI

In a second terminal:

```powershell
cd apps/mission-control
npm install      # first time only
npm run dev -- --port 5173
```

Open `http://localhost:5173` in your browser.

---

## 5. Open an existing repository

**From the UI (recommended):**

If no repository is open, the sidebar shows an inline picker:
1. Type the absolute path to your git repository in the input field.
2. Press **Enter** or click **Open**.

To switch repos later, go to **Settings → Repository → Open folder**.

**From the CLI:**

Pass `--repo` when starting the server:
```powershell
--repo "C:\dev\myproject"
```

Recent repos are remembered and shown in the sidebar for quick switching.

---

## 6. Run your first handoff

1. Click the **compose** icon (pencil) in the activity bar or open the **New Handoff** panel.
2. Fill in:
   - **Objective / PRD**: describe what the agent should build or fix.
   - **Validation commands** (optional): e.g. `python -m pytest tests/` — the agent will run these to verify its work.
    - **Provider mode**: `subprocess` for real Spacecode execution with LM Studio.
3. Click **Start Handoff**.

The agent run appears in the Explorer panel. Logs stream in the bottom panel. When the run completes, the diff review surface shows changed files and hunks for human approval.

---

## 7. Review and apply changes

1. Select a completed run in the Explorer.
2. Review file diffs hunk-by-hunk. Use **Accept** / **Reject** per hunk or per file.
3. Click **Apply** to write accepted hunks to the repository.
4. A backup is created before applying. Use **Rollback** from the timeline if needed.

---

## 8. Long Handoff (multi-phase)

For complex tasks, use the long handoff CLI with per-phase time budgets:

```powershell
$env:PYTHONPATH = "src"
python -m nemo_coding_platform long-handoff-run \
    --prd "Refactor auth module to use JWT" \
    --repo "C:\dev\myproject" \
    --plan-minutes 10 \
    --execute-minutes 30 \
    --review-minutes 10 \
    --token-budget 16000
```

Resume a paused or interrupted run:
```powershell
python -m nemo_coding_platform long-handoff-continue --job-id <job-id>
```

Poll real-time status from the server:
```
GET http://127.0.0.1:8787/api/handoff-job/{job-id}/signal
```

---

## Optional: NEMO memory (persistent context)

NEMO is the memory plane that injects project context into agent prompts. Without it, agents still work but have no cross-session memory.

**Setup:**

1. Configure the VS Code NEMO MCP server named `nemo`.
2. Spacecode should use the MCP transport `stdio://vscode/nemo` through the Mission Control backend.
3. In Mission Control **Settings → NEMO**, verify the health indicator is green.

Do not point the browser or frontend plan sync directly at `http://localhost:8765`; legacy SSE endpoints are compatibility-only and bypass Spacecode's backend policy layer.

When NEMO is connected, the agent automatically builds a context portfolio from your project's memory before each mutation and falls back to semantic search if the portfolio is empty.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `ModuleNotFoundError: nemo_coding_platform` | Set `$env:PYTHONPATH = "src"` or run `pip install -e .` |
| UI shows blank state | Check the backend is running on port 8787; the Vite proxy must be active |
| Agent run hangs | Verify LM Studio server is started and the model is loaded |
| `permission denied` on target file | Add file to `.spacecode-permissions.json` or remove explicit `--target-files` restriction |
| Port 8787 already in use | Pass `--port 8788` to the server and update the Vite proxy config in `vite.config.ts` |
