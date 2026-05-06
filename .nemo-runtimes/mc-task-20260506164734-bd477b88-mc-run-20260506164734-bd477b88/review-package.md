# Review Package: Headless Handoff

## Provider
provider=fake-aider mode=fake model=nvidia.agentic.coder-4b summary=changed 1 file(s) through fake-aider
repair_attempts=0

## Changed Files
- generated-implementation.md

## Diff
--- before/generated-implementation.md
+++ after/generated-implementation.md
@@ -0,0 +1,24 @@
+# Generated Implementation
+
+Objective: hola
+Spec: generated-spec.md
+
+## Acceptance Criteria
+- passes validation
+
+## NEMO Context
+[artifact_state] Checkpoint writeback prepared: checkpoint-review.md for run run-1
+[artifact_state] Checkpoint writeback prepared: checkpoint-execute.md for run run-1
+[artifact_state] Checkpoint writeback prepared: checkpoint-plan.md for run run-1
+[artifact_state] Checkpoint writeback prepared: checkpoint-review.md for run mc-run-20260506062320-a75c143f
+[artifact_state] Checkpoint writeback prepared: checkpoint-execute.md for run mc-run-20260506062320-a75c143f
+[artifact_state] Checkpoint writeback prepared: checkpoint-plan.md for run mc-run-20260506062320-a75c143f
+[artifact_state] Checkpoint writeback prepared: checkpoint-review.md for run mc-run-20260506062944-aae8ca22
+[artifact_state] Checkpoint writeback prepared: checkpoint-execute.md for run mc-run-20260506062944-aae8ca22
+[artifact_state] Checkpoint writeback prepared: checkpoint-plan.md for run mc-run-20260506062944-aae8ca22
+[artifact_state] Checkpoint writeback prepared: checkpoint-review.md for run mc-run-20260506063746-eca07ae1
+[artifact_state] Checkpoint writeback prepared: checkpoint-execute.md for run mc-run-20260506063746-eca07ae1
+[artifact_state] Checkpoint writeback prepared: checkpoint-plan.md for run mc-run-20260506063746-eca07ae1
+[artifact_state] Checkpoint writeback prepared: checkpoint-review.md for run mc-run-20260506063822-3ca25316
+[artifact_state] Checkpoint writeback prepared: checkpoint-execute.md for run mc-run-20260506063822-3ca25316
+[artifact_state] Checkpoint writeback prepared: checkpoint-plan.md for run mc-run-20260506063822-3ca25316

## Validation
validation passed=1/1 required_ok=True
command=python -m unittest
status=passed
returncode=None
output:
simulated pass

## Memory
- prime_context: Loaded startup context.
- build_context_portfolio: Built context portfolio tokens=322.
- store_conversation: Prepared final writeback.

## Risks
No unresolved risks.

status=ready