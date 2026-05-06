# Supervised Workflow

The initial workflow connects orchestration to the quality core. In the global desktop product, this supervised flow becomes the safety substrate for highly autonomous agent runs.

Autonomous agents may work for long periods in sandboxes or worktrees, but final writes to the main workspace still pass through these gates unless an explicit future policy spec says otherwise.

## Phases

1. Plan: read-only, no mutations.
2. Execute: scoped mutation plan may be dry-run.
3. Review: required approval before any write is applied.

## Mutation Flow

1. Build a `MutationPlan`.
2. Run `SupervisedWorkflowRunner.dry_run_execution()`.
3. Collect explicit execution approval.
4. Collect explicit review approval.
5. Apply through `QualityMutationEngine` only.

## CLI Probe

```powershell
python -m nemo_coding_platform dry-run-write demo.txt "hello" --workspace .
python -m nemo_coding_platform apply-write demo.txt "hello" --workspace . --approve-execution --approve-review
```

The CLI is intentionally strict. Missing `--approve-review` or `--approve-execution` returns exit code 1 and does not write files.