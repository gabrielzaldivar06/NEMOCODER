# Spec: Desktop Product Contract

## Purpose

Define the product shell and core UI surface contract for NEMO Coding Platform. Desktop is the product surface; CLI remains a harness for automation, debugging, and CI.

## Product Surface

- Target surface: desktop application.
- CLI role: harness only.
- Desktop shell: Tauri.
- Backend protocol: local HTTP bridge.

## Required Core Surfaces

The desktop experience must expose these surfaces as first-class product capabilities:

- repo picker
- task workspace
- approval queue
- artifact timeline
- NEMO memory trace panel
- model and runtime settings

## Runtime And Data Boundaries

- The desktop shell does not replace backend authority; mutation and policy remain enforced in backend contracts.
- UI actions must map to explicit API calls and produce traceable run events/artifacts.
- Desktop state must be reconstructable from persisted run JSON and review artifacts.

## CLI Harness Requirements

- CLI commands must support equivalent lifecycle checkpoints for replay and diagnostics.
- CLI can create/evaluate/replay run JSON and is acceptable for pre-UI validation.
- CLI output must not redefine product surface ownership.

## Acceptance Criteria

- Product contract exposes desktop target and CLI harness role.
- Desktop shell and backend protocol are fixed and testable.
- Required core surfaces are represented in product contract tests.
- Task/run entities are reusable by both desktop and CLI paths.