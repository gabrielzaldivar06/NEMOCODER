from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nemo_coding_platform.core.headless_runner import execute_headless_handoff
from nemo_coding_platform.core.headless_handoff import HandoffRequest
from nemo_coding_platform.core.persistence import load_headless_result_json
from nemo_coding_platform.core.nemo_adapter import McpNemoAdapter, PersistentNemoAdapter, StdioMcpNemoAdapter
from nemo_coding_platform.core.vscode_mcp_config import VSCODE_STDIO_NEMO_URL
from nemo_coding_platform.core.memory_persistence import PersistentMemoryStore
from nemo_coding_platform.core.nemo_lifecycle import NemoLifecyclePhase, tool_allowed_in_lifecycle
from nemo_coding_platform.core.self_modification import SelfModRequest, SelfModTaskType, execute_self_modification, get_self_mod_continuity, learn_from_self_mod_failure, mark_portfolio_effective, query_self_mod_risk_patterns, record_self_mod_decision, record_self_mod_feedback, self_mod_apply, self_mod_impact, self_mod_review, self_mod_rollback, self_mod_similar_runs, self_mod_status, self_mod_trajectory


def _get_adapter(memory_db: str = ".nemo-memory.db", mcp_url: str = "", mcp_prefix: str = "") -> PersistentNemoAdapter | McpNemoAdapter | StdioMcpNemoAdapter:
    if mcp_url.strip():
        if mcp_url.strip().lower() == VSCODE_STDIO_NEMO_URL:
            return StdioMcpNemoAdapter(server_name="nemo", tool_prefix=mcp_prefix)
        return McpNemoAdapter(mcp_url.strip(), tool_prefix=mcp_prefix)
    store = PersistentMemoryStore(Path(memory_db))
    return PersistentNemoAdapter(store)


def _default_lifecycle_phase(tool_name: str) -> NemoLifecyclePhase:
    for phase in (NemoLifecyclePhase.START, NemoLifecyclePhase.PLAN, NemoLifecyclePhase.BUILD, NemoLifecyclePhase.REVIEW, NemoLifecyclePhase.CLOSE):
        if tool_allowed_in_lifecycle(phase, tool_name):
            return phase
    raise PermissionError(f"NEMO tool {tool_name} is not allowed in any lifecycle phase")


def mcp_call_nemo_tool(
    tool_name: str,
    lifecycle_phase: str | None = None,
    memory_db: str = ".nemo-memory.db",
    mcp_url: str = "",
    mcp_prefix: str = "",
    **arguments: Any,
) -> dict[str, Any]:
    """MCP tool for calling any registered NEMO memory/tool-plane operation."""
    try:
        arguments.pop("approve_review", None)
        phase = NemoLifecyclePhase(lifecycle_phase) if lifecycle_phase else _default_lifecycle_phase(tool_name)
        adapter = _get_adapter(memory_db, mcp_url=mcp_url, mcp_prefix=mcp_prefix)
        _, result = adapter.call(phase, tool_name, **arguments)
        return {
            "ok": result.ok,
            "tool": tool_name,
            "lifecycle_phase": phase.value,
            "transport": "vscode_stdio" if mcp_url.strip().lower() == VSCODE_STDIO_NEMO_URL else ("mcp_remote" if mcp_url.strip() else "persistent_local"),
            "payload": result.payload,
        }
    except Exception as e:
        return {"ok": False, "tool": tool_name, "error": str(e)}


def mcp_prime_context(topic: str = "general", limit: int = 10, **kwargs: Any) -> dict[str, Any]:
    """MCP tool for priming context."""
    try:
        adapter = _get_adapter()
        _, result = adapter.call(NemoLifecyclePhase.START, "prime_context", topic=topic, limit=limit)
        return result.payload
    except Exception as e:
        return {"ok": False, "error": str(e)}


def mcp_build_context_portfolio(task: str, topic: str = "general", token_budget: int = 600, **kwargs: Any) -> dict[str, Any]:
    """MCP tool for building a context portfolio."""
    try:
        adapter = _get_adapter()
        _, result = adapter.call(NemoLifecyclePhase.BUILD, "build_context_portfolio", task=task, topic=topic, token_budget=token_budget)
        return result.payload
    except Exception as e:
        return {"ok": False, "error": str(e)}


