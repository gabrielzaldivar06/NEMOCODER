from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from nemo_coding_platform.core.contracts import ExecutionPhase
from nemo_coding_platform.core.evals import score_headless_result
from nemo_coding_platform.core.headless_handoff import HandoffRequest
from nemo_coding_platform.core.headless_runner import HeadlessRunResult, execute_headless_handoff
from nemo_coding_platform.core.memory import MemoryAtom, MemoryAtomType
from nemo_coding_platform.core.memory_persistence import PersistentMemoryStore
from nemo_coding_platform.core.model_config import ModelProfile, default_model_profile
from nemo_coding_platform.core.nemo_adapter import InMemoryNemoAdapter, PersistentNemoAdapter
from nemo_coding_platform.core.nemo_lifecycle import NemoLifecyclePhase
from nemo_coding_platform.core.permission_engine import PermissionAction, load_ruleset_from_file
from nemo_coding_platform.core.persistence import load_headless_result_json, save_headless_result_json, summarize_persisted_result
from nemo_coding_platform.core.review_gate import MergeApplyResult, apply_merge_plan, build_merge_plan, rollback_apply_result
from nemo_coding_platform.core.self_discovery import ensure_self_mod_permissions_file, find_nemocode_repo
from nemo_coding_platform.core.skills import find_skill_by_name
from nemo_coding_platform.core.validation import validation_commands_for_policy


class SelfModTaskType(StrEnum):
    BUG_FIX = "bug_fix"
    TEST_COVERAGE = "test_coverage"
    REFACTOR = "refactor"
    TOOL_EXPANSION = "tool_expansion"
    DOCUMENTATION = "documentation"
    ARCHITECTURE_HARDENING = "architecture_hardening"


@dataclass(frozen=True, slots=True)
class SelfModRequest:
    description: str
    task_type: SelfModTaskType = SelfModTaskType.BUG_FIX
    target_files: tuple[str, ...] = ()
    validation_policy: str = "smoke"
    validation_commands: tuple[str, ...] = ()
    repair_budget: int = 2
    memory_db: str = ".nemo-runtimes/nemo-memory.sqlite"
    repo_root: str | None = None
    provider_mode: str = "subprocess"
    timeout_seconds: float = 30.0
    bounded_simulation: bool = False
    real_validation: bool = False
    output_dir: str = ".nemo-runtimes/self-mod/runs"


@dataclass(frozen=True, slots=True)
class SelfModRunResult:
    request: SelfModRequest
    repo_root: str
    permissions_file: str
    run_json: str
    context: dict[str, Any]
    result: HeadlessRunResult
    memory_writeback: dict[str, Any]
    trajectory_writeback: dict[str, Any] | None = None

    def to_summary_dict(self) -> dict[str, Any]:
        persisted_status = self_mod_status(self.run_json)
        return {
            "task_type": self.request.task_type.value,
            "repo_root": self.repo_root,
            "permissions_file": self.permissions_file,
            "run_json": self.run_json,
            "task_id": self.result.task.id,
            "run_id": self.result.run.id,
            "validation_passed": persisted_status["grade"] == "ready",
            "score": persisted_status["score"],
            "grade": persisted_status["grade"],
            "changed_files": list(self.result.effective_changed_files),
            "mergeable": persisted_status["mergeable"],
            "risk_flags": persisted_status["risk_flags"],
            "portfolio_id": self.context.get("portfolio", {}).get("portfolio_id"),
            "memory_writeback": self.memory_writeback,
            "trajectory_writeback": self.trajectory_writeback,
        }


@dataclass(frozen=True, slots=True)
class SelfModDecision:
    objective: str
    chosen_path: str
    rationale: str
    alternatives: tuple[str, ...] = ()
    task_type: str = ""
    run_json: str | None = None
    task_id: str | None = None
    run_id: str | None = None


@dataclass(frozen=True, slots=True)
class SelfModFeedback:
    run_json: str
    approved: bool
    issues_found: tuple[str, ...] = ()
    follow_up_work: tuple[str, ...] = ()
    portfolio_id: str | None = None
    atom_ids: tuple[str, ...] = ()
    evidence_handle: str | None = None
    token_delta: int = 0


def self_mod_run_json_path(output_dir: str | Path, task_id: str, run_id: str) -> Path:
    safe_task = _safe_name(task_id)
    safe_run = _safe_name(run_id)
    return Path(output_dir) / f"{safe_task}-{safe_run}.json"


