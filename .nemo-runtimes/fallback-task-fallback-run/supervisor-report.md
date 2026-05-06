# Long Handoff Supervisor

max_runtime_minutes=30
heartbeat_minutes=10
max_heartbeats=2
token_budget=1200
pause_after_minutes=20
resume_token=fallback-task:fallback-run:minute-20

## Heartbeats
- minute 10: checkpoint-execute.md
- minute 20: checkpoint-execute.md

## Escalation Flags
- pause_requested
