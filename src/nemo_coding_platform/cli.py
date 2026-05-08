from __future__ import annotations

import argparse
import json
import logging
import shlex
import time
from pathlib import Path

from nemo_coding_platform.core.architecture import default_blueprint
from nemo_coding_platform.core.contracts import ExecutionPhase
from nemo_coding_platform.core.evals import score_headless_result
from nemo_coding_platform.core.headless_handoff import HandoffRequest, build_handoff_plan
from nemo_coding_platform.core.headless_runner import execute_headless_handoff, HeadlessRunResult
from nemo_coding_platform.core.long_handoff_supervisor import LongHandoffBudget, build_long_handoff_resume_plan, execute_long_handoff_continuation, execute_long_handoff_supervisor
from nemo_coding_platform.core.memory import MemoryAtomType, nemo_tools_for_phase
from nemo_coding_platform.core.mission_control import build_mission_control_state
from nemo_coding_platform.core.memory_persistence import PersistentMemoryStore
from nemo_coding_platform.core.model_config import ModelProfile, default_model_profile
from nemo_coding_platform.core.mutations import FileWrite, MutationPlan, QualityMutationEngine
from nemo_coding_platform.core.nemo_adapter import InMemoryNemoAdapter, NemoCallResult, PersistentNemoAdapter, McpNemoAdapter
from nemo_coding_platform.core.nemo_lifecycle import NemoLifecyclePhase
from nemo_coding_platform.core.orchestrator import DEFAULT_WORKFLOW, ReviewDecision, SupervisedWorkflowRunner
from nemo_coding_platform.core.persistence import build_long_handoff_lineage, build_replay_summary, evaluate_long_handoff_continuation_policy, evaluate_long_handoff_memory_policy, export_run_metrics, load_headless_result_json, save_headless_result_json, summarize_persisted_result
from nemo_coding_platform.core.review_gate import MergeApplyResult, apply_merge_plan, build_merge_plan, rollback_apply_result
from nemo_coding_platform.core.self_modification import SelfModRequest, SelfModTaskType, execute_self_modification, self_mod_apply, self_mod_impact, self_mod_review, self_mod_rollback, self_mod_similar_runs, self_mod_status, self_mod_trajectory
from nemo_coding_platform.core.skills import find_skill_by_name
from nemo_coding_platform.core.task_run import EventKind
from nemo_coding_platform.core.watch_mode import FileWatcher, WatchRequest
from nemo_coding_platform.core.validation import validation_commands_for_policy
from nemo_coding_platform.core.workspace import Workspace


