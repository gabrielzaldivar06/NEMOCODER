# Spec: Quality Mutation Pipeline

## Purpose

Route every source-code mutation through Aider's quality/editing foundation and the platform's approval gates.

## Pipeline

1. Planner produces implementation intent.
2. Builder creates a mutation request.
3. Permission evaluator checks phase, target, and risk.
4. Quality Core prepares patch/diff.
5. Dry-run verifies creates/updates/deletes and workspace boundaries.
6. Apply occurs only in approved sandbox/runtime context.
7. Validation commands run.
8. Reviewer accepts, rejects, or requests repair.
9. Merge gate controls movement into the main workspace.

## Requirements

- Plan phase cannot write.
- Source writes outside workspace fail.
- Dry-run is mandatory before apply.
- Apply requires execution approval.
- Merge requires review approval.
- Validation failure blocks successful close unless explicitly waived and recorded.
- All patches and validation output become artifacts.

## Full Handoff Requirements

- The repair loop can retry within budget.
- Each retry records validation failure and next action.
- Full Handoff cannot ask the user during build unless it hits a stop condition.
- Final review package must include diff, validation, memory summary, and unresolved risks.

## Acceptance Criteria

- Tests prove dry-run and approval are required.
- Tests prove review approval is required.
- Tests prove path escape is rejected.
- Tests prove validation status is represented before close.
