from __future__ import annotations

import unittest

from nemo_coding_platform.core.headless_handoff import HandoffPlan, HandoffRequest, HandoffStep, HandoffStepKind
from nemo_coding_platform.core.todo_guard import (
    TodoItem,
    TodoList,
    build_todo_reminder,
    check_todos_complete,
    extract_todos_from_plan,
)


_DUMMY_REQUEST = HandoffRequest(
    prd="test",
    repo_path=".",
    acceptance_criteria=("ok",),
    validation_commands=("test",),
)


def _make_plan(*pairs: tuple[HandoffStepKind, str]) -> HandoffPlan:
    steps = tuple(HandoffStep(kind, summary) for kind, summary in pairs)
    return HandoffPlan(request=_DUMMY_REQUEST, steps=steps)


class TestTodoItem(unittest.TestCase):
    def test_pending_by_default(self) -> None:
        item = TodoItem("Write tests")
        self.assertFalse(item.is_complete)
        self.assertEqual(item.status, "pending")

    def test_mark_completed(self) -> None:
        item = TodoItem("Write tests")
        completed = item.mark_completed()
        self.assertTrue(completed.is_complete)
        self.assertEqual(completed.content, "Write tests")


class TestTodoList(unittest.TestCase):
    def test_all_complete_when_empty(self) -> None:
        todos = TodoList(items=[])
        self.assertTrue(todos.all_complete)

    def test_not_complete_with_pending_items(self) -> None:
        todos = TodoList(items=[TodoItem("step"), TodoItem("step2")])
        self.assertFalse(todos.all_complete)

    def test_complete_when_all_marked(self) -> None:
        todos = TodoList(items=[TodoItem("s", "completed"), TodoItem("s2", "completed")])
        self.assertTrue(todos.all_complete)

    def test_incomplete_items_filter(self) -> None:
        todos = TodoList(items=[TodoItem("a", "completed"), TodoItem("b")])
        self.assertEqual(len(todos.incomplete_items), 1)
        self.assertEqual(todos.incomplete_items[0].content, "b")


class TestExtractTodosFromPlan(unittest.TestCase):
    def test_extracts_actionable_steps(self) -> None:
        plan = _make_plan(
            (HandoffStepKind.BOOTSTRAP_NEMO, "Load NEMO context"),
            (HandoffStepKind.IMPLEMENT_WITH_ENGINE, "Run Space Code mutation"),
            (HandoffStepKind.RUN_VALIDATION, "Run tests"),
            (HandoffStepKind.WRITE_NEMO_MEMORY, "NEMO review writeback"),
        )
        todos = extract_todos_from_plan(plan)
        # BOOTSTRAP_NEMO and WRITE_NEMO_MEMORY should be skipped
        contents = [item.content for item in todos.items]
        self.assertNotIn("Load NEMO context", contents)
        self.assertNotIn("NEMO review writeback", contents)
        self.assertIn("Run Space Code mutation", contents)
        self.assertIn("Run tests", contents)

    def test_empty_plan_gives_empty_todos(self) -> None:
        plan = HandoffPlan(request=_DUMMY_REQUEST, steps=())
        todos = extract_todos_from_plan(plan)
        self.assertEqual(todos.items, [])


class TestCheckTodosComplete(unittest.TestCase):
    def test_empty_list_is_complete(self) -> None:
        self.assertTrue(check_todos_complete(TodoList(items=[])))

    def test_pending_items_not_complete(self) -> None:
        self.assertFalse(check_todos_complete(TodoList(items=[TodoItem("x")])))

    def test_all_completed(self) -> None:
        self.assertTrue(check_todos_complete(TodoList(items=[TodoItem("x", "completed")])))


class TestBuildTodoReminder(unittest.TestCase):
    def test_returns_empty_for_no_incomplete(self) -> None:
        todos = TodoList(items=[TodoItem("x", "completed")])
        self.assertEqual(build_todo_reminder(todos), "")

    def test_returns_reminder_with_system_reminder_tags(self) -> None:
        todos = TodoList(items=[TodoItem("step A"), TodoItem("step B")])
        reminder = build_todo_reminder(todos)
        self.assertIn("<system_reminder>", reminder)
        self.assertIn("</system_reminder>", reminder)
        self.assertIn("step A", reminder)
        self.assertIn("step B", reminder)

    def test_only_incomplete_items_in_reminder(self) -> None:
        todos = TodoList(items=[TodoItem("COMPLETED_MARKER", "completed"), TodoItem("PENDING_MARKER")])
        reminder = build_todo_reminder(todos)
        self.assertIn("PENDING_MARKER", reminder)
        self.assertNotIn("COMPLETED_MARKER", reminder)


if __name__ == "__main__":
    unittest.main()