DEFAULT_MEMORY_DB = ".nemo-runtimes/nemo-memory.sqlite"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nemo-platform")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("blueprint", help="Print the current architecture blueprint")
    subparsers.add_parser("workflow", help="Print the supervised workflow phases")
    tools = subparsers.add_parser("nemo-tools", help="Print NEMO tool access by workflow phase")
    tools.add_argument("--phase", choices=[phase.value for phase in ExecutionPhase])
    dry_run = subparsers.add_parser("dry-run-write", help="Dry-run a supervised file write")
    dry_run.add_argument("path")
    dry_run.add_argument("content")
    dry_run.add_argument("--workspace", default=".")
    apply_write = subparsers.add_parser("apply-write", help="Apply a supervised file write after explicit gates")
    apply_write.add_argument("path")
    apply_write.add_argument("content")
    apply_write.add_argument("--workspace", default=".")
    apply_write.add_argument("--approve-execution", action="store_true")
    apply_write.add_argument("--approve-review", action="store_true")
    apply_write.add_argument("--review-summary", default="Approved from CLI")
    handoff_plan = subparsers.add_parser("handoff-plan", help="Print a deterministic Full Handoff plan")
    handoff_plan.add_argument("objective")
    handoff_plan.add_argument("--repo", default=".")
    handoff_plan.add_argument("--acceptance", action="append")
    handoff_plan.add_argument("--validation", action="append")
    handoff_plan.add_argument("--json", action="store_true")
    headless_run = subparsers.add_parser("headless-run", help="Run a deterministic Headless Full Handoff simulation")
    headless_run.add_argument("objective")
    headless_run.add_argument("--repo", default=".")
    headless_run.add_argument("--acceptance", action="append")
    headless_run.add_argument("--validation", action="append")
    headless_run.add_argument("--validation-policy", choices=("none", "smoke", "targeted", "full"), default="smoke")
    headless_run.add_argument("--validation-python", action="append", default=[])
    headless_run.add_argument("--fail-validation", action="append", default=[])
    headless_run.add_argument("--real-validation", action="store_true")
    headless_run.add_argument("--validation-cwd", default="runtime")
    headless_run.add_argument("--save-json")
    headless_run.add_argument("--provider", choices=("fake", "subprocess"), default="subprocess")
    headless_run.add_argument("--engine-command", "--aider-command", dest="engine_command")
    headless_run.add_argument("--target-file", action="append", default=[])
    headless_run.add_argument("--model-profile", default=default_model_profile().model)
    headless_run.add_argument("--lmstudio-base-url", default=default_model_profile().base_url)
    headless_run.add_argument("--timeout", type=float, default=30.0)
    headless_run.add_argument("--bounded-simulation", action="store_true")
    headless_run.add_argument("--memory-db", default=DEFAULT_MEMORY_DB, help="SQLite-backed NEMO memory store")
    headless_run.add_argument("--no-memory-db", action="store_true", help="Use the lightweight in-memory NEMO adapter instead")
    headless_run.add_argument("--mcp-url", help="Connect to a remote MCP server via SSE (e.g. http://localhost:8765/mcp/sse)")
    headless_run.add_argument("--mcp-prefix", default="", help="Prefix for MCP tool names (e.g. 'nemo.')")
    headless_run.add_argument("--skill", default=None, help="Name or slug of a skill from the skills/ directory to inject into the NEMO CODE engine prompt")
    headless_run.add_argument("--skills-root", default="skills", help="Root directory for skills (default: ./skills)")
    headless_run.add_argument("--permissions-file", help="Path to .nemocode-permissions.json")
    headless_run.add_argument("--image", help="Path to a design reference image (Vision)")
    headless_run.add_argument("--json", action="store_true")
    self_modify = subparsers.add_parser("self-modify", help="Run a controlled self-modification task against NEMOCODE itself")
    self_modify.add_argument("description")
    self_modify.add_argument("--type", choices=[item.value for item in SelfModTaskType], default=SelfModTaskType.BUG_FIX.value)
    self_modify.add_argument("--repo", help="NEMOCODE repo root; auto-discovered when omitted")
    self_modify.add_argument("--target-file", action="append", default=[])
    self_modify.add_argument("--validation", action="append")
    self_modify.add_argument("--validation-policy", choices=("none", "smoke", "targeted", "full"), default="smoke")
    self_modify.add_argument("--repair-budget", type=int, default=2)
    self_modify.add_argument("--provider", choices=("fake", "subprocess"), default="subprocess")
    self_modify.add_argument("--engine-command", "--aider-command", dest="engine_command")
    self_modify.add_argument("--model-profile", default=default_model_profile().model)
    self_modify.add_argument("--lmstudio-base-url", default=default_model_profile().base_url)
    self_modify.add_argument("--timeout", type=float, default=30.0)
    self_modify.add_argument("--bounded-simulation", action="store_true")
    self_modify.add_argument("--real-validation", action="store_true")
    self_modify.add_argument("--memory-db", default=DEFAULT_MEMORY_DB)
    self_modify.add_argument("--task-id", default="self-mod-task")
    self_modify.add_argument("--run-id", default="self-mod-run")
    self_modify.add_argument("--json", action="store_true")
    self_mod_status_cmd = subparsers.add_parser("self-mod-status", help="Summarize a persisted self-modification run")
    self_mod_status_cmd.add_argument("run_json")
    self_mod_status_cmd.add_argument("--json", action="store_true")
    self_mod_review_cmd = subparsers.add_parser("self-mod-review", help="Build a self-modification review plan and risk report")
    self_mod_review_cmd.add_argument("run_json")
    self_mod_review_cmd.add_argument("--permissions-file")
    self_mod_review_cmd.add_argument("--json", action="store_true")
    self_mod_apply_cmd = subparsers.add_parser("self-mod-apply", help="Apply a reviewed self-modification run after explicit approval")
    self_mod_apply_cmd.add_argument("run_json")
    self_mod_apply_cmd.add_argument("--approve-review", action="store_true")
    self_mod_apply_cmd.add_argument("--permissions-file")
    self_mod_apply_cmd.add_argument("--backup-dir")
    self_mod_apply_cmd.add_argument("--autonomy-profile", choices=("manual", "trusted", "aggressive"), default="manual")
    self_mod_apply_cmd.add_argument("--save-apply-json")
    self_mod_apply_cmd.add_argument("--json", action="store_true")
    self_mod_rollback_cmd = subparsers.add_parser("self-mod-rollback", help="Rollback a self-modification apply JSON after explicit approval")
    self_mod_rollback_cmd.add_argument("apply_json")
    self_mod_rollback_cmd.add_argument("--approve-review", action="store_true")
    self_mod_rollback_cmd.add_argument("--json", action="store_true")
    self_mod_trajectory_cmd = subparsers.add_parser("self-mod-trajectory", help="Build a replayable self-improvement trajectory from a self-modification run")
    self_mod_trajectory_cmd.add_argument("run_json")
    self_mod_trajectory_cmd.add_argument("--json", action="store_true")
    self_mod_impact_cmd = subparsers.add_parser("self-mod-impact", help="Analyze changed surfaces and validation needs for a self-modification run")
    self_mod_impact_cmd.add_argument("run_json")
    self_mod_impact_cmd.add_argument("--json", action="store_true")
    self_mod_similar_cmd = subparsers.add_parser("self-mod-similar-runs", help="Retrieve similar self-modification runs from NEMO memory")
    self_mod_similar_cmd.add_argument("query")
    self_mod_similar_cmd.add_argument("--memory-db", default=DEFAULT_MEMORY_DB)
    self_mod_similar_cmd.add_argument("--limit", type=int, default=6)
    self_mod_similar_cmd.add_argument("--json", action="store_true")
    long_run = subparsers.add_parser("long-handoff-run", help="Run a supervised long Full Handoff slice with budgets and heartbeats")
    long_run.add_argument("objective")
    long_run.add_argument("--repo", default=".")
    long_run.add_argument("--task-id", default="task-1")
    long_run.add_argument("--run-id", default="run-1")
    long_run.add_argument("--acceptance", action="append")
    long_run.add_argument("--validation", action="append")
    long_run.add_argument("--validation-policy", choices=("none", "smoke", "targeted", "full"), default="smoke")
    long_run.add_argument("--validation-python", action="append", default=[])
    long_run.add_argument("--real-validation", action="store_true")
    long_run.add_argument("--validation-cwd", default="runtime")
    long_run.add_argument("--save-json")
    long_run.add_argument("--provider", choices=("fake", "subprocess"), default="subprocess")
    long_run.add_argument("--engine-command", "--aider-command", dest="engine_command")
    long_run.add_argument("--target-file", action="append", default=[])
    long_run.add_argument("--model-profile", default=default_model_profile().model)
    long_run.add_argument("--lmstudio-base-url", default=default_model_profile().base_url)
    long_run.add_argument("--timeout", type=float, default=30.0)
    long_run.add_argument("--max-runtime-minutes", type=int, default=120)
    long_run.add_argument("--heartbeat-minutes", type=int, default=15)
    long_run.add_argument("--max-heartbeats", type=int, default=4)
    long_run.add_argument("--token-budget", type=int, default=32000)
    long_run.add_argument("--pause-after-minutes", type=int)
    long_run.add_argument("--plan-minutes", type=int, default=30)
    long_run.add_argument("--execute-minutes", type=int, default=60)
    long_run.add_argument("--review-minutes", type=int, default=30)
    long_run.add_argument("--repair-time-limit-seconds", type=float)
    long_run.add_argument("--validation-time-budget-seconds", type=float)
    long_run.add_argument("--validation-escalation-mode", action="store_true")
    long_run.add_argument("--memory-db", default=DEFAULT_MEMORY_DB, help="SQLite-backed NEMO memory store")
    long_run.add_argument("--no-memory-db", action="store_true", help="Use the lightweight in-memory NEMO adapter instead")
    long_run.add_argument("--mcp-url", help="Connect to a remote MCP server via SSE (e.g. http://localhost:8765/mcp/sse)")
    long_run.add_argument("--mcp-prefix", default="", help="Prefix for MCP tool names (e.g. 'nemo.')")
    long_run.add_argument("--skill", default=None, help="Name or slug of a skill from the skills/ directory to inject into the NEMO CODE engine prompt")
    long_run.add_argument("--skills-root", default="skills", help="Root directory for skills (default: ./skills)")
    long_run.add_argument("--permissions-file", help="Path to .nemocode-permissions.json")
    long_run.add_argument("--image", help="Path to a design reference image (Vision)")
    long_run.add_argument("--prd-text", help="Optional full PRD/spec text persisted separately from the objective summary")
    long_run.add_argument("--spec-mode", choices=("auto", "sdd"), default="auto")
    long_run.add_argument("--json", action="store_true")

    watch = subparsers.add_parser("watch", help="Watch for # ai! comments in the repository and trigger handoffs")
    watch.add_argument("--repo", default=".", help="Repository root to watch")
    watch.add_argument("--provider", default="subprocess", choices=["fake", "subprocess"])
    watch.add_argument("--model-profile", default="nvidia.agentic.coder-4b")
    watch.add_argument("--lmstudio-base-url", default="http://localhost:1234/v1")
    show_run = subparsers.add_parser("show-run-json", help="Summarize a persisted headless run JSON file")
    show_run.add_argument("path")
    show_run.add_argument("--json", action="store_true")
    eval_run = subparsers.add_parser("eval-run-json", help="Evaluate readiness for a persisted headless run JSON file")
    eval_run.add_argument("path")
    eval_run.add_argument("--json", action="store_true")
    replay_run = subparsers.add_parser("replay-run-json", help="Build a replay summary for a persisted headless run JSON file")
    replay_run.add_argument("path")
    replay_run.add_argument("--json", action="store_true")
    metrics_run = subparsers.add_parser("metrics-run-json", help="Export FR10 metrics for a persisted headless run JSON file")
    metrics_run.add_argument("path")
    metrics_run.add_argument("--json", action="store_true")
    review_run = subparsers.add_parser("review-run-json", help="Build a safe review-to-main merge plan from a persisted run JSON file")
    review_run.add_argument("path")
    review_run.add_argument("--save-plan", help="Write merge-plan.md to this path")
    review_run.add_argument("--json", action="store_true")
    mission_control = subparsers.add_parser("mission-control-state", help="Export Desktop Mission Control state from persisted runs")
    mission_control.add_argument("--repo", default=".")
    mission_control.add_argument("--runtimes", default=".nemo-runtimes")
    mission_control.add_argument("--save-json", help="Write Mission Control state JSON to this path")
    mission_control.add_argument("--json", action="store_true")
    mission_server = subparsers.add_parser("mission-control-server", help="Run the local Desktop Mission Control API bridge")
    mission_server.add_argument("--repo", default=".")
    mission_server.add_argument("--runtimes", default=".nemo-runtimes")
    mission_server.add_argument("--host", default="127.0.0.1")
    mission_server.add_argument("--port", type=int, default=8787)
    mission_server.add_argument("--apply-results", default=".nemo-runtimes/mission-control/apply-results")
    mission_server.add_argument("--memory-db", default=DEFAULT_MEMORY_DB)
    mission_server.add_argument("--no-memory-db", action="store_true")
    apply_run = subparsers.add_parser("apply-run-json", help="Apply a ready run from sandbox to repo after explicit review approval")
    apply_run.add_argument("path")
    apply_run.add_argument("--approve-review", action="store_true")
    apply_run.add_argument("--save-plan", help="Write merge-plan.md to this path before applying")
    apply_run.add_argument("--save-apply-report", help="Write apply-report.md to this path after applying")
    apply_run.add_argument("--save-apply-json", help="Write structured apply result JSON for rollback")
    apply_run.add_argument("--backup-dir", help="Directory for update backups before applying")
    apply_run.add_argument("--autonomy-profile", choices=("manual", "trusted", "aggressive"), default="manual", help="Allow safe auto-apply without --approve-review when profile permits it")
    apply_run.add_argument("--memory-db", default=DEFAULT_MEMORY_DB, help="SQLite-backed NEMO memory store for apply writeback")
    apply_run.add_argument("--no-memory-db", action="store_true", help="Skip NEMO apply writeback")
    apply_run.add_argument("--json", action="store_true")
    rollback_apply = subparsers.add_parser("rollback-apply-json", help="Rollback a structured apply result JSON after explicit review approval")
    rollback_apply.add_argument("path")
    rollback_apply.add_argument("--approve-review", action="store_true")
    rollback_apply.add_argument("--save-rollback-report", help="Write rollback-report.md to this path after rollback")
    rollback_apply.add_argument("--json", action="store_true")
    
    mcp_server = subparsers.add_parser("mcp-server", help="Run the real MCP server (SSE protocol) for integration with LLM clients")
    mcp_server.add_argument("--host", default="127.0.0.1")
    mcp_server.add_argument("--port", type=int, default=8765)
    resume_long = subparsers.add_parser("long-handoff-resume", help="Build a resume plan from a paused long handoff JSON file")
    resume_long.add_argument("path")
    resume_long.add_argument("--save-json")
    resume_long.add_argument("--json", action="store_true")
    continue_long = subparsers.add_parser("long-handoff-continue", help="Execute a new long handoff run from a paused resume token")
    continue_long.add_argument("path")
    continue_long.add_argument("--objective")
    continue_long.add_argument("--acceptance", action="append")
    continue_long.add_argument("--validation", action="append")
    continue_long.add_argument("--validation-policy", choices=("none", "smoke", "targeted", "full"), default="smoke")
    continue_long.add_argument("--validation-python", action="append", default=[])
    continue_long.add_argument("--real-validation", action="store_true")
    continue_long.add_argument("--validation-cwd", default="runtime")
    continue_long.add_argument("--save-json")
    continue_long.add_argument("--provider", choices=("fake", "subprocess"), default="subprocess")
    continue_long.add_argument("--engine-command", "--aider-command", dest="engine_command")
    continue_long.add_argument("--target-file", action="append", default=[])
    continue_long.add_argument("--model-profile", default=default_model_profile().model)
    continue_long.add_argument("--lmstudio-base-url", default=default_model_profile().base_url)
    continue_long.add_argument("--timeout", type=float, default=30.0)
    continue_long.add_argument("--max-runtime-minutes", type=int, default=30)
    continue_long.add_argument("--heartbeat-minutes", type=int, default=10)
    continue_long.add_argument("--max-heartbeats", type=int, default=1)
    continue_long.add_argument("--token-budget", type=int, default=8000)
    continue_long.add_argument("--pause-after-minutes", type=int)
    continue_long.add_argument("--plan-minutes", type=int, default=30)
    continue_long.add_argument("--execute-minutes", type=int, default=60)
    continue_long.add_argument("--review-minutes", type=int, default=30)
    continue_long.add_argument("--repair-time-limit-seconds", type=float)
    continue_long.add_argument("--validation-time-budget-seconds", type=float)
    continue_long.add_argument("--validation-escalation-mode", action="store_true")
    continue_long.add_argument("--memory-db", default=DEFAULT_MEMORY_DB, help="SQLite-backed NEMO memory store for this continuation")
    continue_long.add_argument("--no-memory-db", action="store_true", help="Use the lightweight in-memory NEMO adapter instead")
    continue_long.add_argument("--mcp-url", help="Connect to a remote MCP server via SSE (e.g. http://localhost:8765/mcp/sse)")
    continue_long.add_argument("--mcp-prefix", default="", help="Prefix for MCP tool names (e.g. 'nemo.')")
    continue_long.add_argument("--lineage-context", action="append", default=[], help="Existing run JSON to use as autonomy lineage context")
    continue_long.add_argument("--allow-fork", action="store_true", help="Allow continuing a source run that already has a continuation")
    continue_long.add_argument("--json", action="store_true")
    lineage = subparsers.add_parser("long-handoff-lineage", help="Build a lineage summary from long handoff JSON files")
    lineage.add_argument("paths", nargs="+")
    lineage.add_argument("--json", action="store_true")
    return parser


