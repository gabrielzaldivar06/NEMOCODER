# Data Model Contracts

This spec defines first-class data model entities required by PRD section 12.

## WorkflowRecipe Entity

- A headless run exposes an explicit workflow recipe with mode, step kinds, and execution safety flags.
- The recipe is persisted in run JSON payloads under `workflow_recipe`.

## ModelProfile Linkage

- Each run carries an explicit model profile linkage (`model`, `base_url`, `provider_mode`).
- This linkage is persisted in the `run` payload as `model_profile_link`.

## NemoMemoryEvent Entity

- Memory interactions recorded during a run use explicit `NemoMemoryEvent` records.
- Event records carry tool, suite, risk, summary, optional evidence handle, and memory ids.
