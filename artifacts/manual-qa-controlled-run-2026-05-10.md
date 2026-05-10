# Manual QA Controlled Run Report (2026-05-10)

## Scope
- Restart backend and frontend.
- Use Mission Control UI as a human tester.
- Create a controlled run and observe behavior.
- Identify bugs/inconsistencies, fix, and re-test.

## Environment
- Backend: `npm run dev:api` at `http://127.0.0.1:8787`
- Frontend: `npm run dev -- --port 5173` at `http://127.0.0.1:5173`
- Date: 2026-05-10

## Controlled Run Execution
1. Opened app and submitted chat prompt:
   - "Run controlado QA: crea un plan de 3 pasos para validar /api/search y reporta riesgos."
2. Opened "Configurar handoff" and started sandbox job:
   - Objective: "Run controlado QA: validar endpoint /api/search con una prueba automatizada y reportar resultado."
   - Created job: `job-20260510184153-1a84d482`
3. Observed runtime states:
   - `starting` -> `running`
   - Cancelled manually for bounded QA cycle
   - Final visible status in UI: `cancelled returncode=1`

## Findings

### F1 (Critical) - `/api/stats` returned 500 in UI polling loop
- Symptom:
  - Browser showed repeated `500 Internal Server Error` every ~8s.
  - UI often stuck in noisy "refreshing" states.
- Root cause:
  - `api_stats` used `jobs.jobs`, but `HandoffJobManager` has no `jobs` attribute.
  - Actual data source is `jobs.list()` / internal `_jobs`.
- Evidence:
  - Direct call before fix: `GET /api/stats => 500` with error `HandoffJobManager object has no attribute jobs`.

### F2 (High UX Inconsistency) - Summary showed `0 runs` while active jobs existed
- Symptom:
  - Sidebar "Resumen inteligente" displayed `0 runs` during/after active job lifecycle.
- Root cause:
  - Frontend used only `state.runs.length` for `totalRuns`.
  - Active jobs are surfaced by `/api/stats` (`missionStats.jobs.total`) and can diverge from `state.runs`.

### F3 (Residual, not fixed in this cycle) - "Actividad y decisiones" may still show `0 runs recientes`
- Symptom:
  - Card summary can remain `0 runs recientes` even when total jobs > 0.
- Likely cause:
  - It is derived from `state.runs` detailed list, not from aggregated job stats.
- Impact:
  - Informational inconsistency only; no blocking execution impact.

## Fixes Applied

### Fix for F1 (Backend)
- File: `src/nemo_coding_platform/mission_control_server.py`
- Change:
  - Replaced invalid `jobs.jobs` access with `jobs.list()` aggregation:
    - `job_count`, `active_jobs`, `completed_jobs` now computed from list payload.
- Tests added/validated:
  - `test_api_stats_counts_jobs_from_manager_list`
  - Existing `test_api_stats_includes_source_analytics_summary`

### Fix for F2 (Frontend)
- File: `apps/mission-control/src/main.tsx`
- Change:
  - `totalRuns` now uses fallback from mission stats:
    - `const totalRuns = Math.max(state.runs.length, missionStats?.jobs?.total ?? 0);`

## Re-test Results
- Runtime verification:
  - `GET /api/stats` after fix -> `200`.
- UI verification:
  - "Resumen inteligente" now displays non-zero run count (`15 runs`) instead of `0`.
  - Controlled run lifecycle visible (`starting/running/cancelled`) in bottom panel.
- Build verification:
  - `npm --prefix apps/mission-control run build` -> success.
- Regression verification:
  - Focused tests: `2 passed` for `api_stats` related checks.
  - Full server+adapter: `97 passed`.

## Corrective Plan (Next Iteration)
1. Align "Actividad y decisiones" summary with mission stats fallback
   - Keep detailed list from `state.runs`, but summary count should not contradict global run/job totals.
2. Add frontend test coverage for stats/run consistency
   - Cover mismatch scenario: `state.runs = 0` with `missionStats.jobs.total > 0`.
3. Add lightweight runtime health banner for repeated polling failures
   - If same endpoint fails N times, show explicit endpoint/error source.

## Final Status
- Critical backend 500 issue: fixed and validated.
- Primary run-count inconsistency: fixed and validated.
- One residual non-blocking summary inconsistency documented for next pass.