def mcp_run_headless(objective: str, repo_path: str, target_files: list[str] | None = None, **kwargs: Any) -> dict[str, Any]:
    """MCP tool for running a headless Space Code task."""
    try:
        request = HandoffRequest(
            prd=objective,
            repo_path=repo_path,
            acceptance_criteria=("satisfies the objective",),
            validation_commands=("python -m unittest",)
        )

        result = execute_headless_handoff(
            request,
            target_files=tuple(target_files or []),
            **kwargs
        )

        return {
            "ok": True,
            "task_id": result.task.id,
            "run_id": result.run.id,
            "summary": f"Task {result.task.id} finished with validation_passed={result.validation.passed}.",
            "changed_files": list(result.effective_changed_files)
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def mcp_self_modify(
    description: str,
    task_type: str = "bug_fix",
    target_files: list[str] | None = None,
    validation_policy: str = "smoke",
    repair_budget: int = 2,
    memory_db: str = ".nemo-runtimes/nemo-memory.sqlite",
    repo_root: str | None = None,
    provider_mode: str = "subprocess",
    bounded_simulation: bool = False,
    real_validation: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """MCP tool for running a controlled self-modification task against Space Code."""
    try:
        request = SelfModRequest(
            description=description,
            task_type=SelfModTaskType(task_type),
            target_files=tuple(target_files or ()),
            validation_policy=validation_policy,
            repair_budget=repair_budget,
            memory_db=memory_db,
            repo_root=repo_root,
            provider_mode=provider_mode,
            bounded_simulation=bounded_simulation,
            real_validation=real_validation,
        )
        result = execute_self_modification(request)
        payload = result.to_summary_dict()
        payload["ok"] = True
        return payload
    except Exception as e:
        return {"ok": False, "error": str(e)}


def mcp_self_mod_status(run_json: str, **kwargs: Any) -> dict[str, Any]:
    """MCP tool for reading self-modification run status."""
    try:
        payload = self_mod_status(run_json)
        payload["ok"] = True
        return payload
    except Exception as e:
        return {"ok": False, "error": str(e)}


def mcp_self_mod_review(run_json: str, permissions_file: str | None = None, **kwargs: Any) -> dict[str, Any]:
    """MCP tool for building a self-modification review and risk report."""
    try:
        payload = self_mod_review(run_json, permissions_file)
        payload["ok"] = True
        return payload
    except Exception as e:
        return {"ok": False, "error": str(e)}


def mcp_self_mod_apply(run_json: str, approve_review: bool = False, backup_dir: str | None = None, permissions_file: str | None = None, autonomy_profile: str = "manual", memory_db: str | None = None, portfolio_id: str | None = None, **kwargs: Any) -> dict[str, Any]:
    """MCP tool for applying an approved self-modification run."""
    try:
        payload = self_mod_apply(run_json, approve_review=approve_review, backup_dir=backup_dir, permissions_file=permissions_file, autonomy_profile=autonomy_profile, memory_db=memory_db, portfolio_id=portfolio_id)
        payload["ok"] = True
        return payload
    except Exception as e:
        return {"ok": False, "error": str(e)}


def mcp_record_self_mod_decision(memory_db: str, objective: str, chosen_path: str, rationale: str, alternatives: list[str] | None = None, task_type: str = "", run_json: str | None = None, task_id: str | None = None, run_id: str | None = None, tags: list[str] | None = None, importance: int = 8, **kwargs: Any) -> dict[str, Any]:
    """MCP tool for recording a self-modification strategy decision."""
    try:
        payload = record_self_mod_decision(memory_db, objective=objective, chosen_path=chosen_path, rationale=rationale, alternatives=tuple(alternatives or ()), task_type=task_type, run_json=run_json, task_id=task_id, run_id=run_id, tags=tuple(tags or ()), importance=importance)
        payload["ok"] = True
        return payload
    except Exception as e:
        return {"ok": False, "error": str(e)}


def mcp_record_self_mod_feedback(memory_db: str, run_json: str, approved: bool, issues_found: list[str] | None = None, follow_up_work: list[str] | None = None, portfolio_id: str | None = None, atom_ids: list[str] | None = None, evidence_handle: str | None = None, token_delta: int = 0, **kwargs: Any) -> dict[str, Any]:
    """MCP tool for recording review/apply feedback for a self-modification run."""
    try:
        payload = record_self_mod_feedback(memory_db, run_json=run_json, approved=approved, issues_found=tuple(issues_found or ()), follow_up_work=tuple(follow_up_work or ()), portfolio_id=portfolio_id, atom_ids=tuple(atom_ids or ()), evidence_handle=evidence_handle, token_delta=token_delta)
        payload["ok"] = True
        return payload
    except Exception as e:
        return {"ok": False, "error": str(e)}


def mcp_learn_from_self_mod_failure(memory_db: str, run_json: str, failure_pattern: str, suggested_correction: str, confidence: float = 0.7, **kwargs: Any) -> dict[str, Any]:
    """MCP tool for turning a failed self-modification run into durable correction memory."""
    try:
        payload = learn_from_self_mod_failure(memory_db, run_json, failure_pattern=failure_pattern, suggested_correction=suggested_correction, confidence=confidence)
        payload["ok"] = True
        return payload
    except Exception as e:
        return {"ok": False, "error": str(e)}


def mcp_mark_portfolio_effective(memory_db: str, portfolio_id: str, task_type: str = "", effectiveness_score: int = 8, context_savings: int = 0, run_json: str | None = None, **kwargs: Any) -> dict[str, Any]:
    """MCP tool for boosting future reuse of a self-modification portfolio."""
    try:
        payload = mark_portfolio_effective(memory_db, portfolio_id=portfolio_id, task_type=task_type, effectiveness_score=effectiveness_score, context_savings=context_savings, run_json=run_json)
        payload["ok"] = True
        return payload
    except Exception as e:
        return {"ok": False, "error": str(e)}


def mcp_get_self_mod_continuity(memory_db: str, task_objective: str = "", task_type: str = "", limit: int = 6, include_abandoned: bool = True, **kwargs: Any) -> dict[str, Any]:
    """MCP tool for retrieving prior self-modification attempts and lessons."""
    try:
        payload = get_self_mod_continuity(memory_db, task_objective=task_objective, task_type=task_type, limit=limit, include_abandoned=include_abandoned)
        payload["ok"] = True
        return payload
    except Exception as e:
        return {"ok": False, "error": str(e)}


def mcp_query_self_mod_risk_patterns(memory_db: str, file_or_module: str = "", risk_category: str = "", limit: int = 10, **kwargs: Any) -> dict[str, Any]:
    """MCP tool for retrieving learned self-modification risk patterns."""
    try:
        payload = query_self_mod_risk_patterns(memory_db, file_or_module=file_or_module, risk_category=risk_category, limit=limit)
        payload["ok"] = True
        return payload
    except Exception as e:
        return {"ok": False, "error": str(e)}


def mcp_self_mod_rollback(apply_json: str, approve_review: bool = False, **kwargs: Any) -> dict[str, Any]:
    """MCP tool for rolling back an approved self-modification apply result."""
    try:
        payload = self_mod_rollback(apply_json, approve_review=approve_review)
        payload["ok"] = True
        return payload
    except Exception as e:
        return {"ok": False, "error": str(e)}


def mcp_self_mod_trajectory(run_json: str, **kwargs: Any) -> dict[str, Any]:
    """MCP tool for building a self-improvement trajectory from a run JSON."""
    try:
        payload = self_mod_trajectory(run_json)
        payload["ok"] = True
        return payload
    except Exception as e:
        return {"ok": False, "error": str(e)}


def mcp_self_mod_impact(run_json: str, **kwargs: Any) -> dict[str, Any]:
    """MCP tool for analyzing self-modification impact and validation needs."""
    try:
        payload = self_mod_impact(run_json)
        payload["ok"] = True
        return payload
    except Exception as e:
        return {"ok": False, "error": str(e)}


def mcp_self_mod_similar_runs(memory_db: str = ".nemo-runtimes/nemo-memory.sqlite", query: str = "", limit: int = 6, **kwargs: Any) -> dict[str, Any]:
    """MCP tool for retrieving similar self-modification trajectories from NEMO."""
    try:
        payload = self_mod_similar_runs(memory_db, query, limit=limit)
        payload["ok"] = True
        return payload
    except Exception as e:
        return {"ok": False, "error": str(e)}


def mcp_get_run_result(source_json: str) -> dict[str, Any]:
    """MCP tool for retrieving a previous run result."""
    try:
        result_data = load_headless_result_json(source_json)
        return {"ok": True, "result": result_data}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def mcp_list_runs(runtimes_path: str = ".nemo-runtimes") -> dict[str, Any]:
    """MCP tool for listing available runs."""
    try:
        roots = (Path(runtimes_path) / "runs", Path(runtimes_path) / "mission-control" / "runs")
        runs = []
        for path in roots:
            if not path.exists():
                continue
            for f in path.glob("*.json"):
                runs.append({
                    "name": f.name,
                    "path": str(f),
                    "mtime": f.stat().st_mtime
                })
        return {"ok": True, "runs": sorted(runs, key=lambda x: x["mtime"], reverse=True)}
    except Exception as e:
        return {"ok": False, "error": str(e)}
