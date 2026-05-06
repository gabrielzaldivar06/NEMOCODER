# Spec: Worktree Runtime

## Purpose

Provide the first isolation backend for autonomous and Full Handoff execution. Worktrees are the fastest path to safe local autonomy before container isolation is implemented.

## Runtime Model

- Each autonomous task run gets a runtime identity.
- Each runtime owns an isolated git worktree.
- Commands execute inside the worktree.
- Aider applies changes inside the worktree.
- Merge back to the main workspace requires review gate.

## Requirements

- Create worktree path from task/run identity.
- Refuse path escapes outside runtime root.
- Track runtime state transitions.
- Capture command output as artifacts or events.
- Support pause/resume/cancel semantics.
- Cleanup must preserve artifacts needed for replay.

## Full Handoff Requirements

- Full Handoff may start with worktree isolation in prototypes.
- Production Full Handoff should prefer container isolation once implemented.
- Full Handoff requires checkpoints while running in the worktree.
- Repeated validation failures must stop, pause, or downgrade the run.

## Acceptance Criteria

- High-autonomy levels require worktree or stronger isolation.
- No command or mutation can target the main workspace directly.
- Merge requires explicit review decision.
- Runtime events can replay worktree creation, command execution, validation, and merge decisions.
