# Long Handoff Operational Confidence (RC)

Decision: **rc_autonomy_ready**

## Summary
- Runs: 2
- Validation pass rate: 1.00
- Replay ready rate: 1.00
- Ready grade rate: 1.00
- Resume available: True
- Resumed link present: True
- Lineage complete: True
- Lineage autonomy ready: True
- Lineage forked: False
- No-op signal count: 0
- Rollback incidence: 0

## Thresholds
- min_validation_pass_rate: 1.0
- min_replay_ready_rate: 1.0
- require_resume_path: True
- require_lineage_complete: True
- max_no_op_signal_count: 0

## Violations
- none

## Evidence Files
- artifacts/long-handoff-paused.out.json
- artifacts/long-handoff-resume-plan.out.json
- artifacts/long-handoff-continued.out.json
- artifacts/long-handoff-paused-replay.json
- artifacts/long-handoff-continued-replay.json
- artifacts/long-handoff-lineage.json