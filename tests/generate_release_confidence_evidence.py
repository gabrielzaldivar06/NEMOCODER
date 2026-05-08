from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
from pathlib import Path

from nemo_coding_platform.cli import main
from nemo_coding_platform.mission_control_server import (
    MissionControlServerConfig,
    api_apply,
    api_applies,
    api_file,
    api_review,
    api_rollback,
    api_state,
)
from tests.test_review_gate_cli import write_ready_run


def build_release_confidence_evidence() -> dict[str, object]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = root / "repo"
        sandbox = root / "sandbox"
        runtimes = root / "runtimes"
        apply_results = root / "apply-results"
        repo.mkdir()
        sandbox.mkdir()
        runtimes.mkdir()

        (repo / "existing.txt").write_text("old\n", encoding="utf-8")
        (sandbox / "existing.txt").write_text("new\n", encoding="utf-8")
        (sandbox / "created.txt").write_text("created\n", encoding="utf-8")

        run_json = runtimes / "run.json"
        write_ready_run(run_json, repo, sandbox, ["existing.txt", "created.txt"])
        config = MissionControlServerConfig.from_paths(repo, runtimes, apply_results, None)

        replay_run_json = root / "headless-result.json"
        with contextlib.redirect_stdout(io.StringIO()):
            replay_headless_exit = main([
                "headless-run",
                "Release confidence evidence",
                "--provider",
                "fake",
                "--json",
                "--save-json",
                str(replay_run_json),
            ])

        review_payload = api_review(config, {"source_json": str(run_json)})
        file_payload = api_file(config, {"source_json": str(run_json), "file_path": "existing.txt"})
        apply_payload = api_apply(config, {"source_json": str(run_json), "approve_review": True})
        apply_json = Path(apply_payload["apply_json"])
        applies_payload = api_applies(config)

        replay_output = io.StringIO()
        with contextlib.redirect_stdout(replay_output):
            replay_exit = main(["replay-run-json", str(replay_run_json), "--json"])
        replay_payload = json.loads(replay_output.getvalue())

        rollback_payload = api_rollback(config, {"apply_json": str(apply_json), "approve_review": True})
        state_payload = api_state(config)

        return {
            "release_confidence": {
                "run_json": str(run_json),
                "apply_json": str(apply_json),
                "review": {
                    "ok": review_payload["ok"],
                    "mergeable": review_payload["plan"]["mergeable"],
                    "changed_files": review_payload["plan"]["changed_files"],
                    "risk_flags": review_payload["plan"]["risk_flags"],
                },
                "file_preview": {
                    "operation": file_payload["operation"],
                    "hunk_count": len(file_payload["hunks"]),
                    "file_risk_flags": file_payload["file_risk_flags"],
                },
                "apply": apply_payload["result"],
                "apply_history_count": len(applies_payload["applies"]),
                "replay": {
                    "headless_exit_code": replay_headless_exit,
                    "exit_code": replay_exit,
                    "run_json": str(replay_run_json),
                    "can_replay": replay_payload["can_replay"],
                    "grade": replay_payload["grade"],
                    "event_count": replay_payload["event_count"],
                    "artifact_paths": replay_payload["artifact_paths"],
                    "payload_refs": replay_payload["payload_refs"],
                },
                "rollback": rollback_payload["result"],
                "state": {
                    "run_count": len(state_payload.get("runs", [])),
                    "approval_queue_count": len(state_payload.get("approval_queue", [])),
                },
            }
        }


def to_markdown(payload: dict[str, object]) -> str:
    evidence = payload["release_confidence"]
    review = evidence["review"]
    file_preview = evidence["file_preview"]
    apply_result = evidence["apply"]
    replay = evidence["replay"]
    rollback = evidence["rollback"]
    state = evidence["state"]
    lines = [
        "# Release Confidence Evidence",
        "",
        f"run_json={evidence['run_json']}",
        f"apply_json={evidence['apply_json']}",
        "",
        "## Review",
        f"- ok: {str(review['ok']).lower()}",
        f"- mergeable: {str(review['mergeable']).lower()}",
        f"- changed_files: {', '.join(review['changed_files']) or 'none'}",
        f"- risk_flags: {', '.join(review['risk_flags']) or 'none'}",
        "",
        "## File Preview",
        f"- operation: {file_preview['operation']}",
        f"- hunk_count: {file_preview['hunk_count']}",
        f"- file_risk_flags: {', '.join(file_preview['file_risk_flags']) or 'none'}",
        "",
        "## Apply",
        f"- applied_files: {', '.join(apply_result['applied_files']) or 'none'}",
        f"- created_files: {', '.join(apply_result['created_files']) or 'none'}",
        f"- updated_files: {', '.join(apply_result['updated_files']) or 'none'}",
        f"- apply_history_count: {evidence['apply_history_count']}",
        "",
        "## Replay",
        f"- headless_exit_code: {replay['headless_exit_code']}",
        f"- exit_code: {replay['exit_code']}",
        f"- run_json: {replay['run_json']}",
        f"- can_replay: {str(replay['can_replay']).lower()}",
        f"- grade: {replay['grade']}",
        f"- event_count: {replay['event_count']}",
        f"- artifact_paths: {', '.join(replay['artifact_paths']) or 'none'}",
        f"- payload_refs: {', '.join(replay['payload_refs']) or 'none'}",
        "",
        "## Rollback",
        f"- restored_files: {', '.join(rollback['restored_files']) or 'none'}",
        f"- deleted_files: {', '.join(rollback['deleted_files']) or 'none'}",
        "",
        "## State",
        f"- run_count: {state['run_count']}",
        f"- approval_queue_count: {state['approval_queue_count']}",
        "",
    ]
    return "\n".join(lines)


def main_cli() -> int:
    parser = argparse.ArgumentParser(description="Generate release confidence evidence artifacts.")
    parser.add_argument("--json-out", required=True)
    parser.add_argument("--md-out", required=True)
    args = parser.parse_args()

    payload = build_release_confidence_evidence()
    json_path = Path(args.json_out)
    md_path = Path(args.md_out)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(to_markdown(payload), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
