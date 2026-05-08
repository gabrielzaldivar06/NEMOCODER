# Spec: Desktop UI Flows

## Purpose

Define the Desktop Mission Control interaction contract independently from visual implementation. Backend APIs are the executable source of truth while UI snapshots remain optional until the frontend surface stabilizes.

## Flow Contract

1. Launch
- UI must fetch initial state and settings from backend runtime state.
- Launch succeeds when state payload is available and settings are normalized.

2. Connect Model
- UI updates provider/model/runtime settings through settings API.
- Backend validates setting keys and value ranges before persisting.

3. Connect NEMO
- UI loads NEMO health, context portfolio, and memory traces.
- When memory DB is disabled, backend returns a safe disabled payload.

4. Open Repo
- UI opens an existing git repo or clones one then opens it.
- Backend enforces git-repo boundary and updates recent repos.

5. Create Task
- UI submits objective, acceptance criteria, validation policy, and optional PRD/spec context.
- Backend normalizes payload and returns job/run handles.

6. Run Agent
- UI starts a handoff job and displays runtime status, logs, and signals.
- Background jobs support pause/resume/cancel through explicit APIs.

7. Approve Actions
- UI requests review plan, optional hunk selection, and apply.
- Apply remains review-gated unless policy allows explicit auto-apply.

8. Review Patch
- UI requests file/hunk preview and review package details.
- Review payload must include mergeability and risk flags.

9. Inspect Memory Trace
- UI displays selected run memory traces, portfolio context, corrections, and evidence summaries.

## Backend API Boundary (minimum)

- `GET /api/state`
- `POST /api/settings`
- `POST /api/repo/open`
- `POST /api/repo/clone`
- `POST /api/handoff/start`
- `POST /api/job/pause`
- `POST /api/job/resume`
- `POST /api/job/cancel`
- `POST /api/review`
- `POST /api/file`
- `POST /api/apply`
- `POST /api/apply-selection`
- `POST /api/rollback`
- `POST /api/nemo`

## Executable Test Strategy

- Before frontend snapshots: enforce backend-first contract tests for launch/model/repo/run/review/memory flows.
- After frontend stabilizes: add component/snapshot tests keyed to this flow contract.

## Acceptance Criteria

- Every required flow maps to at least one backend API contract test.
- Repo, runtime, review, and memory-trace flows are all test-covered.
- Unsupported payloads fail with deterministic request errors.