from __future__ import annotations

import unittest

from nemo_coding_platform.core.nemo_adapter import InMemoryNemoAdapter, NemoCall, NemoCallResult
from nemo_coding_platform.core.nemo_lifecycle import NemoLifecyclePhase
from nemo_coding_platform.core.nemo_learning import (
    build_project_context,
    ingest_project_architecture,
    ingest_task_outcome,
)


def _make_adapter() -> InMemoryNemoAdapter:
    return InMemoryNemoAdapter()


class _FakeAdapter:
    """Minimal fake adapter that records calls and returns configurable payloads."""

    def __init__(self, search_payload: dict | None = None) -> None:
        self.calls: list[tuple] = []
        self._search_payload = search_payload or {}

    def call(self, phase: NemoLifecyclePhase, tool_name: str, **kwargs):
        self.calls.append((phase, tool_name, kwargs))
        call = NemoCall(phase, tool_name, dict(kwargs))
        if tool_name == "search_memories":
            payload = self._search_payload
        else:
            payload = {}
        return self, NemoCallResult(call, True, payload)


class NemoLearningTests(unittest.TestCase):
    # --- InMemoryNemoAdapter skip guard ---

    def test_ingest_task_outcome_skips_in_memory_adapter(self) -> None:
        adapter = _make_adapter()
        ingest_task_outcome(
            adapter,
            objective="build a game",
            repo_path="/tmp/repo",
            files_changed=["game.py"],
            result_summary="Created game.py with pygame",
            success=True,
        )
        self.assertEqual(len(adapter.calls), 0)

    def test_ingest_project_architecture_skips_in_memory_adapter(self) -> None:
        adapter = _make_adapter()
        ingest_project_architecture(
            adapter,
            repo_path="/tmp/repo",
            key_files={"main.py": "import pygame"},
        )
        self.assertEqual(len(adapter.calls), 0)

    def test_build_project_context_returns_empty_for_in_memory_adapter(self) -> None:
        adapter = _make_adapter()
        result = build_project_context(adapter, repo_path="/tmp/repo", task="build a game")
        self.assertEqual(result, "")

    def test_ingest_task_outcome_skips_none_adapter(self) -> None:
        # Should not raise
        ingest_task_outcome(None, "obj", "/tmp", [], "summary", True)

    # --- ingest_task_outcome ---

    def test_ingest_task_outcome_calls_cognitive_ingest(self) -> None:
        adapter = _FakeAdapter()
        ingest_task_outcome(
            adapter,
            objective="build a game",
            repo_path="/tmp/repo",
            files_changed=["game.py", "assets/sprite.png"],
            result_summary="Created pygame game with sprite support",
            success=True,
        )
        tool_names = [c[1] for c in adapter.calls]
        self.assertIn("cognitive_ingest", tool_names)

    def test_ingest_task_outcome_content_includes_objective_and_files(self) -> None:
        adapter = _FakeAdapter()
        ingest_task_outcome(
            adapter,
            objective="build a game",
            repo_path="/tmp/repo",
            files_changed=["game.py"],
            result_summary="pygame game created",
            success=True,
        )
        ingest_call = next(c for c in adapter.calls if c[1] == "cognitive_ingest")
        content = ingest_call[2].get("content", "")
        self.assertIn("game.py", content)
        self.assertIn("SUCCESS", content)

    def test_ingest_task_outcome_failed_has_lower_importance(self) -> None:
        adapter = _FakeAdapter()
        ingest_task_outcome(
            adapter,
            objective="build a game",
            repo_path="/tmp/repo",
            files_changed=[],
            result_summary="syntax error",
            success=False,
        )
        ingest_call = next(c for c in adapter.calls if c[1] == "cognitive_ingest")
        self.assertEqual(ingest_call[2].get("importance_level"), 5)

    # --- ingest_project_architecture ---

    def test_ingest_project_architecture_skips_if_already_stored(self) -> None:
        # search_memories returns non-empty results → should skip cognitive_ingest
        adapter = _FakeAdapter(search_payload={"results": "existing architecture memory"})
        ingest_project_architecture(
            adapter,
            repo_path="/tmp/repo",
            key_files={"main.py": "code"},
        )
        tool_names = [c[1] for c in adapter.calls]
        self.assertIn("search_memories", tool_names)
        self.assertNotIn("cognitive_ingest", tool_names)

    def test_ingest_project_architecture_stores_when_no_existing_memory(self) -> None:
        # search_memories returns empty → should call cognitive_ingest
        adapter = _FakeAdapter(search_payload={"results": ""})
        ingest_project_architecture(
            adapter,
            repo_path="/tmp/repo",
            key_files={"main.py": "import pygame\n# game code"},
        )
        tool_names = [c[1] for c in adapter.calls]
        self.assertIn("cognitive_ingest", tool_names)

    # --- build_project_context ---

    def test_build_project_context_formats_results(self) -> None:
        adapter = _FakeAdapter(search_payload={"results": "Project uses pygame, functional style."})
        ctx = build_project_context(adapter, repo_path="/tmp/repo", task="add enemy sprites")
        self.assertIn("pygame", ctx)
        self.assertIn("NEMO", ctx)

    def test_build_project_context_returns_empty_when_no_memories(self) -> None:
        adapter = _FakeAdapter(search_payload={"results": ""})
        ctx = build_project_context(adapter, repo_path="/tmp/repo")
        self.assertEqual(ctx, "")

    def test_build_project_context_makes_two_search_queries(self) -> None:
        adapter = _FakeAdapter(search_payload={"results": "some memory"})
        build_project_context(adapter, repo_path="/tmp/repo", task="do something")
        search_calls = [c for c in adapter.calls if c[1] == "search_memories"]
        self.assertEqual(len(search_calls), 2)
