# Release Go/No-Go

Decision: **go**

## Violations
- none

## Evidence
- release_confidence: artifacts\release-confidence.json
- long_handoff_operational_confidence: artifacts\long-handoff-operational-confidence.json
- ci_workflow: .github\workflows\nemo-code-ci.yml
- latest_release_bundle: .release\nemocode-0.1.0-rc-protocol
- latest_release_zip: .release\nemocode-0.1.0-rc-protocol.zip
- bundle_files: {'manifest': '.release\\nemocode-0.1.0-rc-protocol\\manifest.json', 'integrity': '.release\\nemocode-0.1.0-rc-protocol\\integrity.json', 'checklist': '.release\\nemocode-0.1.0-rc-protocol\\release-checklist.md'}

## Thresholds
- benchmark_gate_must_pass: True
- release_confidence_replay_grade: ready
- review_mergeable_required: True
- long_handoff_decision: rc_autonomy_ready
- ci_gate_tests_required: ['tests.test_release_confidence_e2e', 'tests.test_global_mvp_gate', 'tests.test_desktop_release_readiness_contract']
- release_bundle_artifacts_required: ['manifest.json', 'integrity.json', 'release-checklist.md', '<bundle>.zip']
- require_release_bundle: True

Generated UTC: 2026-05-09T21:57:25.499967+00:00
