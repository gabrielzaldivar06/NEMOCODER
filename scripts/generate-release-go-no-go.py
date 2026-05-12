from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class DecisionContext:
    release_confidence_path: Path
    long_handoff_confidence_path: Path
    ci_workflow_path: Path
    release_root: Path


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_latest_release_bundle(release_root: Path) -> tuple[Path | None, Path | None]:
    if not release_root.exists():
        return None, None
    bundle_dirs = [p for p in release_root.iterdir() if p.is_dir() and p.name.startswith("space-code-")]
    if not bundle_dirs:
        return None, None
    latest_dir = max(bundle_dirs, key=lambda p: p.stat().st_mtime)
    zip_path = release_root / f"{latest_dir.name}.zip"
    return latest_dir, (zip_path if zip_path.exists() else None)


def build_release_decision(context: DecisionContext, require_release_bundle: bool = True) -> dict[str, Any]:
    violations: list[str] = []

    release_confidence = _load_json(context.release_confidence_path).get("release_confidence", {})
    long_handoff = _load_json(context.long_handoff_confidence_path)
    workflow_text = context.ci_workflow_path.read_text(encoding="utf-8")

    benchmark_gate = release_confidence.get("benchmark_gate", {})
    replay = release_confidence.get("replay", {})
    review = release_confidence.get("review", {})

    if benchmark_gate.get("baseline_exit_code") != 0:
        violations.append("benchmark_baseline_failed")
    if benchmark_gate.get("pass_case_exit_code") != 0 or not benchmark_gate.get("pass_case_passed", False):
        violations.append("benchmark_pass_case_failed")
    if benchmark_gate.get("fail_case_exit_code") != 1 or benchmark_gate.get("fail_case_passed", True):
        violations.append("benchmark_fail_case_not_detected")

    if not replay.get("can_replay", False):
        violations.append("release_confidence_replay_not_ready")
    if replay.get("grade") != "ready":
        violations.append("release_confidence_grade_not_ready")
    if not review.get("mergeable", False):
        violations.append("review_not_mergeable")

    if long_handoff.get("decision") != "rc_autonomy_ready":
        violations.append("long_handoff_decision_not_ready")
    if long_handoff.get("violations"):
        violations.append("long_handoff_has_violations")

    required_ci_tests = (
        "tests.test_release_confidence_e2e",
        "tests.test_global_mvp_gate",
        "tests.test_desktop_release_readiness_contract",
    )
    for test_name in required_ci_tests:
        if test_name not in workflow_text:
            violations.append(f"ci_gate_missing:{test_name}")

    bundle_dir, bundle_zip = _find_latest_release_bundle(context.release_root)
    bundle_files = {
        "manifest": None,
        "integrity": None,
        "checklist": None,
    }
    if bundle_dir is not None:
        manifest = bundle_dir / "manifest.json"
        integrity = bundle_dir / "integrity.json"
        checklist = bundle_dir / "release-checklist.md"
        bundle_files = {
            "manifest": str(manifest) if manifest.exists() else None,
            "integrity": str(integrity) if integrity.exists() else None,
            "checklist": str(checklist) if checklist.exists() else None,
        }
        if require_release_bundle and not manifest.exists():
            violations.append("release_bundle_manifest_missing")
        if require_release_bundle and not integrity.exists():
            violations.append("release_bundle_integrity_missing")
        if require_release_bundle and not checklist.exists():
            violations.append("release_bundle_checklist_missing")
    elif require_release_bundle:
        violations.append("release_bundle_missing")

    if require_release_bundle and bundle_zip is None:
        violations.append("release_bundle_zip_missing")

    decision = "go" if not violations else "no-go"

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "decision": decision,
        "violations": violations,
        "thresholds": {
            "benchmark_gate_must_pass": True,
            "release_confidence_replay_grade": "ready",
            "review_mergeable_required": True,
            "long_handoff_decision": "rc_autonomy_ready",
            "ci_gate_tests_required": list(required_ci_tests),
            "release_bundle_artifacts_required": [
                "manifest.json",
                "integrity.json",
                "release-checklist.md",
                "<bundle>.zip",
            ],
            "require_release_bundle": require_release_bundle,
        },
        "evidence_refs": {
            "release_confidence": str(context.release_confidence_path),
            "long_handoff_operational_confidence": str(context.long_handoff_confidence_path),
            "ci_workflow": str(context.ci_workflow_path),
            "latest_release_bundle": str(bundle_dir) if bundle_dir else None,
            "latest_release_zip": str(bundle_zip) if bundle_zip else None,
            "bundle_files": bundle_files,
        },
        "snapshot": {
            "release_confidence_replay": {
                "can_replay": replay.get("can_replay"),
                "grade": replay.get("grade"),
            },
            "release_confidence_review": {
                "mergeable": review.get("mergeable"),
            },
            "release_confidence_benchmark_gate": benchmark_gate,
            "long_handoff": {
                "decision": long_handoff.get("decision"),
                "violations": long_handoff.get("violations", []),
            },
        },
    }
    return payload


def _to_markdown(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Release Go/No-Go")
    lines.append("")
    lines.append(f"Decision: **{payload['decision']}**")
    lines.append("")
    lines.append("## Violations")
    violations = payload.get("violations", [])
    if violations:
        for violation in violations:
            lines.append(f"- {violation}")
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Evidence")
    evidence = payload.get("evidence_refs", {})
    for key, value in evidence.items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    lines.append("## Thresholds")
    thresholds = payload.get("thresholds", {})
    for key, value in thresholds.items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    lines.append(f"Generated UTC: {payload['generated_at_utc']}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate release go/no-go artifacts from existing evidence")
    parser.add_argument("--release-confidence", default="artifacts/release-confidence.json")
    parser.add_argument("--long-handoff-confidence", default="artifacts/long-handoff-operational-confidence.json")
    parser.add_argument("--ci-workflow", default=".github/workflows/nemo-code-ci.yml")
    parser.add_argument("--release-root", default=".release")
    parser.add_argument("--json-out", default="artifacts/release-go-no-go.json")
    parser.add_argument("--md-out", default="artifacts/release-go-no-go.md")
    parser.add_argument("--allow-missing-release-bundle", action="store_true")
    args = parser.parse_args()

    context = DecisionContext(
        release_confidence_path=Path(args.release_confidence),
        long_handoff_confidence_path=Path(args.long_handoff_confidence),
        ci_workflow_path=Path(args.ci_workflow),
        release_root=Path(args.release_root),
    )

    payload = build_release_decision(context, require_release_bundle=not args.allow_missing_release_bundle)

    json_out = Path(args.json_out)
    md_out = Path(args.md_out)
    json_out.parent.mkdir(parents=True, exist_ok=True)
    md_out.parent.mkdir(parents=True, exist_ok=True)

    json_out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    md_out.write_text(_to_markdown(payload), encoding="utf-8")

    print(json.dumps({"decision": payload["decision"], "violations": payload["violations"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