def _persistent_nemo_adapter(db_path: str, no_memory_db: bool, mcp_url: str | None = None, mcp_prefix: str = ""):
    if mcp_url:
        return McpNemoAdapter(mcp_url, tool_prefix=mcp_prefix)
    if no_memory_db:
        return InMemoryNemoAdapter()
    return PersistentNemoAdapter(PersistentMemoryStore(Path(db_path)))


def _nemo_memory_summaries(memory_db: str | None, no_memory_db: bool = False) -> list[str]:
    if no_memory_db or not memory_db or not Path(memory_db).exists():
        return []
    return [atom.atom.content for atom in PersistentMemoryStore(Path(memory_db)).search_atoms(limit=500)]


def _print_continuation_policy_block(policy: dict[str, object], json_output: bool) -> int:
    payload = {
        "error": "continuation_blocked_by_lineage_policy",
        "reasons": policy["reasons"],
        "source_run": policy["source_run"],
        "existing_continuations": policy["existing_continuations"],
    }
    if json_output:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(f"error={payload['error']} reasons={','.join(payload['reasons'])}")
    return 1


def _resolve_skill_prompt(skill_name: str | None, skills_root: str, *, quiet: bool = False) -> str:
    """Load the prompt for *skill_name* from *skills_root*, or return empty string."""
    if not skill_name:
        return ""
    from pathlib import Path
    skill = find_skill_by_name(skill_name, Path(skills_root))
    if skill is None:
        if not quiet:
            logging.warning("skill '%s' not found in %r", skill_name, skills_root)
        return ""
    return skill.prompt