def build_self_mod_handoff_request(request: SelfModRequest, repo_root: str | Path) -> HandoffRequest:
    target_note = ", ".join(request.target_files) if request.target_files else "agent-selected scoped files"
    validation_commands = request.validation_commands or validation_commands_for_policy(request.validation_policy, ())
    return HandoffRequest(
        prd=(
            f"[SELF-MOD:{request.task_type.value}] {request.description}\n\n"
            "Modify NEMOCODE itself through the self-modification workflow. "
            "Preserve public contracts unless the objective explicitly requires a contract change. "
            "Update or add tests for behavioral changes."
        ),
        repo_path=str(Path(repo_root)),
        acceptance_criteria=(
            f"Self-modification type is {request.task_type.value}.",
            f"Target scope: {target_note}.",
            "Changes remain inside the approved self-mod permission policy.",
            "Validation commands pass or failures are captured for review.",
            "Outcome is written back to NEMO memory.",
        ),
        validation_commands=validation_commands,
        repair_budget=request.repair_budget,
    )


def build_self_mod_context(adapter: PersistentNemoAdapter | InMemoryNemoAdapter, request: SelfModRequest) -> dict[str, Any]:
    topic = "NEMOCODE self-modification"
    _, prime = adapter.call(NemoLifecyclePhase.START, "prime_context", topic=topic, limit=8)
    _, portfolio = adapter.call(
        NemoLifecyclePhase.PLAN,
        "build_context_portfolio",
        task=request.description,
        topic=topic,
        portfolio_phase=ExecutionPhase.PLAN.value,
        token_budget=1200,
    )
    _, anticipate = adapter.call(NemoLifecyclePhase.PLAN, "anticipate", task=request.description, topic=topic, limit=8)
    continuity = get_self_mod_continuity(adapter.store.db_path, task_objective=request.description, task_type=request.task_type.value, limit=6) if isinstance(adapter, PersistentNemoAdapter) else {"items": [], "count": 0}
    risks = query_self_mod_risk_patterns(adapter.store.db_path, file_or_module=" ".join(request.target_files), limit=6) if isinstance(adapter, PersistentNemoAdapter) else {"patterns": [], "count": 0}
    return {"prime": prime.payload, "portfolio": portfolio.payload, "anticipate": anticipate.payload, "continuity": continuity, "risk_patterns": risks}


def record_self_mod_outcome(adapter: PersistentNemoAdapter | InMemoryNemoAdapter, run: HeadlessRunResult, request: SelfModRequest, context: dict[str, Any], run_json: str | Path | None = None) -> dict[str, Any]:
    status = self_mod_status(run_json) if run_json else None
    score = score_headless_result(run)
    validation_passed = bool(status.get("validation_passed")) if status else run.validation.passed
    grade = str(status.get("grade")) if status else score.grade
    risk_flags = tuple(status.get("risk_flags", ())) if status else ()
    _, result = adapter.call(
        NemoLifecyclePhase.REVIEW,
        "store_conversation",
        summary=(
            f"Self-modification outcome type={request.task_type.value} validation_passed={validation_passed} "
            f"grade={grade} changed_files={','.join(run.effective_changed_files)} risks={','.join(risk_flags)}"
        ),
        topic="NEMOCODE self-modification",
        tags=("self-modification", request.task_type.value, run.task.id, run.run.id),
        atom_type=MemoryAtomType.SESSION_SUMMARY.value,
        source_scope="self_modification",
        importance=8 if validation_passed else 9,
        evidence_handle=context.get("portfolio", {}).get("portfolio_id"),
    )
    return result.payload


def self_mod_impact(run_json: str | Path) -> dict[str, Any]:
    payload = load_headless_result_json(run_json)
    return build_self_mod_impact(payload)


