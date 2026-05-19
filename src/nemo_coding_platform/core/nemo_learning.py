"""NEMO Learning Layer — LLM-assisted cross-session memory for project context.

Three write functions store memories after task completion:
  - ingest_project_architecture: one-time analysis of repo structure
  - ingest_task_outcome: narrative + style signals after each task

One read function surfaces them at task start:
  - build_project_context: queries NEMO, returns formatted string for system prompt injection

All functions skip InMemoryNemoAdapter (test stub) and None. Never raise.
"""
from __future__ import annotations

import json
import textwrap
import urllib.error
import urllib.request
from typing import TYPE_CHECKING

from nemo_coding_platform.core.nemo_adapter import InMemoryNemoAdapter
from nemo_coding_platform.core.nemo_lifecycle import NemoLifecyclePhase

_ARCH_MEMORY_TYPE = "project_architecture"
_OUTCOME_MEMORY_TYPE = "task_outcome"

_ARCH_SYS = (
    "You are a concise technical analyst. Analyze the provided source files "
    "and produce a 3-5 sentence architecture summary covering: what the project does, "
    "main components and their roles, key patterns and conventions used. "
    "Output ONLY the summary text — no headings, no JSON, no extra explanation."
)

_OUTCOME_SYS = (
    "You are a concise technical note-taker. Given a completed coding task, "
    "produce a 2-3 sentence summary covering: what was built, key technical decisions made, "
    "and one notable coding style signal (e.g. 'uses functional patterns', 'avoids OOP'). "
    "Output ONLY the summary text — no headings, no JSON, no extra explanation."
)


def _lm_extract(system_prompt: str, user_prompt: str, base_url: str, model: str) -> str:
    """Make a minimal LLM call for text extraction. Returns empty string on any error."""
    if not base_url:
        return ""
    try:
        endpoint = base_url.rstrip("/") + "/chat/completions"
        body = json.dumps({
            "model": model or "auto",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 256,
            "temperature": 0.0,
        }).encode()
        req = urllib.request.Request(
            endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"].strip()
    except Exception:
        return ""


def _skip(adapter: object) -> bool:
    return adapter is None or isinstance(adapter, InMemoryNemoAdapter)


def _tag_repo(repo_path: str) -> str:
    """Stable short tag derived from the repo path."""
    import hashlib
    return "repo_" + hashlib.md5(repo_path.encode()).hexdigest()[:8]


def ingest_project_architecture(
    adapter: object,
    repo_path: str,
    key_files: dict[str, str],
    base_url: str = "",
    model: str = "",
) -> None:
    """Analyze key repo files with LLM and store architecture summary in NEMO.

    Checks if an architecture memory already exists for this repo before calling
    the LLM — avoids redundant ingestion on every task.
    """
    if _skip(adapter):
        return
    try:
        _, existing = adapter.call(
            NemoLifecyclePhase.BUILD,
            "search_memories",
            query=f"project architecture {repo_path}",
            limit=1,
            compact=True,
            tags_include=[_ARCH_MEMORY_TYPE, _tag_repo(repo_path)],
        )
        if existing.ok and (existing.payload.get("results") or existing.payload.get("memories")):
            return  # Already stored — skip re-ingestion

        files_text = "\n\n".join(
            f"# {name}\n{content[:600]}" for name, content in list(key_files.items())[:6]
        )
        user_prompt = f"Repository path: {repo_path}\n\nKey files:\n{files_text}"
        summary = _lm_extract(_ARCH_SYS, user_prompt, base_url, model)
        if not summary:
            summary = f"Repository at {repo_path}. Files: {', '.join(list(key_files.keys())[:8])}"

        adapter.call(
            NemoLifecyclePhase.REVIEW,
            "cognitive_ingest",
            content=f"[project_architecture] {repo_path}\n{summary}",
            memory_type=_ARCH_MEMORY_TYPE,
            tags=[_ARCH_MEMORY_TYPE, _tag_repo(repo_path)],
            importance_level=8,
        )
    except Exception:
        pass


def ingest_task_outcome(
    adapter: object,
    objective: str,
    repo_path: str,
    files_changed: list[str],
    result_summary: str,
    success: bool,
    base_url: str = "",
    model: str = "",
) -> None:
    """Store task outcome narrative and style signals in NEMO after task completion."""
    if _skip(adapter):
        return
    try:
        status = "SUCCESS" if success else "FAILED"
        files_str = ", ".join(files_changed[:8]) or "none"

        user_prompt = textwrap.dedent(f"""\
            Task ({status}): {objective}
            Repository: {repo_path}
            Files changed: {files_str}
            Result: {result_summary[:400]}
        """)
        narrative = _lm_extract(_OUTCOME_SYS, user_prompt, base_url, model)
        if not narrative:
            narrative = (
                f"{status}: {objective[:100]} | files={files_str} | {result_summary[:200]}"
            )

        adapter.call(
            NemoLifecyclePhase.REVIEW,
            "cognitive_ingest",
            content=f"[task_outcome][{status}] {repo_path}\n{narrative}\nfiles_changed={files_str}",
            memory_type=_OUTCOME_MEMORY_TYPE,
            tags=[_OUTCOME_MEMORY_TYPE, _tag_repo(repo_path), "task_outcome_" + status.lower()],
            importance_level=7 if success else 5,
        )
    except Exception:
        pass


def build_project_context(
    adapter: object,
    repo_path: str,
    task: str = "",
) -> str:
    """Query NEMO for project architecture + recent outcomes.

    Returns a formatted string for injection into system prompts. Empty string
    if nothing found or adapter is stub. Never raises.
    """
    if _skip(adapter):
        return ""
    parts: list[str] = []
    repo_tag = _tag_repo(repo_path)

    _queries = [
        (f"project architecture conventions patterns {repo_path}", [_ARCH_MEMORY_TYPE, repo_tag], "Project context"),
        (f"task outcome completed {repo_path} {task[:60]}", [_OUTCOME_MEMORY_TYPE, repo_tag], "Recent work"),
    ]
    for query, tags, label in _queries:
        try:
            _, result = adapter.call(
                NemoLifecyclePhase.BUILD,
                "search_memories",
                query=query,
                limit=3,
                compact=True,
                tags_include=tags,
            )
            if result.ok:
                memories = result.payload.get("results") or result.payload.get("memories") or ""
                if memories:
                    parts.append(f"## NEMO — {label}\n{memories}")
        except Exception:
            pass

    if not parts:
        return ""
    return "\n\n".join(parts) + "\n"
