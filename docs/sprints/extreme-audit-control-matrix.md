# Extreme Audit Control Matrix (Iteration 2)

## Objective
Trace each critical UI control to handler, backend contract, and expected operational evidence.

## Mission Control UI -> Handler -> API Matrix

| Section | Control | Handler | API Endpoint(s) | Operational Evidence | Test Coverage |
|---|---|---|---|---|---|
| Home | Send intent | sendHomeAgentMessage -> sendAgentMessage | POST /api/agent/message | New assistant message + status update | smoke existing shell + manual path |
| Home | Memory quick action | openMemorySection | none (state navigation) | Activity bar Memory becomes active | smoke: opens memory section |
| Home | Provider switch | setProviderMode | POST /api/settings | settings persisted + status "Real mode enabled" | backend settings tests |
| Runs | Build plan | reviewRun | POST /api/review | reviewPlan populated + status | backend review tests |
| Runs | Apply | applyRun | POST /api/review, POST /api/apply | apply json stored + state refresh | backend apply tests |
| Runs | Apply selected hunks | applySelectedDiff | POST /api/apply-selection | selective file mutations | backend apply-selection tests |
| Runs | Rollback | rollbackRun | POST /api/rollback | restored files + state refresh | backend rollback tests |
| Jobs | Pause/Resume/Cancel | controlJob | POST /api/job/pause, /api/job/resume, /api/job/cancel | job status transitions + logs | backend job tests |
| Versioning | Refresh | loadGitStatus/loadGitBranches/loadGitRemotes | GET /api/git/status, /api/git/branches, /api/git/remotes | branch + working tree shown | smoke versioning navigation |
| Versioning | Stage/Unstage file | stageGitPath | POST /api/git/stage | status refresh + updated staged markers | smoke: stage action call assertion + backend git-stage tests |
| Versioning | Stage/Unstage hunk | stageGitHunk / applySelectedGitHunks | POST /api/git/stage-hunk | diff/hunk status changes | backend git-stage-hunk tests |
| Versioning | Commit | commitGit | POST /api/git/commit | commit created + status refresh | smoke: commit action call assertion + backend git commit tests |
| Versioning | Checkout/Create branch | checkoutGitBranch | POST /api/git/checkout | branch switch/create reflected | backend git checkout tests |
| Versioning | Pull/Push | syncGit | POST /api/git/sync | sync output + status refresh | backend git sync tests |
| Terminal | Run command | runTerminal | POST /api/terminal/run | stdout/stderr + exit code + duration | backend terminal tests (error paths added) |
| Browser | Open URL | openBrowserUrl | POST /api/browser/open | last_url/history updated | backend browser-open tests |
| Browser | Search web | searchBrowserWeb | POST /api/browser/search | results list + search history + engine label | smoke: performs browser search |
| Browser | Open search result | openBrowserSearchResult | POST /api/browser/open | selected result opened/history tracked | manual + backend browser-open tests |
| Extensions | Toggle extension | toggleExtension | POST /api/extensions | enabled/disabled persisted in settings | backend extensions tests |
| Settings | Save settings | saveSettings | POST /api/settings | settings persisted + reflected in state | backend settings tests |
| Settings | Open repo | openRepo | POST /api/repo/open | repo changed + sections updated | backend repo-open tests |
| Settings | Clone repo | cloneRepo | POST /api/repo/clone | clone then open or actionable error | backend repo-clone tests (error path added) |
| Settings | Cleanup artifacts | cleanupArtifacts | POST /api/artifacts/cleanup | scan/delete lists + status | backend cleanup tests |
| Settings | Cleanup orphan jobs | cleanupOrphanJobs | POST /api/jobs/orphans | orphan summary + cleanup actions | backend orphan-job tests |

## Dead-Control Policy Status
- Decorative controls converted to non-interactive labels in Iteration 1:
  - Runs header tabs (visual labels only)
  - Bottom panel tabs (visual labels only)
  - Home notification icon (status-only decorative icon)
- No known P0 dead buttons remain in Mission Control active shell.

## Error-Path Tests Added in Iteration 2
1. browser open invalid URL -> invalid_browser_url
2. browser search invalid max_results -> invalid_browser_search_options
3. terminal run missing command -> missing_terminal_command
4. terminal run invalid timeout -> invalid_terminal_timeout
5. repo clone non-empty destination -> ValueError guard

## Error-Path Tests Added in Iteration 3
1. browser search missing Playwright dependency -> explicit ValueError propagation
2. browser search timeout in execution runtime -> explicit ValueError propagation

## Next Iteration (Recommended)
1. Extend frontend interaction tests for remaining Versioning actions (checkout/sync/hunk-stage) with status/error assertions.
2. Add end-to-end click-through script to assert zero broken controls before release.
3. Add CI gate to fail on any newly introduced dead interactive controls.