def build_self_mod_impact(payload: dict[str, Any]) -> dict[str, Any]:
    summary = summarize_persisted_result(payload)
    changed_files = tuple(str(item).replace("\\", "/") for item in summary.get("changed_files", ()) or ())
    validation = payload.get("validation", {}) if isinstance(payload.get("validation"), dict) else {}
    validation_results = validation.get("results", []) if isinstance(validation, dict) else []
    validation_commands = tuple(_validation_command(item) for item in validation_results if isinstance(item, dict))
    validation_statuses = tuple(str(item.get("status", "unknown")) for item in validation_results if isinstance(item, dict))
    suggested_tests = _suggested_tests_for(changed_files)
    impacted_modules = tuple(_module_name(path) for path in changed_files if path.endswith(".py"))
    risk_flags = list(_impact_risks(changed_files, validation_statuses, suggested_tests))
    return {
        "changed_files": list(changed_files),
        "impacted_modules": [item for item in impacted_modules if item],
        "suggested_tests": list(suggested_tests),
        "validation_commands": list(validation_commands),
        "validation_statuses": list(validation_statuses),
        "risk_flags": risk_flags,
        "touches_source": any(path.startswith("src/") for path in changed_files),
        "touches_tests": any(path.startswith("tests/") for path in changed_files),
        "touches_ui": any(path.startswith("apps/mission-control/") for path in changed_files),
        "touches_mcp": any("mcp" in path for path in changed_files),
        "touches_cli": any(path.endswith("cli.py") for path in changed_files),
    }


def self_mod_trajectory(run_json: str | Path) -> dict[str, Any]:
    payload = load_headless_result_json(run_json)
    summary = summarize_persisted_result(payload)
    task = payload.get("task", {}) if isinstance(payload.get("task"), dict) else {}
    run = payload.get("run", {}) if isinstance(payload.get("run"), dict) else {}
    repair_plan = payload.get("repair_plan", {}) if isinstance(payload.get("repair_plan"), dict) else {}
    timeline = payload.get("timeline", []) if isinstance(payload.get("timeline"), list) else []
    artifacts = payload.get("artifacts", []) if isinstance(payload.get("artifacts"), list) else []
    review = self_mod_review(run_json)
    impact = build_self_mod_impact(payload)
    return {
        "schema_version": 1,
        "run_json": str(run_json),
        "task_id": summary.get("task_id"),
        "run_id": summary.get("run_id"),
        "objective": task.get("objective") or task.get("title") or "",
        "repo_path": task.get("repo_path") or review.get("merge_plan", {}).get("repo_path"),
        "sandbox_path": run.get("sandbox_path"),
        "grade": summary.get("grade"),
        "score": summary.get("score"),
        "validation_passed": summary.get("validation_passed"),
        "mergeable": review.get("mergeable"),
        "risk_flags": review.get("risk_flags", []),
        "changed_files": summary.get("changed_files", []),
        "impact": impact,
        "repair_attempts": list(repair_plan.get("attempts", [])) if isinstance(repair_plan.get("attempts", []), list) else [],
        "timeline": [
            {
                "sequence": item.get("sequence"),
                "phase": item.get("phase"),
                "kind": item.get("kind"),
                "summary": item.get("summary"),
                "payload_ref": item.get("payload_ref"),
            }
            for item in timeline
            if isinstance(item, dict)
        ],
        "artifacts": [
            {
                "type": item.get("artifact_type"),
                "path": item.get("path"),
                "summary": item.get("summary"),
            }
            for item in artifacts
            if isinstance(item, dict)
        ],
    }


def record_self_mod_trajectory(memory_db: str | Path, run_json: str | Path) -> dict[str, Any]:
    store = PersistentMemoryStore(Path(memory_db))
    trajectory = self_mod_trajectory(run_json)
    task_id = str(trajectory.get("task_id") or "unknown-task")
    run_id = str(trajectory.get("run_id") or "unknown-run")
    changed_files = tuple(str(item) for item in trajectory.get("changed_files", []) if item)
    risk_flags = tuple(str(item) for item in trajectory.get("risk_flags", []) if item)
    evidence_handle = store.create_evidence(
        json.dumps(trajectory, indent=2, sort_keys=True),
        f"Self-mod trajectory {task_id}/{run_id}: {len(changed_files)} changed file(s), grade={trajectory.get('grade')}",
        source_task_id=task_id,
        source_run_id=run_id,
    )
    summary_atom = store.create_atom(
        MemoryAtom(
            MemoryAtomType.SESSION_SUMMARY,
            f"Self-mod trajectory task={task_id} run={run_id} grade={trajectory.get('grade')} changed_files={','.join(changed_files) or 'none'} risks={','.join(risk_flags) or 'none'}",
            "self_modification",
            evidence_handle,
        ),
        topic="NEMOCODE self-modification",
        tags=("self-modification", "self-mod-trajectory", task_id, run_id),
        importance=9 if risk_flags else 8,
    )
    impact_atom = store.create_atom(
        MemoryAtom(
            MemoryAtomType.ARTIFACT_STATE,
            f"Self-mod impact task={task_id} run={run_id} suggested_tests={','.join(trajectory['impact'].get('suggested_tests', [])) or 'none'} risk_flags={','.join(trajectory['impact'].get('risk_flags', [])) or 'none'}",
            "self_modification",
            evidence_handle,
        ),
        topic="NEMOCODE self-modification",
        tags=("self-modification", "self-mod-impact", task_id, run_id),
        importance=8,
    )
    store.record_feedback(evidence_handle=evidence_handle, event_type="self_mod_trajectory_recorded", was_useful=True)
    return {"stored": True, "evidence_handle": evidence_handle, "memory_atom_ids": [summary_atom, impact_atom], "trajectory": trajectory}


