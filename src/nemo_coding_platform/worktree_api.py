"""Worktree merge-gate API helpers — extracted from mission_control_server.py."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from nemo_coding_platform.spacecode_mcp_tools import mcp_call_nemo_tool

if TYPE_CHECKING:
    from nemo_coding_platform.mission_control_server import (
        HandoffJob,
        HandoffJobManager,
        MissionControlServerConfig,
    )


def _job_runtime_id(job: "HandoffJob") -> str:
    from nemo_coding_platform.core.worktree_runtime import runtime_slug
    return f"{runtime_slug(job.task_id)}-{runtime_slug(job.run_id)}"


def _job_worktree_path(config: "MissionControlServerConfig", job: "HandoffJob") -> Path:
    return Path(config.repo_path) / ".worktrees" / _job_runtime_id(job)


def _job_worktree_branch(job: "HandoffJob") -> str:
    from nemo_coding_platform.core.worktree_runtime import worktree_branch_name
    return worktree_branch_name(_job_runtime_id(job))


def api_worktree_diff(
    config: "MissionControlServerConfig",
    job_id: str,
    jobs: "HandoffJobManager",
) -> dict[str, object]:
    try:
        job = jobs.get(job_id)
    except FileNotFoundError:
        return {"error": f"job not found: {job_id}"}
    from nemo_coding_platform.core.worktree_runtime import worktree_diff
    branch = _job_worktree_branch(job)
    wt_path = _job_worktree_path(config, job)
    diff = worktree_diff(Path(config.repo_path), branch)
    return {
        "diff": diff,
        "branch": branch,
        "runtime_id": _job_runtime_id(job),
        "worktree_exists": wt_path.exists(),
    }


def api_worktree_merge(
    config: "MissionControlServerConfig",
    job_id: str,
    payload: dict[str, object],
    jobs: "HandoffJobManager",
) -> dict[str, object]:
    if not payload.get("approved"):
        return {"error": "merge requires approved=true in request body"}
    try:
        job = jobs.get(job_id)
    except FileNotFoundError:
        return {"error": f"job not found: {job_id}"}
    from nemo_coding_platform.core.worktree_runtime import cleanup_git_worktree, merge_worktree_to_main
    branch = _job_worktree_branch(job)
    wt_path = _job_worktree_path(config, job)
    message = str(payload.get("message") or f"merge worktree {branch}")
    merged = merge_worktree_to_main(Path(config.repo_path), branch, message)
    if merged:
        cleanup_git_worktree(Path(config.repo_path), wt_path, branch)
    return {"merged": merged, "branch": branch}


def api_worktree_cleanup(
    config: "MissionControlServerConfig",
    job_id: str,
    jobs: "HandoffJobManager",
) -> dict[str, object]:
    try:
        job = jobs.get(job_id)
    except FileNotFoundError:
        return {"error": f"job not found: {job_id}"}
    from nemo_coding_platform.core.worktree_runtime import cleanup_git_worktree
    branch = _job_worktree_branch(job)
    wt_path = _job_worktree_path(config, job)
    cleanup_git_worktree(Path(config.repo_path), wt_path, branch)
    return {"cleaned_up": True, "branch": branch}


def api_run_reject(
    config: "MissionControlServerConfig",
    job_id: str,
    payload: dict[str, object],
    jobs: "HandoffJobManager",
) -> dict[str, object]:
    """Reject a handoff run: persist reason to NEMO create_correction and clean up the worktree."""
    try:
        job = jobs.get(job_id)
    except FileNotFoundError:
        return {"error": f"job not found: {job_id}"}
    reason = str(payload.get("reason") or "").strip() or "user rejected without providing a reason"
    note = str(payload.get("note") or "").strip()
    objective = str(job.payload.get("objective") or job.job_id)[:120]
    _nemo_db = getattr(config, "memory_db", None)
    _nemo_url = str(job.payload.get("nemo_mcp_url") or getattr(config, "nemo_mcp_url", "") or "")
    if bool(_nemo_db) or bool((_nemo_url or "").strip()):
        try:
            mcp_call_nemo_tool(
                "create_correction",
                lifecycle_phase="review",
                memory_db=str(_nemo_db) if _nemo_db else "",
                mcp_url=_nemo_url,
                wrong_assumption=f"run '{job_id}' with objective '{objective}' would produce acceptable results",
                correct_answer=f"user rejected: {reason[:200]}",
                context=f"job_id={job_id} note={note[:100]}" if note else f"job_id={job_id}",
                topic="user_rejections",
                tags=["user_rejected", "handoff_rejection"],
                importance_level=9,
            )
        except Exception:  # noqa: BLE001
            pass  # NEMO persistence is best-effort — cleanup still happens
    from nemo_coding_platform.core.worktree_runtime import cleanup_git_worktree
    branch = _job_worktree_branch(job)
    wt_path = _job_worktree_path(config, job)
    cleanup_git_worktree(Path(config.repo_path), wt_path, branch)
    return {"ok": True, "job_id": job_id, "reason": reason}
