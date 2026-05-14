from __future__ import annotations

from nemo_coding_platform.core.nemo_adapter import InMemoryNemoAdapter
from nemo_coding_platform.core.nemo_lifecycle import NemoLifecyclePhase


def nemo_before_attempt(
    adapter: object,
    query: str,
    tags: tuple[str, ...] = (),
    limit: int = 3,
) -> str:
    """Search NEMO for similar past failures/solutions before an attempt.

    Returns a formatted context snippet (possibly empty) to prepend to the
    repair context. Never raises — NEMO errors return empty string silently.
    Skips InMemoryNemoAdapter (test stub) and None.
    """
    if adapter is None or isinstance(adapter, InMemoryNemoAdapter):
        return ""
    try:
        _, result = adapter.call(
            NemoLifecyclePhase.BUILD,
            "search_memories",
            query=query,
            limit=limit,
            compact=True,
            tags_include=list(tags) if tags else [],
        )
        if not result.ok:
            return ""
        memories = result.payload.get("results") or result.payload.get("memories") or ""
        if not memories:
            return ""
        return f"\n# Prior NEMO Memory — similar failures and solutions\n{memories}\n"
    except Exception:
        return ""


def nemo_after_failure(
    adapter: object,
    evidence: str,
    task_id: str = "",
    attempt_n: int = 0,
    tags: tuple[str, ...] = ("repair_failure",),
) -> None:
    """Immediately ingest a repair failure into NEMO.

    Called after each failed attempt — not post-loop — so crashes during repair
    do not lose intermediate failure history. Never raises.
    """
    if adapter is None or isinstance(adapter, InMemoryNemoAdapter):
        return
    try:
        content_parts = []
        if task_id:
            content_parts.append(f"task={task_id}")
        content_parts.append(f"repair_attempt_{attempt_n}: {evidence}")
        content = " ".join(content_parts)
        all_tags = list(tags)
        if task_id:
            all_tags.append(task_id)
        adapter.call(
            NemoLifecyclePhase.REVIEW,
            "cognitive_ingest",
            content=content,
            memory_type="repair_failure",
            tags=all_tags,
            context=f"attempt {attempt_n}",
        )
    except Exception:
        pass


def nemo_after_success(
    adapter: object,
    solution_summary: str,
    task_id: str = "",
    tags: tuple[str, ...] = ("repair_success",),
) -> None:
    """Ingest a successful repair solution into NEMO with high importance.

    importance_level=8 ensures this is retrieved preferentially in future
    search_memories calls about similar problems. Never raises.
    """
    if adapter is None or isinstance(adapter, InMemoryNemoAdapter):
        return
    try:
        content_parts = []
        if task_id:
            content_parts.append(f"task={task_id}")
        content_parts.append(solution_summary)
        content = " ".join(content_parts)
        all_tags = list(tags)
        if task_id:
            all_tags.append(task_id)
        adapter.call(
            NemoLifecyclePhase.REVIEW,
            "cognitive_ingest",
            content=content,
            memory_type="repair_success",
            tags=all_tags,
            importance_level=8,
        )
    except Exception:
        pass


def nemo_cross_run_context(
    adapter: object,
    task_id: str,
    objective: str,
    limit: int = 8,
) -> str:
    """Retrieve cross-run context for a continuation — what happened in prior iterations.

    Used by long_handoff_supervisor before resuming a paused run. Never raises.
    Returns formatted string or "" if nothing found or adapter is stub.
    """
    if adapter is None or isinstance(adapter, InMemoryNemoAdapter):
        return ""
    try:
        _, result = adapter.call(
            NemoLifecyclePhase.BUILD,
            "search_memories",
            query=f"task {task_id}: {objective[:80]}",
            limit=limit,
            compact=True,
            tags_include=[task_id] if task_id else [],
        )
        if not result.ok:
            return ""
        memories = result.payload.get("results") or result.payload.get("memories") or ""
        if not memories:
            return ""
        return f"\n# NEMO Cross-Run Memory — prior iteration history for task {task_id}\n{memories}\n"
    except Exception:
        return ""