def self_mod_similar_runs(memory_db: str | Path, query: str = "", *, limit: int = 6) -> dict[str, Any]:
    store = PersistentMemoryStore(Path(memory_db))
    atoms = store.search_atoms(topic="NEMOCODE self-modification", tags=("self-mod-trajectory",), limit=max(limit * 4, limit))
    query_terms = {term.lower() for term in query.replace(",", " ").split() if len(term) > 2}
    matches: list[dict[str, Any]] = []
    for atom in atoms:
        content = atom.atom.content.lower()
        overlap = len(query_terms & set(content.replace("=", " ").replace(",", " ").split())) if query_terms else 0
        if query_terms and overlap == 0:
            continue
        matches.append({"id": atom.id, "content": atom.atom.content, "tags": list(atom.tags), "importance": atom.importance, "evidence_handle": atom.atom.evidence_handle, "score": overlap or atom.importance})
    matches.sort(key=lambda item: (int(item["score"]), int(item["importance"])), reverse=True)
    return {"query": query, "runs": matches[:limit], "count": min(len(matches), limit)}


def record_self_mod_decision(memory_db: str | Path, decision: SelfModDecision | None = None, **kwargs: Any) -> dict[str, Any]:
    store = PersistentMemoryStore(Path(memory_db))
    item = decision or SelfModDecision(
        objective=str(kwargs.get("objective", "")),
        chosen_path=str(kwargs.get("chosen_path", "")),
        rationale=str(kwargs.get("rationale", "")),
        alternatives=_string_tuple(kwargs.get("alternatives", ())),
        task_type=str(kwargs.get("task_type", "")),
        run_json=_optional_string(kwargs.get("run_json")),
        task_id=_optional_string(kwargs.get("task_id")),
        run_id=_optional_string(kwargs.get("run_id")),
    )
    evidence_payload = {
        "objective": item.objective,
        "chosen_path": item.chosen_path,
        "rationale": item.rationale,
        "alternatives": list(item.alternatives),
        "task_type": item.task_type,
        "run_json": item.run_json,
        "task_id": item.task_id,
        "run_id": item.run_id,
    }
    handle = store.create_evidence(
        json.dumps(evidence_payload, indent=2, sort_keys=True),
        f"Self-mod decision: {item.chosen_path or item.objective}",
        source_task_id=item.task_id,
        source_run_id=item.run_id,
    )
    tags = tuple(dict.fromkeys(("self-modification", "self-mod-decision", item.task_type, item.task_id or "", item.run_id or "", *_string_tuple(kwargs.get("tags", ())))))
    content = f"Self-mod decision objective={item.objective} chosen_path={item.chosen_path} rationale={item.rationale} alternatives={','.join(item.alternatives) or 'none'}"
    atom_id = store.create_atom(
        MemoryAtom(MemoryAtomType.DECISION, content, "self_modification", handle),
        topic="NEMOCODE self-modification",
        tags=tuple(tag for tag in tags if tag),
        importance=int(kwargs.get("importance", 8)),
    )
    return {"stored": True, "decision_id": atom_id, "evidence_handle": handle, "decision": evidence_payload}


