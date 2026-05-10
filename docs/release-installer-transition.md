# Release Installer Transition Contract

## Purpose

Define the controlled transition from RC baseline bundle packaging to desktop installer delivery, with explicit gates for CI and first-run validation.

## Scope

This contract defines:

- Entry criteria to start installer mode.
- Required outputs for installer-capable release jobs.
- CI gate conditions for installer release candidate readiness.
- First-run validation requirements for desktop installer quality.
- Exit criteria for promoting installer mode to default release path.

It does not define full implementation details of installer build internals.

## Entry Criteria

Installer transition work can start only when all conditions are true:

1. Release confidence gate is green:
- `tests.test_release_confidence_e2e`
- `tests.test_global_mvp_gate`
- `tests.test_desktop_release_readiness_contract`

2. Go/no-go dossier exists and reports `decision=go`:
- `artifacts/release-go-no-go.json`
- `artifacts/release-go-no-go.md`

3. Long handoff operational confidence is ready:
- `artifacts/long-handoff-operational-confidence.json`
- decision is `rc_autonomy_ready`
- no violations.

4. RC bundle packaging baseline is reproducible:
- `scripts/package-release.ps1` succeeds.
- bundle includes `manifest.json`, `integrity.json`, `release-checklist.md`.

## Installer Outputs (Required)

Installer-capable jobs must produce:

1. Desktop installer artifact(s):
- Windows: `.msi` or `.exe`

2. Installer metadata:
- artifact name
- version
- build timestamp UTC
- checksum (SHA-256)

3. Installer release evidence:
- installer smoke output log
- first-run validation report
- linkage to release go/no-go dossier.

## CI Integration Contract

Installer mode is enabled behind an explicit switch.

Recommended controls:

1. Workflow input or environment toggle:
- `INSTALLER_MODE=true|false`

2. Behavior when `INSTALLER_MODE=false`:
- keep RC baseline path as canonical.
- still publish go/no-go dossier artifacts.

3. Behavior when `INSTALLER_MODE=true`:
- run installer build stage.
- run first-run validation stage.
- publish installer artifacts and installer evidence.
- fail job if any installer-required check fails.

Current implementation status:

- CI workflow includes real installer preview job on Windows under `INSTALLER_MODE=true`.
- Installer evidence is published as `installer-transition-evidence` artifact including checksums and bundle outputs.

## First-Run Validation Contract

Installer first-run validation must verify:

1. App launches successfully without manual file edits.
2. Backend lifecycle can be started from app shell.
3. Startup diagnostics are available and actionable.
4. Settings can be edited and persisted from onboarding:
- LM Studio URL
- NEMO database path
- auto-start backend
5. Mission Control UI recovery path is visible when frontend is unavailable.
6. No secret leakage in surfaced provider error text.

## Blocking Failure Conditions

Installer release must be marked failed if any of these occur:

1. Installer artifact missing.
2. Installer checksum missing.
3. First-run validation fails.
4. Release confidence gate fails.
5. Go/no-go decision is `no-go`.

## Exit Criteria (Installer Becomes Default)

Installer mode can replace RC baseline as default only when:

1. Installer path is green for at least 3 consecutive CI runs.
2. First-run validation is green for the same runs.
3. Release go/no-go remains `go` across those runs.
4. Packaging docs are updated to make installer path primary and RC baseline fallback.

## Operational Notes

1. Keep RC baseline path available as fallback until installer mode proves stable.
2. Do not remove go/no-go dossier generation from pipeline.
3. Keep evidence artifact names stable to avoid breaking downstream automation.

## References

- `docs/release-packaging.md`
- `scripts/package-release.ps1`
- `scripts/generate-release-go-no-go.py`
- `.github/workflows/nemo-code-ci.yml`
- `artifacts/release-go-no-go.json`
- `artifacts/long-handoff-operational-confidence.json`
