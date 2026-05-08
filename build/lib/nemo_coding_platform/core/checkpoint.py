from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from nemo_coding_platform.core.aider_interface import MutationResult
from nemo_coding_platform.core.contracts import ExecutionPhase
from nemo_coding_platform.core.task_run import MemoryTrace, RunEvent
from nemo_coding_platform.core.validation import ValidationSuiteResult


@dataclass(frozen=True, slots=True)
class CheckpointDiff:
    """Represents incremental changes since last checkpoint."""
    checkpoint_id: str
    timestamp: float
    phase: ExecutionPhase
    phase_elapsed_seconds: float
    global_elapsed_seconds: float
    mutation_count: int
    validation_results: tuple[str, ...]
    changed_files: tuple[str, ...]
    repair_attempts: int
    timeline_events_count: int
    schema_version: int = 2
    risk_flags: tuple[str, ...] = ()
    resume_mode: str = "phase_boundary"
    repair_cursor: int = 0
    runtime_snapshot_manifest: tuple[str, ...] = ()
    validation_state: tuple[str, ...] = ()
    nemo_evidence_handles: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def snapshot_changed_files(
    repo_path: str | Path,
    changed_files: list[str],
    runtime_path: str | Path,
    checkpoint_id: str,
) -> list[str]:
    """Copy changed files from repo into a per-checkpoint file-snapshot store.

    Each file is saved under ``{runtime_path}/file-snapshots/{checkpoint_id}/{rel_path}``.
    Only regular files that actually exist in the repo are copied.

    Returns:
        List of relative paths that were successfully snapshotted.
    """
    repo = Path(repo_path)
    snapshot_dir = Path(runtime_path) / "file-snapshots" / checkpoint_id
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    stored: list[str] = []
    for rel_path in changed_files:
        src = repo / rel_path
        if not src.is_file():
            continue
        dest = snapshot_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())
        stored.append(rel_path)
    return stored


def restore_file_snapshot(
    snapshot_runtime_path: str | Path,
    checkpoint_id: str,
    repo_path: str | Path,
) -> list[str]:
    """Restore file state from a snapshot store back into the repo.

    Reads all files under ``{snapshot_runtime_path}/file-snapshots/{checkpoint_id}/``
    and writes them to the corresponding paths inside *repo_path*.

    Returns:
        List of relative paths (POSIX) that were restored, or an empty list when
        the snapshot directory does not exist.
    """
    snapshot_dir = Path(snapshot_runtime_path) / "file-snapshots" / checkpoint_id
    if not snapshot_dir.exists():
        return []
    repo = Path(repo_path)
    restored: list[str] = []
    for src in sorted(snapshot_dir.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(snapshot_dir)
        dest = repo / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())
        restored.append(rel.as_posix())
    return restored


def save_execution_snapshot(
    runtime_path: str | Path,
    checkpoint_id: str,
    phase: ExecutionPhase,
    phase_elapsed_seconds: float,
    global_elapsed_seconds: float,
    changed_files: list[str],
    timeline_events: list[RunEvent],
    mutation_count: int = 0,
    repair_attempts: int = 0,
    validation_results: list[str] | None = None,
    risk_flags: list[str] | None = None,
    resume_mode: str = "phase_boundary",
    repair_cursor: int = 0,
    runtime_snapshot_manifest: list[str] | None = None,
    validation_state: list[str] | None = None,
    nemo_evidence_handles: list[str] | None = None,
    snapshot_runtime_path: str | None = None,
) -> str:
    """Save an incremental execution snapshot to runtime.

    Args:
        runtime_path: Path to runtime directory.
        checkpoint_id: Unique identifier for this checkpoint.
        phase: Current execution phase.
        phase_elapsed_seconds: Time elapsed in current phase.
        global_elapsed_seconds: Total time elapsed since handoff start.
        changed_files: List of files changed since last checkpoint.
        timeline_events: All timeline events so far.
        mutation_count: Number of mutations executed.
        repair_attempts: Number of repair attempts.
        validation_results: Summary strings of validation results.
        risk_flags: Risk or escalation flags.
        snapshot_runtime_path: Base path where file-snapshots are stored for this
            checkpoint.  Stored in the outer JSON so resumers can locate the
            files without re-executing the snapshot capture.  Defaults to
            ``str(runtime_path)`` when not supplied.

    Returns:
        Path to checkpoint JSON file.
    """
    runtime_path = Path(runtime_path)
    runtime_path.mkdir(parents=True, exist_ok=True)

    snapshot = CheckpointDiff(
        schema_version=2,
        checkpoint_id=checkpoint_id,
        timestamp=time.time(),
        phase=phase,
        phase_elapsed_seconds=phase_elapsed_seconds,
        global_elapsed_seconds=global_elapsed_seconds,
        mutation_count=mutation_count,
        validation_results=tuple(validation_results or []),
        changed_files=tuple(changed_files),
        repair_attempts=repair_attempts,
        timeline_events_count=len(timeline_events),
        risk_flags=tuple(risk_flags or []),
        resume_mode=resume_mode,
        repair_cursor=repair_cursor,
        runtime_snapshot_manifest=tuple(runtime_snapshot_manifest or []),
        validation_state=tuple(validation_state or []),
        nemo_evidence_handles=tuple(nemo_evidence_handles or []),
    )

    checkpoint_path = runtime_path / f"{checkpoint_id}.json"
    checkpoint_data = {
        "schema_version": 2,
        "checkpoint_format": "nemo_checkpoint_v2",
        "snapshot": snapshot.to_dict(),
        "timeline_event_count": len(timeline_events),
        "changed_files": changed_files,
        "snapshot_runtime_path": snapshot_runtime_path or str(runtime_path),
        "recorded_at": time.time(),
    }

    checkpoint_path.write_text(json.dumps(checkpoint_data, indent=2), encoding="utf-8")
    return str(checkpoint_path)


def build_checkpoint_markdown(
    phase: str,
    changed_files: tuple[str, ...],
    validation: ValidationSuiteResult,
    memory_traces: tuple[MemoryTrace, ...],
    next_action: str,
    risk_flags: tuple[str, ...] = (),
    mutation_result: MutationResult | None = None,
) -> str:
    changed = "\n".join(f"- {path}" for path in changed_files) or "- none"
    memory = "\n".join(f"- {trace.nemo_tool}: {trace.summary}" for trace in memory_traces) or "- none"
    risks = "\n".join(f"- {risk}" for risk in risk_flags) or "- none"
    failures = "\n".join(
        f"- {result.command.command}: returncode={result.returncode} output={result.output or '<empty>'}"
        for result in validation.results
        if not result.passed and result.command.required
    ) or "- none"
    provider = "none"
    if mutation_result:
        model = mutation_result.model_profile.model if mutation_result.model_profile else "unknown-model"
        provider = f"{mutation_result.provider} model={model} changed={len(mutation_result.changed_files)}"
    return "\n".join(
        (
            "# Checkpoint",
            "",
            f"phase={phase}",
            f"provider={provider}",
            "",
            "## Changed Files",
            changed,
            "",
            "## Validation",
            validation.summary(),
            "",
            "## Failures",
            failures,
            "",
            "## NEMO Evidence",
            memory,
            "",
            "## Next Action",
            next_action,
            "",
            "## Risk Flags",
            risks,
        )
    )