def record_self_mod_feedback(memory_db: str | Path, feedback: SelfModFeedback | None = None, **kwargs: Any) -> dict[str, Any]:
    store = PersistentMemoryStore(Path(memory_db))
    item = feedback or SelfModFeedback(
        run_json=str(kwargs.get("run_json", "")),
        approved=bool(kwargs.get("approved", kwargs.get("was_approved", False))),
        issues_found=_string_tuple(kwargs.get("issues_found", ())),
        follow_up_work=_string_tuple(kwargs.get("follow_up_work", ())),
        portfolio_id=_optional_string(kwargs.get("portfolio_id")),
        atom_ids=_string_tuple(kwargs.get("atom_ids", ())),
        evidence_handle=_optional_string(kwargs.get("evidence_handle")),
        token_delta=int(kwargs.get("token_delta", 0)),
    )
    evidence_handle = item.evidence_handle or item.portfolio_id
    feedback_ids: list[str] = []
    targets = item.atom_ids or (None,)
    for atom_id in targets:
        feedback_ids.append(
            store.record_feedback(
                atom_id=atom_id,
                evidence_handle=evidence_handle,
                event_type="self_mod_apply_approved" if item.approved else "self_mod_apply_rejected",
                was_useful=item.approved,
                token_delta=item.token_delta,
            )
        )
    status = "approved" if item.approved else "rejected"
    atom_id = store.create_atom(
        MemoryAtom(
            MemoryAtomType.SESSION_SUMMARY,
            f"Self-mod feedback status={status} run_json={item.run_json} issues={','.join(item.issues_found) or 'none'} follow_up={','.join(item.follow_up_work) or 'none'}",
            "self_modification",
            evidence_handle,
        ),
        topic="NEMOCODE self-modification",
        tags=("self-modification", "self-mod-feedback", status),
        importance=8 if item.approved else 9,
    )
    return {"stored": True, "feedback_ids": feedback_ids, "memory_atom_id": atom_id, "approved": item.approved}


def learn_from_self_mod_failure(memory_db: str | Path, run_json: str | Path, *, failure_pattern: str, suggested_correction: str, confidence: float = 0.7) -> dict[str, Any]:
    store = PersistentMemoryStore(Path(memory_db))
    trajectory = self_mod_trajectory(run_json)
    task_id = str(trajectory.get("task_id") or "unknown-task")
    run_id = str(trajectory.get("run_id") or "unknown-run")
    evidence_payload = {"trajectory": trajectory, "failure_pattern": failure_pattern, "suggested_correction": suggested_correction, "confidence": confidence}
    handle = store.create_evidence(
        json.dumps(evidence_payload, indent=2, sort_keys=True),
        f"Self-mod failure pattern {failure_pattern}: {suggested_correction}",
        source_task_id=task_id,
        source_run_id=run_id,
    )
    correction_id = store.create_atom(
        MemoryAtom(MemoryAtomType.CORRECTION, f"Self-mod failure pattern={failure_pattern}; correction={suggested_correction}; confidence={confidence:.2f}", "self_modification", handle),
        topic="NEMOCODE self-modification",
        tags=("self-modification", "self-mod-failure", "self-mod-correction", failure_pattern, task_id, run_id),
        importance=10,
    )
    risk_ids = _record_risk_pattern_atoms(store, trajectory, (failure_pattern,), suggested_correction, handle)
    return {"stored": True, "correction_id": correction_id, "risk_pattern_ids": risk_ids, "evidence_handle": handle}


