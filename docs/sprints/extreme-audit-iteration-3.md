# Extreme Audit Iteration 3

## Scope
- Strengthen operability coverage for Versioning controls in Mission Control.
- Add deterministic backend coverage for browser-search runtime failures.

## Implemented
- Frontend smoke tests in `apps/mission-control/src/smoke.test.tsx`:
  - Added `executes stage action from versioning working tree`.
  - Added `executes commit action from versioning panel`.
  - Assertions verify endpoint invocation contracts (`/api/git/stage`, `/api/git/commit`) with `POST` method.
- Backend tests in `tests/test_mission_control_server.py`:
  - Added `test_browser_search_surfaces_missing_playwright_dependency`.
  - Added `test_browser_search_surfaces_timeout_failure`.
  - Both tests lock expected error propagation semantics from `api_browser_search`.

## Expected Outcome
- Prevent regressions where Versioning controls stop wiring to backend endpoints.
- Prevent silent contract drift in browser search failure behavior.

## Validation Commands
- `python -m unittest tests.test_mission_control_server.MissionControlServerTests.test_browser_search_surfaces_missing_playwright_dependency tests.test_mission_control_server.MissionControlServerTests.test_browser_search_surfaces_timeout_failure`
- `npm --prefix apps/mission-control run -s test:smoke`
- `npm --prefix apps/mission-control run -s build`
