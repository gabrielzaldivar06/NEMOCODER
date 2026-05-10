"""Tests for core/trace.py — build_trace_event, append_tool_call_traces."""
from nemo_coding_platform.core.trace import (
    TraceEventKind,
    TraceEventStatus,
    append_tool_call_traces,
    build_trace_event,
)


class TestBuildTraceEvent:
    def test_required_fields_present(self):
        evt = build_trace_event(
            step=1,
            kind=TraceEventKind.STATUS,
            label="starting",
            status=TraceEventStatus.PENDING,
        )
        for field in ("id", "step", "kind", "label", "status", "detail", "source", "ts"):
            assert field in evt

    def test_id_prefix(self):
        evt = build_trace_event(step=0, kind="status", label="x", status="pending")
        assert str(evt["id"]).startswith("trace-")

    def test_tool_name_optional(self):
        evt = build_trace_event(step=1, kind="tool_call", label="t", status="completed")
        assert "tool_name" not in evt

        evt2 = build_trace_event(step=1, kind="tool_call", label="t", status="completed", tool_name="search")
        assert evt2["tool_name"] == "search"

    def test_duration_ms_optional(self):
        evt = build_trace_event(step=1, kind="status", label="x", status="completed")
        assert "duration_ms" not in evt

        evt2 = build_trace_event(step=1, kind="status", label="x", status="completed", duration_ms=55)
        assert evt2["duration_ms"] == 55

    def test_string_kind_and_status_accepted(self):
        evt = build_trace_event(step=5, kind="finalize", label="done", status="completed")
        assert evt["kind"] == "finalize"
        assert evt["status"] == "completed"

    def test_enum_kind_and_status(self):
        evt = build_trace_event(
            step=2,
            kind=TraceEventKind.TOOL_RESULT,
            label="result",
            status=TraceEventStatus.ERROR,
        )
        assert evt["kind"] == "tool_result"
        assert evt["status"] == "error"


class TestAppendToolCallTraces:
    def _make_tool_call(self, name="search", status="completed", summary="ok", alias_name=""):
        return {"name": name, "status": status, "summary": summary, "alias_name": alias_name}

    def test_appends_events_for_each_tool_call(self):
        trace: list[dict] = []
        calls = [self._make_tool_call("a"), self._make_tool_call("b")]
        next_idx, next_step = append_tool_call_traces(trace, calls, start_index=0, step_counter=1)
        assert len(trace) == 2
        assert next_idx == 2
        assert next_step == 3

    def test_start_index_respected(self):
        trace: list[dict] = []
        calls = [self._make_tool_call("a"), self._make_tool_call("b"), self._make_tool_call("c")]
        next_idx, next_step = append_tool_call_traces(trace, calls, start_index=1, step_counter=0)
        assert len(trace) == 2  # only b and c
        assert next_idx == 3

    def test_alias_name_included_in_label(self):
        trace: list[dict] = []
        calls = [self._make_tool_call("mcp__nemo__search_memories", alias_name="search_memories")]
        append_tool_call_traces(trace, calls, start_index=0, step_counter=0)
        label = str(trace[0]["label"])
        assert "alias" in label
        assert "search_memories" in label

    def test_empty_calls_no_op(self):
        trace: list[dict] = []
        next_idx, next_step = append_tool_call_traces(trace, [], start_index=0, step_counter=5)
        assert trace == []
        assert next_idx == 0
        assert next_step == 5

    def test_event_kind_is_tool_result(self):
        trace: list[dict] = []
        append_tool_call_traces(trace, [self._make_tool_call()], start_index=0, step_counter=0)
        assert trace[0]["kind"] == "tool_result"