def mark_portfolio_effective(memory_db: str | Path, *, portfolio_id: str, task_type: str = "", effectiveness_score: int = 8, context_savings: int = 0, run_json: str | Path | None = None) -> dict[str, Any]:
    store = PersistentMemoryStore(Path(memory_db))
    score = max(0, min(10, int(effectiveness_score)))
    feedback_id = store.record_feedback(evidence_handle=portfolio_id, event_type="self_mod_portfolio_effective", was_useful=score >= 6, token_delta=int(context_savings))
    atom_id = store.create_atom(
        MemoryAtom(MemoryAtomType.ARTIFACT_STATE, f"Self-mod portfolio effective portfolio_id={portfolio_id} task_type={task_type or 'unknown'} score={score} context_savings={context_savings}", "self_modification", portfolio_id),
        topic="NEMOCODE self-modification",
        tags=("self-modification", "self-mod-portfolio", "effective", task_type or "unknown"),
        importance=6 + min(4, score // 2),
    )
    return {"stored": True, "feedback_id": feedback_id, "memory_atom_id": atom_id, "portfolio_id": portfolio_id, "effectiveness_score": score, "run_json": str(run_json) if run_json else None}


def get_self_mod_continuity(memory_db: str | Path, *, task_objective: str = "", task_type: str = "", limit: int = 6, include_abandoned: bool = True) -> dict[str, Any]:
    store = PersistentMemoryStore(Path(memory_db))
    atoms = store.search_atoms(topic="NEMOCODE self-modification", limit=max(limit * 8, 40))
    query_terms = _term_set(f"{task_objective} {task_type}")
    items: list[dict[str, Any]] = []
    for atom in atoms:
        tags = set(atom.tags)
        if not include_abandoned and "abandoned" in tags:
            continue
        if not ({"self-mod-trajectory", "self-mod-decision", "self-mod-feedback", "self-mod-correction", "self-mod-risk"} & tags):
            continue
        score = len(query_terms & _term_set(atom.atom.content + " " + " ".join(atom.tags))) if query_terms else atom.importance
        if query_terms and score == 0:
            continue
        items.append({**_stored_atom_payload(atom), "score": score})
    items.sort(key=lambda item: (int(item["score"]), int(item["importance"]), str(item["created_at"])), reverse=True)
    return {"query": task_objective, "task_type": task_type, "items": items[:limit], "count": min(len(items), limit)}


def query_self_mod_risk_patterns(memory_db: str | Path, *, file_or_module: str = "", risk_category: str = "", limit: int = 10) -> dict[str, Any]:
    store = PersistentMemoryStore(Path(memory_db))
    atoms = store.search_atoms(topic="NEMOCODE self-modification", tags=("self-mod-risk",), limit=max(limit * 4, limit))
    query_terms = _term_set(f"{file_or_module} {risk_category}")
    patterns: list[dict[str, Any]] = []
    for atom in atoms:
        haystack = atom.atom.content + " " + " ".join(atom.tags)
        score = len(query_terms & _term_set(haystack)) if query_terms else atom.importance
        if query_terms and score == 0:
            continue
        patterns.append({**_stored_atom_payload(atom), "score": score})
    patterns.sort(key=lambda item: (int(item["score"]), int(item["importance"])), reverse=True)
    return {"patterns": patterns[:limit], "count": min(len(patterns), limit), "query": file_or_module, "risk_category": risk_category}


def execute_self_modification(
    request: SelfModRequest,
    *,
    task_id: str = "self-mod-task",
    run_id: str = "self-mod-run",
    model_profile: ModelProfile | None = None,
    engine_command: tuple[str, ...] | None = None,
) -> SelfModRunResult:
    repo_root = find_nemocode_repo(request.repo_root)
    permissions_file = ensure_self_mod_permissions_file(repo_root)
    adapter = PersistentNemoAdapter(PersistentMemoryStore(Path(request.memory_db)))
    context = build_self_mod_context(adapter, request)
    decision_writeback = record_self_mod_decision(
        request.memory_db,
        objective=request.description,
        chosen_path=f"{request.task_type.value}:{','.join(request.target_files) or 'agent-selected'}",
        rationale="Selected self-modification scope and validation policy before sandbox mutation.",
        alternatives=("defer", "manual-only-review"),
        task_type=request.task_type.value,
        task_id=task_id,
        run_id=run_id,
    )
    handoff_request = build_self_mod_handoff_request(request, repo_root)
    skill_prompt = _self_mod_skill_prompt(repo_root)
    validation_cwd = "repo" if any(str(path).replace("\\", "/").startswith("apps/") for path in request.target_files) else "runtime"
    result = execute_headless_handoff(
        handoff_request,
        task_id=task_id,
        run_id=run_id,
        real_validation=request.real_validation,
        validation_cwd=validation_cwd,
        nemo_adapter=adapter,
        provider_mode=request.provider_mode,
        model_profile=model_profile or default_model_profile(),
        engine_command=engine_command,
        timeout_seconds=request.timeout_seconds,
        target_files=request.target_files,
        validation_policy=request.validation_policy,
        bounded_simulation=request.bounded_simulation,
        skill_prompt=skill_prompt,
        permissions_file=str(permissions_file),
    )
    run_json = save_headless_result_json(result, self_mod_run_json_path(Path(repo_root) / request.output_dir, task_id, run_id))
    trajectory_writeback = record_self_mod_trajectory(request.memory_db, run_json)
    memory_writeback = record_self_mod_outcome(adapter, result, request, context, run_json)
    if not self_mod_status(run_json)["validation_passed"] or self_mod_status(run_json)["risk_flags"]:
        learn_from_self_mod_failure(
            request.memory_db,
            run_json,
            failure_pattern=";".join(self_mod_status(run_json)["risk_flags"] or ["validation_not_ready"]),
            suggested_correction="Retrieve this trajectory before similar self-modification work and tighten scope or validation.",
        )
    context["decision_writeback"] = decision_writeback
    return SelfModRunResult(request, str(repo_root), str(permissions_file), str(run_json), context, result, memory_writeback, trajectory_writeback)


def self_mod_status(run_json: str | Path) -> dict[str, Any]:
    payload = load_headless_result_json(run_json)
    summary = summarize_persisted_result(payload)
    score = score_headless_result(payload)
    review = self_mod_review(run_json)
    return {
        **summary,
        "validation_passed": score.validation_passed,
        "run_json": str(run_json),
        "review_ready": bool(review["merge_plan"]["files"]),
        "mergeable": review["mergeable"],
        "risk_flags": review["risk_flags"],
    }


def self_mod_review(run_json: str | Path, permissions_file: str | Path | None = None) -> dict[str, Any]:
    payload = load_headless_result_json(run_json)
    plan = build_merge_plan(payload)
    policy_file = Path(permissions_file) if permissions_file else Path(plan.repo_path) / ".nemocode-self-mod.permissions.json"
    self_mod_risks = self_mod_risk_flags(payload, policy_file)
    risk_flags = tuple(dict.fromkeys((*plan.risk_flags, *self_mod_risks)))
    mergeable = plan.mergeable and not _blocking_self_mod_risks(risk_flags)
    return {
        "run_json": str(run_json),
        "permissions_file": str(policy_file),
        "merge_plan": plan.to_dict(),
        "self_mod_risk_flags": list(self_mod_risks),
        "risk_flags": list(risk_flags),
        "mergeable": mergeable,
    }


def self_mod_apply(run_json: str | Path, *, approve_review: bool = False, backup_dir: str | None = None, permissions_file: str | Path | None = None, autonomy_profile: str = "manual", memory_db: str | Path | None = None, portfolio_id: str | None = None) -> dict[str, Any]:
    review = self_mod_review(run_json, permissions_file)
    if not review["mergeable"]:
        raise PermissionError(f"self-mod review is not mergeable: {', '.join(review['risk_flags'])}")
    plan = build_merge_plan(load_headless_result_json(run_json))
    applied = apply_merge_plan(plan, approve_review=approve_review, backup_dir=backup_dir, autonomy_profile=autonomy_profile)
    payload = applied.to_dict()
    if memory_db is not None:
        feedback = record_self_mod_feedback(memory_db, run_json=str(run_json), approved=True, portfolio_id=portfolio_id, evidence_handle=portfolio_id)
        payload["learning_feedback"] = feedback
        if portfolio_id:
            payload["portfolio_effectiveness"] = mark_portfolio_effective(memory_db, portfolio_id=portfolio_id, task_type="self-modification", effectiveness_score=9, run_json=run_json)
    return payload


def self_mod_rollback(apply_json: str | Path, *, approve_review: bool = False) -> dict[str, Any]:
    import json

    applied = MergeApplyResult.from_dict(json.loads(Path(apply_json).read_text(encoding="utf-8")))
    rollback = rollback_apply_result(applied, approve_review=approve_review)
    return rollback.to_dict()


def self_mod_risk_flags(payload: dict[str, Any], permissions_file: str | Path) -> tuple[str, ...]:
    flags: list[str] = []
    validation = payload.get("validation", {}) if isinstance(payload.get("validation"), dict) else {}
    results = validation.get("results", []) if isinstance(validation, dict) else []
    if any(isinstance(item, dict) and str(item.get("status")) == "skipped" for item in results):
        flags.append("validation_skipped")
    ruleset = load_ruleset_from_file(permissions_file)
    changed_files = tuple(str(item) for item in summarize_persisted_result(payload).get("changed_files", ()) or ())
    for changed_file in changed_files:
        normalized = changed_file.replace("\\", "/")
        if ruleset.evaluate("write_file", normalized) != PermissionAction.ALLOW:
            flags.append(f"permission_policy_warn_path:{normalized}")
        parts = set(Path(normalized).parts)
        if parts & {".git", ".venv", ".nemo-runtimes", "__pycache__"}:
            flags.append(f"protected_path_touched:{normalized}")
        if normalized.endswith(('.db', '.sqlite', '.sqlite-wal', '.sqlite-shm')) or normalized.startswith(".env"):
            flags.append(f"protected_path_touched:{normalized}")
    if len(changed_files) > 8:
        flags.append("too_many_changed_files")
    if any(path.startswith("tests/") and ("delete" in path.lower() or "removed" in path.lower()) for path in changed_files):
        flags.append("tests_removed")
    return tuple(dict.fromkeys(flags))


def _self_mod_skill_prompt(repo_root: Path) -> str:
    skill = find_skill_by_name("self-modification", repo_root / "skills")
    if skill is None:
        return ""
    return skill.prompt


def _safe_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in ("-", "_") else "-" for char in value) or "run"