def _write_apply_memory(applied: object, memory_db: str | None, no_memory_db: bool = False) -> None:
    adapter = _persistent_nemo_adapter(memory_db, no_memory_db)
    if adapter is None:
        return
    applied_files = tuple(getattr(applied, "applied_files"))
    adapter.call(
        NemoLifecyclePhase.REVIEW,
        "store_conversation",
        summary=(
            f"Review-to-main apply completed task={getattr(applied, 'task_id')} "
            f"run={getattr(applied, 'run_id')} applied_files={','.join(applied_files)}"
        ),
        topic="Review-To-Main Gate",
        tags=(str(getattr(applied, "task_id")), str(getattr(applied, "run_id")), "apply", "review_to_main"),
        atom_type=MemoryAtomType.DECISION.value,
        source_scope="review_gate",
        importance=9,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "blueprint":
        print(default_blueprint().to_text())
        return 0
    if args.command == "workflow":
        for phase in DEFAULT_WORKFLOW.phases:
            print(
                f"{phase.phase}: read_only={phase.read_only} mutation_allowed={phase.mutation_allowed} "
                f"approval={phase.approval_level} review_gate={phase.review_gate_required}"
            )
        return 0
    if args.command == "nemo-tools":
        phases = [ExecutionPhase(args.phase)] if args.phase else list(ExecutionPhase)
        for phase in phases:
            print(f"{phase}:")
            for tool in nemo_tools_for_phase(phase):
                print(f"- {tool.name} suite={tool.suite} risk={tool.risk}")
        return 0
    if args.command == "dry-run-write":
        workspace = Workspace.from_path(args.workspace)
        runner = SupervisedWorkflowRunner(QualityMutationEngine(workspace))
        plan = MutationPlan(writes=(FileWrite(args.path, args.content),))
        result = runner.dry_run_execution(plan)
        print(f"files={','.join(result.files)}")
        print(f"creates={','.join(result.creates)}")
        print(f"updates={','.join(result.updates)}")
        return 0
    if args.command == "apply-write":
        workspace = Workspace.from_path(args.workspace)
        runner = SupervisedWorkflowRunner(QualityMutationEngine(workspace))
        plan = MutationPlan(writes=(FileWrite(args.path, args.content),), approved=args.approve_execution)
        review = ReviewDecision(approved=args.approve_review, summary=args.review_summary)
        try:
            result = runner.execute_approved_plan(plan, review)
        except PermissionError as error:
            print(f"error={error}")
            return 1
        print(f"applied={','.join(result.applied_files)}")
        print(f"review={result.review.summary}")
        return 0
    if args.command == "handoff-plan":
        request = HandoffRequest(
            prd=args.objective,
            repo_path=args.repo,
            acceptance_criteria=tuple(args.acceptance or ["passes validation"]),
            validation_commands=tuple(args.validation or ["python -m unittest"]),
        )
        plan = build_handoff_plan(request)
        if args.json:
            print(json.dumps({"steps": [step.kind.value for step in plan.steps], "unattended": plan.can_run_unattended, "review_gate": plan.review_gate_required}, sort_keys=True))
        else:
            print(f"steps={len(plan.steps)} unattended={plan.can_run_unattended} review_gate={plan.review_gate_required}")
            for step in plan.steps:
                print(f"- {step.kind}: {step.summary}")
        return 0
    if args.command == "headless-run":
        request = HandoffRequest(
            prd=args.objective,
            repo_path=args.repo,
            acceptance_criteria=tuple(args.acceptance or ["passes validation"]),
            validation_commands=validation_commands_for_policy(args.validation_policy, tuple(args.validation or ())),
        )
        skill_prompt = _resolve_skill_prompt(args.skill, args.skills_root, quiet=bool(args.json))
        result = execute_headless_handoff(
            request,
            tuple(args.fail_validation),
            real_validation=args.real_validation,
            validation_cwd=args.validation_cwd,
            provider_mode=args.provider,
            model_profile=ModelProfile(model=args.model_profile, base_url=args.lmstudio_base_url),
            engine_command=tuple(shlex.split(args.engine_command)) if args.engine_command else None,
            timeout_seconds=args.timeout,
            target_files=tuple(args.target_file),
            validation_python_scripts=tuple(args.validation_python),
            validation_policy=args.validation_policy,
            bounded_simulation=args.bounded_simulation,
            nemo_adapter=_persistent_nemo_adapter(args.memory_db, args.no_memory_db, args.mcp_url, args.mcp_prefix),
            skill_prompt=skill_prompt,
            permissions_file=args.permissions_file or "",
            image_path=args.image or "",
        )
        score = score_headless_result(result)
        if args.save_json:
            save_headless_result_json(result, args.save_json)
        if args.json:
            effective_mutation = result.effective_mutation_result
            payload = {"score": score.score, "events": len(result.timeline.events), "artifacts": len(result.artifacts), "validation_passed": score.validation_passed, "provider": effective_mutation.provider if effective_mutation else None, "model": effective_mutation.model_profile.model if effective_mutation and effective_mutation.model_profile else None, "changed_files": list(result.effective_changed_files)}
            if args.save_json:
                payload["saved_json"] = args.save_json
            print(json.dumps(payload, sort_keys=True))
        else:
            print(f"task={result.task.id} run={result.run.id}")
            print(f"events={len(result.timeline.events)} artifacts={len(result.artifacts)} score={score.score}")
            print(result.review_package.to_markdown())
        return 0
    if args.command == "self-modify":
        request = SelfModRequest(
            description=args.description,
            task_type=SelfModTaskType(args.type),
            target_files=tuple(args.target_file),
            validation_policy=args.validation_policy,
            validation_commands=tuple(args.validation or ()),
            repair_budget=args.repair_budget,
            memory_db=args.memory_db,
            repo_root=args.repo,
            provider_mode=args.provider,
            timeout_seconds=args.timeout,
            bounded_simulation=args.bounded_simulation,
            real_validation=args.real_validation,
        )
        result = execute_self_modification(
            request,
            task_id=args.task_id,
            run_id=args.run_id,
            model_profile=ModelProfile(model=args.model_profile, base_url=args.lmstudio_base_url),
            engine_command=tuple(shlex.split(args.engine_command)) if args.engine_command else None,
        )
        payload = result.to_summary_dict()
        if args.json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print(f"task={payload['task_id']} run={payload['run_id']} validation_passed={payload['validation_passed']} grade={payload['grade']}")
            print(f"repo={payload['repo_root']}")
            print(f"changed_files={','.join(payload['changed_files'])}")
            print(f"permissions_file={payload['permissions_file']}")
            print(f"run_json={payload['run_json']}")
        return 0
    if args.command == "self-mod-status":
        payload = self_mod_status(args.run_json)
        if args.json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print(f"task={payload['task_id']} run={payload['run_id']} grade={payload['grade']} mergeable={payload['mergeable']}")
            print(f"risk_flags={','.join(payload['risk_flags'])}")
        return 0
    if args.command == "self-mod-review":
        payload = self_mod_review(args.run_json, args.permissions_file)
        if args.json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print(f"mergeable={payload['mergeable']} risk_flags={','.join(payload['risk_flags'])}")
            print(json.dumps(payload["merge_plan"], indent=2, sort_keys=True))
        return 0 if payload["mergeable"] else 1
    if args.command == "self-mod-apply":
        try:
            payload = self_mod_apply(args.run_json, approve_review=args.approve_review, backup_dir=args.backup_dir, permissions_file=args.permissions_file, autonomy_profile=args.autonomy_profile)
        except PermissionError as error:
            error_payload = {"error": str(error)}
            if args.json:
                print(json.dumps(error_payload, sort_keys=True))
            else:
                print(f"error={error}")
            return 1
        if args.save_apply_json:
            target = Path(args.save_apply_json)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        if args.json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print(f"applied={','.join(payload['applied_files'])}")
        return 0
    if args.command == "self-mod-rollback":
        try:
            payload = self_mod_rollback(args.apply_json, approve_review=args.approve_review)
        except (PermissionError, FileNotFoundError) as error:
            error_payload = {"error": str(error)}
            if args.json:
                print(json.dumps(error_payload, sort_keys=True))
            else:
                print(f"error={error}")
            return 1
        if args.json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print(f"restored={','.join(payload['restored_files'])} deleted={','.join(payload['deleted_files'])}")
        return 0
    if args.command == "self-mod-trajectory":
        payload = self_mod_trajectory(args.run_json)
        if args.json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print(f"task={payload['task_id']} run={payload['run_id']} grade={payload['grade']} mergeable={payload['mergeable']}")
            print(f"changed_files={','.join(payload['changed_files'])}")
            print(f"risks={','.join(payload['risk_flags']) or 'none'}")
        return 0
    if args.command == "self-mod-impact":
        payload = self_mod_impact(args.run_json)
        if args.json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print(f"changed_files={','.join(payload['changed_files'])}")
            print(f"suggested_tests={','.join(payload['suggested_tests']) or 'none'}")
            print(f"risk_flags={','.join(payload['risk_flags']) or 'none'}")
        return 0
    if args.command == "self-mod-similar-runs":
        query = args.query
        try:
            if Path(query).exists():
                query = self_mod_trajectory(query).get("objective") or query
        except OSError:
            pass
        payload = self_mod_similar_runs(args.memory_db, str(query), limit=args.limit)
        if args.json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print(f"matches={payload['count']} query={payload['query']}")
            for run in payload["runs"]:
                print(f"- {run['id']}: {run['content']}")
        return 0
    if args.command == "long-handoff-run":
        request = HandoffRequest(
            prd=args.prd_text or args.objective,
            repo_path=args.repo,
            acceptance_criteria=tuple(args.acceptance or ["passes validation"]),
            validation_commands=validation_commands_for_policy(args.validation_policy, tuple(args.validation or ())),
            objective_summary=args.objective,
            linked_prd=args.prd_text or None,
            spec_mode=args.spec_mode,
        )
        result = execute_long_handoff_supervisor(
            request,
            budget=LongHandoffBudget(
                max_runtime_minutes=args.max_runtime_minutes,
                heartbeat_minutes=args.heartbeat_minutes,
                max_heartbeats=args.max_heartbeats,
                token_budget=args.token_budget,
                pause_after_minutes=args.pause_after_minutes,
                plan_minutes=args.plan_minutes,
                execute_minutes=args.execute_minutes,
                review_minutes=args.review_minutes,
            ),
            task_id=args.task_id,
            run_id=args.run_id,
            real_validation=args.real_validation,
            validation_cwd=args.validation_cwd,
            provider_mode=args.provider,
            model_profile=ModelProfile(model=args.model_profile, base_url=args.lmstudio_base_url),
            engine_command=tuple(shlex.split(args.engine_command)) if args.engine_command else None,
            timeout_seconds=args.timeout,
            target_files=tuple(args.target_file),
            validation_python_scripts=tuple(args.validation_python),
            validation_policy=args.validation_policy,
            nemo_adapter=_persistent_nemo_adapter(args.memory_db, args.no_memory_db, args.mcp_url, args.mcp_prefix),
            permissions_file=args.permissions_file or "",
            image_path=args.image or "",
            repair_time_limit_seconds=args.repair_time_limit_seconds,
            validation_time_budget_seconds=args.validation_time_budget_seconds,
            validation_escalation_mode=args.validation_escalation_mode,
        )
        score = score_headless_result(result)
        if args.save_json:
            save_headless_result_json(result, args.save_json)
        heartbeats = [event for event in result.timeline.events if event.kind == EventKind.HEARTBEAT]
        escalations = [event for event in result.timeline.events if event.kind == EventKind.ESCALATION]
        pauses = [event for event in result.timeline.events if event.kind == EventKind.PAUSED]
        if args.json:
            payload = {
                "score": score.score,
                "events": len(result.timeline.events),
                "artifacts": len(result.artifacts),
                "validation_passed": score.validation_passed,
                "heartbeats": len(heartbeats),
                "escalations": len(escalations),
                "paused": bool(pauses),
                "changed_files": list(result.effective_changed_files),
            }
            if args.save_json:
                payload["saved_json"] = args.save_json
            print(json.dumps(payload, sort_keys=True))
        else:
            print(f"task={result.task.id} run={result.run.id}")
            print(f"events={len(result.timeline.events)} artifacts={len(result.artifacts)} heartbeats={len(heartbeats)} score={score.score}")
        return 0
    if args.command == "show-run-json":
        summary = summarize_persisted_result(load_headless_result_json(args.path))
        if args.json:
            print(json.dumps(summary, sort_keys=True))
        else:
            print(f"task={summary['task_id']} run={summary['run_id']} score={summary['score']} grade={summary['grade']}")
        return 0
    if args.command == "eval-run-json":
        score = score_headless_result(load_headless_result_json(args.path))
        payload = {"score": score.score, "grade": score.grade, "reasons": list(score.reasons)}
        if args.json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print(f"score={score.score} grade={score.grade} reasons={','.join(score.reasons)}")
        return 0
    if args.command == "replay-run-json":
        replay = build_replay_summary(load_headless_result_json(args.path))
        if args.json:
            print(json.dumps(replay, sort_keys=True))
        else:
            print(f"task={replay['task_id']} run={replay['run_id']} grade={replay['grade']} can_replay={replay['can_replay']}")
            for event in replay["events"]:
                payload_ref = f" payload={event['payload_ref']}" if event.get("payload_ref") else ""
                print(f"{event['sequence']}. {event['phase']}/{event['kind']}: {event['summary']}{payload_ref}")
        return 0
    if args.command == "metrics-run-json":
        metrics = export_run_metrics(load_headless_result_json(args.path))
        if args.json:
            print(json.dumps(metrics, sort_keys=True))
        else:
            print(
                " ".join(
                    (
                        f"task={metrics['task_id']}",
                        f"run={metrics['run_id']}",
                        f"success_rate={metrics['success_rate']}",
                        f"validation_pass_rate={metrics['validation_pass_rate']}",
                        f"override_rate={metrics['override_rate']}",
                        f"stale_memory_incidents={metrics['stale_memory_incidents']}",
                        f"tool_failure_rate={metrics['tool_failure_rate']}",
                    )
                )
            )
        return 0
    if args.command == "review-run-json":
        plan = build_merge_plan(load_headless_result_json(args.path))
        if args.save_plan:
            target = Path(args.save_plan)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(plan.to_markdown(), encoding="utf-8")
        if args.json:
            print(json.dumps(plan.to_dict(), sort_keys=True))
        else:
            print(plan.to_markdown(), end="")
        return 0 if plan.mergeable else 1
    if args.command == "mission-control-state":
        state = build_mission_control_state(args.repo, args.runtimes)
        if args.save_json:
            target = Path(args.save_json)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        if args.json:
            print(json.dumps(state, sort_keys=True))
        else:
            print(f"product={state['product']} runs={len(state['runs'])} approvals={len(state['approval_queue'])}")
            for run in state["runs"][:10]:
                print(f"- {run['review_status']} {run['task_id']} {run['run_id']} changes={len(run['changed_files'])}")
        return 0
    if args.command == "watch":
        print(f"NEMOCODE watching {args.repo} for '# ai!' comments... (Ctrl+C to stop)")
        
        def on_comments(requests: list[WatchRequest]):
            for req in requests:
                print(f"\n[AI Comment Detected] {req.filepath}:{req.line_number} -> {req.objective}")
                # For now, we just print. In a real scenario, we might trigger a background task.
                print("Tip: Run 'nemocode headless-run \"{req.objective}\" --target-file {req.filepath}' to execute.")

        watcher = FileWatcher(args.repo, on_comments)
        watcher.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nStopping watcher...")
            watcher.stop()
        return 0

    if args.command == "mission-control-server":
        from nemo_coding_platform.mission_control_server import MissionControlServerConfig, run_server

        config = MissionControlServerConfig.from_paths(
            repo=args.repo,
            runtimes=args.runtimes,
            apply_results=args.apply_results,
            memory_db=None if args.no_memory_db else args.memory_db,
        )
        run_server(args.host, args.port, config)
        return 0

    if args.command == "mcp-server":
        from nemo_coding_platform.mcp_server import run_mcp_server
        run_mcp_server(args.host, args.port)
        return 0

    if args.command == "apply-run-json":
        plan = build_merge_plan(load_headless_result_json(args.path))
        if args.save_plan:
            target = Path(args.save_plan)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(plan.to_markdown(), encoding="utf-8")
        try:
            applied = apply_merge_plan(plan, approve_review=args.approve_review, backup_dir=args.backup_dir, autonomy_profile=args.autonomy_profile)
        except PermissionError as error:
            payload = {"error": str(error), "mergeable": plan.mergeable, "risk_flags": list(plan.risk_flags)}
            if args.json:
                print(json.dumps(payload, sort_keys=True))
            else:
                print(f"error={error}")
            return 1
        if args.save_apply_report:
            target = Path(args.save_apply_report)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(applied.to_markdown(), encoding="utf-8")
        if args.save_apply_json:
            target = Path(args.save_apply_json)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(applied.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        _write_apply_memory(applied, args.memory_db, args.no_memory_db)
        if args.json:
            print(json.dumps(applied.to_dict(), sort_keys=True))
        else:
            print(f"applied={','.join(applied.applied_files)}")
        return 0
    if args.command == "rollback-apply-json":
        applied = MergeApplyResult.from_dict(json.loads(Path(args.path).read_text(encoding="utf-8")))
        try:
            rollback = rollback_apply_result(applied, approve_review=args.approve_review)
        except (PermissionError, FileNotFoundError) as error:
            payload = {"error": str(error)}
            if args.json:
                print(json.dumps(payload, sort_keys=True))
            else:
                print(f"error={error}")
            return 1
        if args.save_rollback_report:
            target = Path(args.save_rollback_report)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(rollback.to_markdown(), encoding="utf-8")
        if args.json:
            print(json.dumps(rollback.to_dict(), sort_keys=True))
        else:
            print(f"restored={','.join(rollback.restored_files)} deleted={','.join(rollback.deleted_files)}")
        return 0
    if args.command == "long-handoff-resume":
        resume = build_long_handoff_resume_plan(load_headless_result_json(args.path)).to_dict()
        if args.save_json:
            target = Path(args.save_json)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(resume, indent=2, sort_keys=True), encoding="utf-8")
        if args.json:
            print(json.dumps(resume, sort_keys=True))
        else:
            print(f"can_resume={resume['can_resume']} task={resume['task_id']} run={resume['run_id']}")
            print(f"next_action={resume['next_action']}")
        return 0
    if args.command == "long-handoff-continue":
        source_payload = load_headless_result_json(args.path)
        if args.lineage_context:
            lineage_payloads = [load_headless_result_json(path) for path in args.lineage_context]
            policy = evaluate_long_handoff_continuation_policy(source_payload, lineage_payloads, allow_fork=args.allow_fork)
            if not policy["allowed"]:
                return _print_continuation_policy_block(policy, args.json)
        else:
            policy = evaluate_long_handoff_memory_policy(source_payload, _nemo_memory_summaries(args.memory_db, args.no_memory_db), allow_fork=args.allow_fork)
            if not policy["allowed"]:
                return _print_continuation_policy_block(policy, args.json)
        try:
            result = execute_long_handoff_continuation(
                source_payload,
                objective=args.objective,
                acceptance_criteria=tuple(args.acceptance) if args.acceptance else None,
                validation_commands=validation_commands_for_policy(args.validation_policy, tuple(args.validation or ())),
                budget=LongHandoffBudget(
                    max_runtime_minutes=args.max_runtime_minutes,
                    heartbeat_minutes=args.heartbeat_minutes,
                    max_heartbeats=args.max_heartbeats,
                    token_budget=args.token_budget,
                    pause_after_minutes=args.pause_after_minutes,
                    plan_minutes=args.plan_minutes,
                    execute_minutes=args.execute_minutes,
                    review_minutes=args.review_minutes,
                ),
                real_validation=args.real_validation,
                validation_cwd=args.validation_cwd,
                provider_mode=args.provider,
                model_profile=ModelProfile(model=args.model_profile, base_url=args.lmstudio_base_url),
                engine_command=tuple(shlex.split(args.engine_command)) if args.engine_command else None,
                timeout_seconds=args.timeout,
                target_files=tuple(args.target_file),
                validation_python_scripts=tuple(args.validation_python),
                validation_policy=args.validation_policy,
                nemo_adapter=_persistent_nemo_adapter(args.memory_db, args.no_memory_db, args.mcp_url, args.mcp_prefix),
                repair_time_limit_seconds=args.repair_time_limit_seconds,
                validation_time_budget_seconds=args.validation_time_budget_seconds,
                validation_escalation_mode=args.validation_escalation_mode,
            )
        except ValueError as error:
            print(f"error={error}")
            return 1
        score = score_headless_result(result)
        if args.save_json:
            save_headless_result_json(result, args.save_json)
        resumed = [event for event in result.timeline.events if event.kind == EventKind.RESUMED]
        heartbeats = [event for event in result.timeline.events if event.kind == EventKind.HEARTBEAT]
        if args.json:
            payload = {
                "score": score.score,
                "events": len(result.timeline.events),
                "artifacts": len(result.artifacts),
                "validation_passed": score.validation_passed,
                "heartbeats": len(heartbeats),
                "resumed": bool(resumed),
                "changed_files": list(result.effective_changed_files),
                "run_id": result.run.id,
                "task_id": result.task.id,
            }
            if args.save_json:
                payload["saved_json"] = args.save_json
            print(json.dumps(payload, sort_keys=True))
        else:
            print(f"task={result.task.id} run={result.run.id} resumed={bool(resumed)} score={score.score}")
        return 0
    if args.command == "long-handoff-lineage":
        lineage = build_long_handoff_lineage([load_headless_result_json(path) for path in args.paths])
        if args.json:
            print(json.dumps(lineage, sort_keys=True))
        else:
            print(f"nodes={lineage['node_count']} links={lineage['link_count']} complete={lineage['complete']} ready={lineage['ready']}")
            for link in lineage["links"]:
                print(f"{link['source_run']} -> {link['continuation_run']} token={link['resume_token']}")
        return 0
    parser.error(f"unknown command: {args.command}")
    return 2
