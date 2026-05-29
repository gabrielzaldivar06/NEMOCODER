"""NemoGateway — single point of entry for all NEMO MCP operations.

Replaces the scattered _nemo_configured() checks and _nemo_chat_tool_call()
copies across mission_control_server.py.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

try:
    from nemo_coding_platform.spacecode_mcp_tools import mcp_call_nemo_tool as _mcp_call_nemo_tool  # type: ignore
except ImportError:
    _mcp_call_nemo_tool = None  # type: ignore[assignment]


class NemoGateway:
    """Owns NEMO availability logic and wraps mcp_call_nemo_tool."""

    def __init__(
        self,
        memory_db: "Path | str | None",
        mcp_url: "str | None",
        allowed_tools: "set[str] | None" = None,
    ) -> None:
        self.memory_db = memory_db
        self.mcp_url = (mcp_url or "").strip()
        self.allowed_tools = allowed_tools

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------

    def is_configured(self) -> bool:
        """True if at least one NEMO backend is reachable."""
        return bool(self.memory_db) or bool(self.mcp_url)

    # ------------------------------------------------------------------
    # Tool call
    # ------------------------------------------------------------------

    def call(
        self,
        tool_name: str,
        lifecycle_phase: str,
        tool_calls: list[dict[str, Any]],
        **arguments: Any,
    ) -> dict[str, Any]:
        """Invoke a NEMO tool and append an audit entry to tool_calls."""
        canonical = f"nemo_memory.{tool_name}"
        alias = f"spacecode.{tool_name}"

        if isinstance(self.allowed_tools, set) and tool_name not in self.allowed_tools:
            tool_calls.append({
                "id": f"tool-{uuid4().hex[:8]}",
                "name": canonical,
                "tool_name": tool_name,
                "alias_name": alias,
                "status": "skipped",
                "summary": f"Tool {tool_name!r} disabled for this session by MCP tool selector.",
            })
            return {}

        if not self.is_configured():
            tool_calls.append({
                "id": f"tool-{uuid4().hex[:8]}",
                "name": canonical,
                "tool_name": tool_name,
                "alias_name": alias,
                "status": "skipped",
                "summary": "NEMO not configured (no memory_db and no mcp_url).",
            })
            return {}

        if _mcp_call_nemo_tool is None:
            raise ImportError(
                "nemo_coding_platform.spacecode_mcp_tools is not installed — "
                "cannot call NEMO MCP tools."
            )
        mcp_call_nemo_tool = _mcp_call_nemo_tool

        result = mcp_call_nemo_tool(
            tool_name,
            lifecycle_phase=lifecycle_phase,
            memory_db=str(self.memory_db) if self.memory_db else "",
            mcp_url=self.mcp_url,
            approve_review=bool(self.mcp_url),
            **arguments,
        )
        ok = bool(result.get("ok"))
        payload = self._normalize(result)
        if ok:
            summary = self._summarize(tool_name, payload)
        else:
            # Surface the actual error so the chat UI shows '1 fallida → reason'
            # instead of a misleading 'ok' summary. Look in payload.error first
            # (NEMO's structured error path), then result.error (transport error),
            # then fall back to a generic message.
            err = ""
            if isinstance(payload, dict):
                err = str(payload.get("error") or "")
            if not err:
                err = str(result.get("error") or "")
            if not err:
                # mcp_call_nemo_tool may return ok=False because the inner content
                # had isError=true. The text usually starts with "Error: ...".
                err = "unknown error from NEMO MCP"
            summary = f"{tool_name} failed: {err[:200]}"
        tool_calls.append({
            "id": f"tool-{uuid4().hex[:8]}",
            "name": canonical,
            "tool_name": tool_name,
            "alias_name": alias,
            "status": "completed" if ok else "failed",
            "summary": summary,
        })
        return payload if ok else {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize(result: dict[str, Any]) -> dict[str, Any]:
        outer = result.get("payload") if isinstance(result.get("payload"), dict) else {}
        if not outer:
            return {}
        nested_result = outer.get("result") if isinstance(outer.get("result"), dict) else None
        if nested_result is None:
            return outer
        nested_payload = nested_result.get("payload") if isinstance(nested_result.get("payload"), dict) else None
        return nested_payload if nested_payload is not None else nested_result

    @staticmethod
    def _summarize(tool_name: str, payload: dict[str, Any]) -> str:
        if tool_name == "context_bootstrap":
            memories = payload.get("memories_retrieved", payload.get("memories_loaded", "?"))
            return f"context_bootstrap: {memories} memories loaded"
        if tool_name == "search_memories":
            results = payload.get("results", payload.get("memories", []))
            user_name = ""
            if isinstance(results, list) and results:
                first = results[0]
                if isinstance(first, dict):
                    raw_name = first.get("user_name")
                    if isinstance(raw_name, str) and raw_name.strip():
                        user_name = raw_name.strip()
            suffix = f" user_name={user_name}." if user_name else "."
            return f"Searched memory; matches={len(results)}{suffix}"
        if tool_name in ("cognitive_ingest", "create_memory"):
            return f"{tool_name}: stored"
        return f"{tool_name}: ok" if payload else f"{tool_name}: no payload"