def _blocking_self_mod_risks(risk_flags: tuple[str, ...]) -> bool:
    blocking_prefixes = ("protected_path_touched:",)
    blocking_values = {"tests_removed"}
    return any(flag in blocking_values or flag.startswith(blocking_prefixes) for flag in risk_flags)


def _validation_command(item: dict[str, Any]) -> str:
    command = item.get("command")
    if isinstance(command, dict):
        return str(command.get("command", ""))
    return str(command or "")


def _module_name(path: str) -> str:
    if not path.endswith(".py"):
        return ""
    if path.startswith("src/"):
        return path.removeprefix("src/").removesuffix(".py").replace("/", ".")
    return path.removesuffix(".py").replace("/", ".")


def _suggested_tests_for(changed_files: tuple[str, ...]) -> tuple[str, ...]:
    suggestions: list[str] = []
    for path in changed_files:
        if path.startswith("src/nemo_coding_platform/core/") and path.endswith(".py"):
            stem = Path(path).stem
            suggestions.append(f"tests/test_{stem}.py")
        if path == "src/nemo_coding_platform/cli.py":
            suggestions.append("tests/test_cli_headless.py")
        if path == "src/nemo_coding_platform/mcp_server.py" or path.endswith("nemocode_mcp_tools.py"):
            suggestions.append("tests/test_mcp_server.py")
        if path == "src/nemo_coding_platform/mission_control_server.py":
            suggestions.append("tests/test_mission_control_server.py")
        if path.startswith("apps/mission-control/"):
            suggestions.append("npm run build")
    return tuple(dict.fromkeys(suggestions))


