"""Agent trace events: typed schema for tracing agent execution steps."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4


class TraceEventKind(str, Enum):
    STATUS = "status"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    PHASE = "phase"
    FINALIZE = "finalize"


class TraceEventStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    ERROR = "error"
    SKIPPED = "skipped"


def build_trace_event(
    *,
    step: int,
    kind: TraceEventKind | str,
    label: str,
    status: TraceEventStatus | str,
    detail: str = "",
    tool_name: str | None = None,
    source: str = "server",
    duration_ms: int | None = None,
) -> dict[str, object]:
    """Build a serialisable agent trace event dict."""
    kind_val = kind.value if isinstance(kind, TraceEventKind) else str(kind)
    status_val = status.value if isinstance(status, TraceEventStatus) else str(status)
    event: dict[str, object] = {
        "id": f"trace-{uuid4().hex[:10]}",
        "step": step,
        "kind": kind_val,
        "label": label,
        "status": status_val,
        "detail": detail,
        "source": source,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    if tool_name:
        event["tool_name"] = tool_name
    if duration_ms is not None:
        event["duration_ms"] = duration_ms
    return event


def append_tool_call_traces(
    trace: list[dict[str, object]],
    tool_calls: list[dict[str, object]],
    *,
    start_index: int,
    step_counter: int,
) -> tuple[int, int]:
    """Append tool_result events to *trace* for each unprocessed tool call.

    Returns (next_start_index, next_step_counter).
    """
    index = start_index
    step = step_counter
    while index < len(tool_calls):
        tool = tool_calls[index]
        name = str(tool.get("name") or "tool")
        alias_name = str(tool.get("alias_name") or "").strip()
        status = str(tool.get("status") or TraceEventStatus.COMPLETED.value)
        summary = str(tool.get("summary") or "")
        label = f"tool: {name}"
        if alias_name and alias_name != name:
            label = f"{label} (alias {alias_name})"
        trace.append(
            build_trace_event(
                step=step,
                kind=TraceEventKind.TOOL_RESULT,
                label=label,
                status=status,
                detail=summary,
                tool_name=name,
            )
        )
        step += 1
        index += 1
    return index, step
