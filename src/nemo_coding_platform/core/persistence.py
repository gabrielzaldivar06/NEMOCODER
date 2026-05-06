from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from nemo_coding_platform.core.evals import score_headless_result


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {key: _json_value(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(_json_value(key)): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def headless_result_to_dict(result: object) -> dict[str, Any]:
    score = score_headless_result(result)
    return {
        "schema_version": 1,
        "task": _json_value(getattr(result, "task")),
        "run": _json_value(getattr(result, "run")),
        "timeline": _json_value(getattr(result, "timeline").events),
        "artifacts": _json_value(getattr(result, "artifacts")),
        "memory_traces": _json_value(getattr(result, "memory_traces")),
        "validation": _json_value(getattr(result, "validation")),
        "platform_info": _json_value(getattr(result, "platform_info", None)),
        "portfolio": _json_value(getattr(result, "portfolio", None)),
        "mutation_result": _json_value(getattr(result, "mutation_result", None)),
        "repair_plan": _json_value(getattr(result, "repair_plan", None)),
        "repair_result": _json_value(getattr(result, "repair_result", None)),
        "runtime_files": _json_value(getattr(result, "runtime_files", ())),
        "readiness": _json_value(score),
    }


def headless_result_to_json(result: object) -> str:
    return json.dumps(headless_result_to_dict(result), indent=2, sort_keys=True)


def save_headless_result_json(result: object, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(headless_result_to_json(result), encoding="utf-8")
    return target


def load_headless_result_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def summarize_persisted_result(payload: dict[str, Any]) -> dict[str, Any]:
    score = score_headless_result(payload)
    mutation = payload.get("mutation_result", {}) if isinstance(payload.get("mutation_result"), dict) else {}
    repair = payload.get("repair_result", {}) if isinstance(payload.get("repair_result"), dict) else {}
    repair_mutations = repair.get("mutation_results", []) if isinstance(repair, dict) else []
    repair_changed_files: list[str] = []
    for item in repair_mutations:
        if isinstance(item, dict) and item.get("changed_files"):
            repair_changed_files = item["changed_files"]
    return {
        "schema_version": payload.get("schema_version"),
        "task_id": payload.get("task", {}).get("id"),
        "run_id": payload.get("run", {}).get("id"),
        "events": len(payload.get("timeline", [])),
        "artifacts": len(payload.get("artifacts", [])),
        "provider": mutation.get("provider") if mutation else None,
        "changed_files": mutation.get("changed_files") or repair_changed_files,
        "model": mutation.get("model_profile", {}).get("model") if isinstance(mutation.get("model_profile"), dict) else None,
        "score": score.score,
        "grade": score.grade,
        "reasons": list(score.reasons),
    }


def build_replay_summary(payload: dict[str, Any]) -> dict[str, Any]:
    summary = summarize_persisted_result(payload)
    timeline = payload.get("timeline", [])
    artifacts = payload.get("artifacts", [])
    runtime_files = payload.get("runtime_files", [])
    mutation = payload.get("mutation_result", {}) if isinstance(payload.get("mutation_result"), dict) else {}
    validation = payload.get("validation", {}) if isinstance(payload.get("validation"), dict) else {}
    payload_refs = [event.get("payload_ref") for event in timeline if isinstance(event, dict) and event.get("payload_ref")]
    replay_events = [
        {
            "sequence": event.get("sequence"),
            "phase": event.get("phase"),
            "kind": event.get("kind"),
            "summary": event.get("summary"),
            "payload_ref": event.get("payload_ref"),
        }
        for event in timeline
        if isinstance(event, dict)
    ]
    artifact_paths = [artifact.get("path") for artifact in artifacts if isinstance(artifact, dict) and artifact.get("path")]
    validation_results = validation.get("results", []) if isinstance(validation, dict) else []
    return {
        **summary,
        "can_replay": bool(replay_events) and bool(artifact_paths) and not summary["reasons"],
        "events": replay_events,
        "event_count": len(replay_events),
        "artifact_paths": artifact_paths,
        "payload_refs": payload_refs,
        "runtime_files": runtime_files,
        "changed_files": mutation.get("changed_files") or summary["changed_files"],
        "validation_commands": [result.get("command", {}).get("command") for result in validation_results if isinstance(result, dict)],
    }


def _artifact_paths(payload: dict[str, Any]) -> list[str]:
    artifacts = payload.get("artifacts", [])
    return [artifact.get("path") for artifact in artifacts if isinstance(artifact, dict) and artifact.get("path")]


def _payload_refs(payload: dict[str, Any]) -> list[str]:
    timeline = payload.get("timeline", [])
    return [event.get("payload_ref") for event in timeline if isinstance(event, dict) and event.get("payload_ref")]


def _memory_trace_summaries(payload: dict[str, Any]) -> list[str]:
    traces = payload.get("memory_traces", [])
    return [trace.get("summary") for trace in traces if isinstance(trace, dict) and trace.get("summary")]


def _continuation_links_from_summaries(summaries: list[str]) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for summary in summaries:
        if not summary.startswith("Long handoff continuation linked "):
            continue
        fields = dict(part.split("=", 1) for part in summary.split() if "=" in part)
        links.append(
            {
                "source_task": fields.get("source_task", ""),
                "source_run": fields.get("source_run", ""),
                "continuation_task": fields.get("continuation_task", ""),
                "continuation_run": fields.get("continuation_run", ""),
                "resume_token": fields.get("resume_token", ""),
                "resume_minute": fields.get("resume_minute", ""),
            }
        )
    return links


def build_long_handoff_lineage(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    run_ids: set[str] = set()
    for payload in payloads:
        summary = summarize_persisted_result(payload)
        task = payload.get("task", {}) if isinstance(payload.get("task"), dict) else {}
        run = payload.get("run", {}) if isinstance(payload.get("run"), dict) else {}
        run_id = str(summary.get("run_id") or "")
        task_id = str(summary.get("task_id") or "")
        run_ids.add(run_id)
        artifacts = _artifact_paths(payload)
        payload_refs = _payload_refs(payload)
        resumed = "continuation-link.md" in payload_refs
        paused = "resume-token.txt" in payload_refs
        nodes.append(
            {
                "task_id": task_id,
                "run_id": run_id,
                "objective": task.get("objective"),
                "grade": summary["grade"],
                "score": summary["score"],
                "events": summary["events"],
                "artifacts": summary["artifacts"],
                "changed_files": summary["changed_files"],
                "paused": paused,
                "resumed": resumed,
                "artifact_paths": artifacts,
            }
        )
        for link in _continuation_links_from_summaries(_memory_trace_summaries(payload)):
            links.append(
                {
                    "source_task": link.get("source_task", ""),
                    "source_run": link.get("source_run", ""),
                    "continuation_task": link.get("continuation_task") or task_id,
                    "continuation_run": link.get("continuation_run") or run_id,
                    "resume_token": link.get("resume_token", ""),
                    "resume_minute": link.get("resume_minute", ""),
                }
            )
    source_runs = {link["source_run"] for link in links if link.get("source_run")}
    continuation_runs = {link["continuation_run"] for link in links if link.get("continuation_run")}
    missing_sources = sorted({run_id for run_id in source_runs if run_id not in run_ids})
    missing_continuations = sorted({run_id for run_id in continuation_runs if run_id not in run_ids})
    children_by_source: dict[str, list[str]] = {}
    for link in links:
        source_run = link.get("source_run", "")
        continuation_run = link.get("continuation_run", "")
        if not source_run or not continuation_run:
            continue
        children_by_source.setdefault(source_run, []).append(continuation_run)
    roots = sorted(run_id for run_id in run_ids if run_id not in continuation_runs)
    leaves = sorted(run_id for run_id in run_ids if run_id not in source_runs)
    branches = [
        {"source_run": source_run, "continuation_runs": sorted(set(continuation_runs))}
        for source_run, continuation_runs in sorted(children_by_source.items())
        if len(set(continuation_runs)) > 1
    ]

    def depth_from(run_id: str, seen: set[str] | None = None) -> int:
        active_seen = set(seen or set())
        if run_id in active_seen:
            return 0
        active_seen.add(run_id)
        children = children_by_source.get(run_id, [])
        if not children:
            return 1
        return 1 + max(depth_from(child, active_seen) for child in children)

    max_depth = max((depth_from(root) for root in roots), default=0)
    ready = bool(nodes) and all(node["grade"] == "ready" for node in nodes) and not missing_sources and not missing_continuations
    policy_reasons = _lineage_policy_reasons(ready, bool(branches), missing_sources, missing_continuations)
    return {
        "nodes": nodes,
        "links": links,
        "node_count": len(nodes),
        "link_count": len(links),
        "roots": roots,
        "leaves": leaves,
        "branches": branches,
        "branch_count": len(branches),
        "forked": bool(branches),
        "max_depth": max_depth,
        "missing_sources": missing_sources,
        "missing_continuations": missing_continuations,
        "complete": bool(links) and not missing_sources and not missing_continuations,
        "ready": ready,
        "autonomy_ready": ready and not branches,
        "policy_reasons": policy_reasons,
    }


def _lineage_policy_reasons(ready: bool, forked: bool, missing_sources: list[str], missing_continuations: list[str]) -> list[str]:
    reasons: list[str] = []
    if not ready:
        reasons.append("lineage_not_ready")
    if forked:
        reasons.append("lineage_forked")
    if missing_sources:
        reasons.append("missing_sources")
    if missing_continuations:
        reasons.append("missing_continuations")
    return reasons


def evaluate_long_handoff_continuation_policy(
    source_payload: dict[str, Any],
    lineage_payloads: list[dict[str, Any]],
    *,
    allow_fork: bool = False,
) -> dict[str, Any]:
    source_run = source_payload.get("run", {}) if isinstance(source_payload.get("run"), dict) else {}
    source_run_id = str(source_run.get("id") or "")
    lineage = build_long_handoff_lineage(lineage_payloads)
    existing_continuations = sorted(
        {
            link.get("continuation_run", "")
            for link in lineage.get("links", [])
            if isinstance(link, dict) and link.get("source_run") == source_run_id and link.get("continuation_run")
        }
    )
    reasons: list[str] = []
    if lineage_payloads and not lineage.get("autonomy_ready"):
        reasons.extend(str(reason) for reason in lineage.get("policy_reasons", []))
    if existing_continuations and not allow_fork:
        reasons.append("source_already_continued")
    return {
        "allowed": not reasons,
        "source_run": source_run_id,
        "existing_continuations": existing_continuations,
        "allow_fork": allow_fork,
        "reasons": reasons,
        "lineage": lineage,
    }


def evaluate_long_handoff_memory_policy(
    source_payload: dict[str, Any],
    memory_summaries: list[str],
    *,
    allow_fork: bool = False,
) -> dict[str, Any]:
    source_run = source_payload.get("run", {}) if isinstance(source_payload.get("run"), dict) else {}
    source_run_id = str(source_run.get("id") or "")
    links = _continuation_links_from_summaries(memory_summaries)
    existing_continuations = sorted(
        {
            link.get("continuation_run", "")
            for link in links
            if link.get("source_run") == source_run_id and link.get("continuation_run")
        }
    )
    reasons = ["source_already_continued"] if existing_continuations and not allow_fork else []
    return {
        "allowed": not reasons,
        "source_run": source_run_id,
        "existing_continuations": existing_continuations,
        "allow_fork": allow_fork,
        "reasons": reasons,
        "memory_links": links,
    }