def _impact_risks(changed_files: tuple[str, ...], validation_statuses: tuple[str, ...], suggested_tests: tuple[str, ...]) -> tuple[str, ...]:
    risks: list[str] = []
    if not changed_files:
        risks.append("impact:no_changed_files")
    if "failed" in validation_statuses:
        risks.append("impact:validation_failed")
    if "skipped" in validation_statuses:
        risks.append("impact:validation_skipped")
    if any(path.startswith("src/") for path in changed_files) and not any(path.startswith("tests/") for path in changed_files):
        risks.append("impact:source_without_test_change")
    if any(path.endswith(("cli.py", "mcp_server.py", "nemocode_mcp_tools.py")) for path in changed_files):
        risks.append("impact:public_surface_change")
    if any(path.startswith("apps/mission-control/") for path in changed_files) and "npm run build" in suggested_tests:
        risks.append("impact:ui_build_required")
    return tuple(dict.fromkeys(risks))


def _record_risk_pattern_atoms(store: PersistentMemoryStore, trajectory: dict[str, Any], risk_flags: tuple[str, ...], mitigation: str, evidence_handle: str) -> list[str]:
    changed_files = tuple(str(item) for item in trajectory.get("changed_files", []) if item)
    task_id = str(trajectory.get("task_id") or "unknown-task")
    run_id = str(trajectory.get("run_id") or "unknown-run")
    atom_ids: list[str] = []
    for risk in risk_flags:
        for path in changed_files or ("unknown",):
            atom_ids.append(
                store.create_atom(
                    MemoryAtom(MemoryAtomType.ARTIFACT_STATE, f"Self-mod risk pattern risk={risk} path={path} mitigation={mitigation}", "self_modification", evidence_handle),
                    topic="NEMOCODE self-modification",
                    tags=("self-modification", "self-mod-risk", risk, path, task_id, run_id),
                    importance=9,
                )
            )
    return atom_ids


def _stored_atom_payload(atom: Any) -> dict[str, Any]:
    return {
        "id": atom.id,
        "type": atom.atom.atom_type.value,
        "content": atom.atom.content,
        "topic": atom.topic,
        "tags": list(atom.tags),
        "importance": atom.importance,
        "evidence_handle": atom.atom.evidence_handle,
        "created_at": atom.created_at,
    }


def _term_set(value: str) -> set[str]:
    return {term.lower().strip(".,:;[](){}") for term in value.replace("/", " ").replace("_", " ").replace("-", " ").split() if len(term.strip(".,:;[](){}")) > 2}


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value else ()
    return tuple(str(item) for item in value if str(item))


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None