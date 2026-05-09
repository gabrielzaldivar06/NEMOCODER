# Release Confidence Evidence

run_json=C:\Users\gabri\AppData\Local\Temp\tmpv4qxh2o8\runtimes\run.json
apply_json=C:\Users\gabri\AppData\Local\Temp\tmpv4qxh2o8\apply-results\task-1-run-1.json

## Review
- ok: true
- mergeable: true
- changed_files: existing.txt, created.txt
- risk_flags: none

## File Preview
- operation: update
- hunk_count: 1
- file_risk_flags: none

## Apply
- applied_files: existing.txt, created.txt
- created_files: created.txt
- updated_files: existing.txt
- apply_history_count: 1

## Replay
- headless_exit_code: 0
- exit_code: 0
- run_json: C:\Users\gabri\AppData\Local\Temp\tmpv4qxh2o8\headless-result.json
- can_replay: true
- grade: ready
- event_count: 8
- artifact_paths: generated-spec.md, generated-test.txt, patch.diff, engine-output.txt, validation.txt, memory.md, checkpoint.md, review-package.md
- payload_refs: engine-output.txt, checkpoint.md

## Rollback
- restored_files: existing.txt
- deleted_files: created.txt

## State
- run_count: 1
- approval_queue_count: 1

## Benchmark Gate
- baseline_exit_code: 0
- baseline_json: C:\Users\gabri\AppData\Local\Temp\tmpv4qxh2o8\bench-baseline.json
- current_json: C:\Users\gabri\AppData\Local\Temp\tmpv4qxh2o8\bench-current.json
- pass_case_exit_code: 0
- pass_case_passed: true
- pass_case_violation_count: 0
- fail_case_exit_code: 1
- fail_case_passed: false
- fail_case_violation_count: 1
