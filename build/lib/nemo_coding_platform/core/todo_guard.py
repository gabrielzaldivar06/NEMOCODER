"""Todo-Awareness Guard for NEMOCODE.

Prevents the agent from silently abandoning incomplete plan steps. When the
agent finishes a cycle with validation still failing, this module detects
unfinished TODOs derived from the original handoff plan and generates a
``<system_reminder>`` message to inject into the next repair cycle context.

Inspired by deer-flow's ``TodoMiddleware`` in
``backend/packages/harness/deerflow/agents/middlewares/todo_middleware.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from nemo_coding_platform.core.headless_handoff import HandoffPlan


@dataclass
class TodoItem:
    """A single trackable step derived from a handoff plan."""

    content: str
    status: str = "pending"  # "pending" | "completed"

    def mark_completed(self) -> "TodoItem":
        return TodoItem(content=self.content, status="completed")

    @property
    def is_complete(self) -> bool:
        return self.status == "completed"


@dataclass
class TodoList:
    """Ordered list of TodoItems with aggregate status."""

    items: list[TodoItem] = field(default_factory=list)

    @property
    def all_complete(self) -> bool:
        # Empty list is vacuously complete (nothing left to do).
        return all(item.is_complete for item in self.items)

    @property
    def incomplete_items(self) -> list[TodoItem]:
        return [item for item in self.items if not item.is_complete]


def extract_todos_from_plan(plan: HandoffPlan) -> TodoList:
    """Build a :class:`TodoList` from the steps of a :class:`HandoffPlan`.

    Each plan step becomes a pending ``TodoItem``. Steps that represent
    already-completed lifecycle bookkeeping (NEMO startup/review) are
    excluded because they are handled automatically by the platform.

    Args:
        plan: The handoff plan whose steps to convert.

    Returns:
        A :class:`TodoList` with one item per actionable plan step.
    """
    from nemo_coding_platform.core.headless_handoff import HandoffStepKind

    _SKIP_KINDS = {
        HandoffStepKind.BOOTSTRAP_NEMO,
        HandoffStepKind.WRITE_NEMO_MEMORY,
    }

    items: list[TodoItem] = []
    for step in plan.steps:
        if step.kind in _SKIP_KINDS:
            continue
        items.append(TodoItem(content=step.summary))

    return TodoList(items=items)


def check_todos_complete(todos: TodoList) -> bool:
    """Return True if all todo items are marked completed.

    Args:
        todos: The :class:`TodoList` to check.

    Returns:
        ``True`` when every item is complete (or the list is empty).
    """
    return todos.all_complete


def build_todo_reminder(todos: TodoList) -> str:
    """Build a ``<system_reminder>`` XML block for incomplete todo items.

    The reminder is injected into the repair cycle's Aider context so the
    model is forced to acknowledge and complete outstanding steps before
    producing a final response.

    Args:
        todos: The :class:`TodoList` containing at least one incomplete item.

    Returns:
        A formatted system reminder string ready for inclusion in a prompt.
    """
    incomplete = todos.incomplete_items
    if not incomplete:
        return ""

    lines = ["<system_reminder>"]
    lines.append(
        "You have incomplete plan steps that must be finished before submitting your final answer:"
    )
    lines.append("")
    for item in incomplete:
        lines.append(f"- [{item.status}] {item.content}")
    lines.append("")
    lines.append(
        "Please complete these steps now. Only respond when all items are done "
        "and validation passes."
    )
    lines.append("</system_reminder>")
    return "\n".join(lines)
