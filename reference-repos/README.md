# Reference Repositories

These repositories are shallow clones used as architectural references for the NEMO Desktop Coding Platform.

They are not product source code and should not be modified directly unless intentionally studying or prototyping against upstream patterns.

## Repositories

- `aider/` — Aider quality/editing core patterns: git-first workflow, diff application, repo map, validation loops.
- `opencode/` — OpenCode workflow and policy patterns: plan/build separation, permissions, agents, session ergonomics.
- `openhands/` — OpenHands autonomy/runtime patterns: sandboxing, long-running agent execution, task state, strong agentic behavior.

## Clone Mode

All clones were created with `--depth 1` to keep this workspace light. If full history is needed:

```powershell
git -C reference-repos/aider fetch --unshallow
git -C reference-repos/opencode fetch --unshallow
git -C reference-repos/openhands fetch --unshallow
```
