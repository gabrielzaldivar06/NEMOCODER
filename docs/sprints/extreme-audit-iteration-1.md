# Extreme Audit Iteration 1 (P0)

## Objective
Eliminate dead UI controls and establish an operational control matrix baseline for Mission Control.

## Scope
- Frontend Mission Control shell
- Home quick actions
- Runs header tabs
- Bottom panel tabs
- Smoke test guardrails

## Changes Applied
1. Wired Home memory quick action to a real behavior.
- Control: Home -> "Memory"
- Action now: opens Memory section and updates status line.
- File: apps/mission-control/src/main.tsx

2. Removed decorative non-operational buttons by converting them to non-interactive labels.
- Controls: Runs tab strip labels (Editor/Review/Agent)
- Controls: Bottom panel labels (Timeline/Terminal/Output)
- Control: Notifications icon in Home status strip
- File: apps/mission-control/src/main.tsx

3. Updated styles for non-interactive bottom labels.
- File: apps/mission-control/src/styles.css

4. Added smoke regression coverage for memory quick action.
- Test: "opens memory section from home quick action"
- File: apps/mission-control/src/smoke.test.tsx

## Quick Control Matrix (P0 subset)
| Control | Type | Previous Status | Current Status | Verification |
|---|---|---|---|---|
| Home Memory quick action | button | dead (no handler) | operational | smoke test + manual click path |
| Home Notifications icon | button | dead (no handler) | non-interactive label | static inspection |
| Runs tab strip buttons | buttons | dead (no handlers) | non-interactive labels | static inspection |
| Bottom panel tabs | buttons | dead (no handlers) | non-interactive labels | static inspection |

## Validation
- `npm --prefix apps/mission-control run -s test:smoke` -> PASS (3 tests)
- `npm --prefix apps/mission-control run -s build` -> PASS
- Editor diagnostics on touched files -> No errors

## Remaining P0 Work (next iteration)
1. Complete full control inventory with explicit UI->handler->API mapping for all interactive elements.
2. Add tests for top-priority controls in each section:
- Versioning actions (stage/unstage/commit/checkout/sync)
- Browser actions (open/search/open result)
- Repo open/clone actions
3. Add negative-path tests for error surfaces (invalid payloads, backend failures, timeout states).
4. Audit backend route parity and produce endpoint coverage table.
