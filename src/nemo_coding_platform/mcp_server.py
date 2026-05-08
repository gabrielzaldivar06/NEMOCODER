from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from typing import Any
from datetime import datetime, timezone

from nemo_coding_platform.core.memory import NEMO_TOOL_REGISTRY
from nemo_coding_platform.core.nemo_lifecycle import lifecycle_contract, NemoLifecyclePhase
from nemo_coding_platform.nemocode_mcp_tools import mcp_call_nemo_tool, mcp_get_self_mod_continuity, mcp_learn_from_self_mod_failure, mcp_mark_portfolio_effective, mcp_query_self_mod_risk_patterns, mcp_record_self_mod_decision, mcp_record_self_mod_feedback, mcp_run_headless, mcp_list_runs, mcp_get_run_result, mcp_prime_context, mcp_build_context_portfolio, mcp_self_modify, mcp_self_mod_status, mcp_self_mod_review, mcp_self_mod_apply, mcp_self_mod_rollback, mcp_self_mod_trajectory, mcp_self_mod_impact, mcp_self_mod_similar_runs


NEMOCODE_TOOL_PREFIX = "nemocode."
NEMO_TOOLS_BY_NAME = {tool.name: tool for tool in NEMO_TOOL_REGISTRY}

# Static tools not represented in NEMO_TOOL_REGISTRY still need explicit governance.
STATIC_TOOL_RISK: dict[str, str] = {
    "nemocode.run_headless": "memory_write",
    "nemocode.self_modify": "destructive",
    "nemocode.self_mod_apply": "destructive",
    "nemocode.self_mod_rollback": "destructive",
    "nemocode.record_self_mod_decision": "memory_write",
    "nemocode.record_self_mod_feedback": "memory_write",
    "nemocode.learn_from_self_mod_failure": "memory_write",
    "nemocode.mark_portfolio_effective": "memory_write",
}


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    if isinstance(value, (int, float)):
        return value != 0
    return False


def _tool_risk(tool_name: str) -> str:
    if not isinstance(tool_name, str):
        return "unknown"
    if tool_name in STATIC_TOOL_RISK:
        return STATIC_TOOL_RISK[tool_name]
    if tool_name.startswith(NEMOCODE_TOOL_PREFIX):
        logical_name = tool_name.removeprefix(NEMOCODE_TOOL_PREFIX)
        tool = NEMO_TOOLS_BY_NAME.get(logical_name)
        if tool:
            return tool.risk.value
    return "unknown"


def _approval_required(risk: str) -> bool:
    # Scheduling and destructive actions are always review-gated.
    # Unknown risk is conservative and requires explicit approval.
    return risk in {"scheduling_write", "destructive", "unknown"}


def tool_policy_decision(tool_name: str, tool_args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = tool_args if isinstance(tool_args, dict) else {}
    risk = _tool_risk(tool_name)
    approval_required = _approval_required(risk)
    approved = _truthy(args.get("approve_review"))
    allowed = (not approval_required) or approved
    reason = "allowed"
    if approval_required and not approved:
        reason = (
            f"approval required for risky MCP action: tool={tool_name} risk={risk}. "
            "Pass approve_review=true to proceed."
        )
    return {
        "tool": tool_name,
        "risk": risk,
        "approval_required": approval_required,
        "approved": approved,
        "allowed": allowed,
        "reason": reason,
    }


def _audit_log_path() -> Path:
    configured = os.environ.get("NEMOCODE_MCP_AUDIT_LOG", ".nemo-runtimes/mcp/tool-audit.jsonl")
    return Path(configured)


def _persist_tool_call_audit(request_id: Any, decision: dict[str, Any], outcome: str) -> None:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "request_id": request_id,
        "outcome": outcome,
        "tool_call_audit": decision,
    }
    path = _audit_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True) + "\n")
    except OSError:
        # Audit write failures must not block MCP request handling.
        return


class MCPServerHandler(BaseHTTPRequestHandler):
    # Map of tool names to their implementations
    TOOLS = {
        "nemocode.run_headless": mcp_run_headless,
        "nemocode.list_runs": mcp_list_runs,
        "nemocode.get_run_result": mcp_get_run_result,
        "nemocode.prime_context": mcp_prime_context,
        "nemocode.build_context_portfolio": mcp_build_context_portfolio,
        "nemocode.self_modify": mcp_self_modify,
        "nemocode.self_mod_status": mcp_self_mod_status,
        "nemocode.self_mod_review": mcp_self_mod_review,
        "nemocode.self_mod_apply": mcp_self_mod_apply,
        "nemocode.self_mod_rollback": mcp_self_mod_rollback,
        "nemocode.self_mod_trajectory": mcp_self_mod_trajectory,
        "nemocode.self_mod_impact": mcp_self_mod_impact,
        "nemocode.self_mod_similar_runs": mcp_self_mod_similar_runs,
        "nemocode.record_self_mod_decision": mcp_record_self_mod_decision,
        "nemocode.record_self_mod_feedback": mcp_record_self_mod_feedback,
        "nemocode.learn_from_self_mod_failure": mcp_learn_from_self_mod_failure,
        "nemocode.mark_portfolio_effective": mcp_mark_portfolio_effective,
        "nemocode.get_self_mod_continuity": mcp_get_self_mod_continuity,
        "nemocode.query_self_mod_risk_patterns": mcp_query_self_mod_risk_patterns,
    }
    NEMO_TOOL_NAMES = {f"{NEMOCODE_TOOL_PREFIX}{tool.name}" for tool in NEMO_TOOL_REGISTRY}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/mcp/sse":
            self._handle_sse()
        else:
            self.send_error(404)

    def do_POST(self):
        url = urlparse(self.path)
        if url.path == "/mcp/messages":
            self._handle_message()
        else:
            self.send_error(404)

    def _handle_sse(self):
        """Establish SSE connection and send the endpoint URI."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        # Send the "endpoint" event so the client knows where to POST messages
        # In this simple implementation, we use /mcp/messages
        endpoint_uri = "/mcp/messages"
        self.wfile.write(f"event: endpoint\ndata: {endpoint_uri}\n\n".encode("utf-8"))
        self.wfile.flush()

        # Keep connection open (minimal heartbeat)
        try:
            while True:
                time.sleep(30)
                self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
        except (ConnectionResetError, BrokenPipeError):
            pass

    def _handle_message(self):
        """Handle JSON-RPC messages from the client."""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")
        
        try:
            request = json.loads(body)
            request_id = request.get("id")
            method = request.get("method")
            params = request.get("params", {})

            if method == "initialize":
                response = self._json_rpc_success(request_id, {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {"listChanged": True}
                    },
                    "serverInfo": {"name": "NEMOCODE", "version": "1.0.0"}
                })
            elif method == "tools/list":
                response = self._json_rpc_success(request_id, {"tools": mcp_tool_definitions()})
            elif method == "tools/call":
                tool_name = params.get("name")
                tool_args = params.get("arguments", {})
                decision = tool_policy_decision(str(tool_name or ""), tool_args if isinstance(tool_args, dict) else {})
                if not decision["allowed"]:
                    _persist_tool_call_audit(request_id, decision, "denied")
                    response = self._json_rpc_error(request_id, -32003, str(decision["reason"]), data={"tool_call_audit": decision})
                elif tool_name in self.TOOLS:
                    result = self.TOOLS[tool_name](**tool_args)
                    payload = {
                        "ok": True,
                        "tool_call_audit": decision,
                        "result": result,
                    }
                    _persist_tool_call_audit(request_id, decision, "allowed")
                    response = self._json_rpc_success(request_id, {
                        "content": [{"type": "text", "text": json.dumps(payload, indent=2)}]
                    })
                elif tool_name in self.NEMO_TOOL_NAMES:
                    nemo_tool_name = tool_name.removeprefix(NEMOCODE_TOOL_PREFIX)
                    result = mcp_call_nemo_tool(nemo_tool_name, **tool_args)
                    payload = {
                        "ok": True,
                        "tool_call_audit": decision,
                        "result": result,
                    }
                    _persist_tool_call_audit(request_id, decision, "allowed")
                    response = self._json_rpc_success(request_id, {
                        "content": [{"type": "text", "text": json.dumps(payload, indent=2)}]
                    })
                else:
                    _persist_tool_call_audit(request_id, decision, "not_found")
                    response = self._json_rpc_error(request_id, -32601, f"Tool not found: {tool_name}")
            else:
                # Silently ignore notifications or unsupported methods for now
                response = None

            if response:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps(response).encode("utf-8"))
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            print(f"Error handling MCP message: {e}")

    def _json_rpc_success(self, request_id: Any, result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _json_rpc_error(self, request_id: Any, code: int, message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        error: dict[str, Any] = {"code": code, "message": message}
        if isinstance(data, dict) and data:
            error["data"] = data
        return {"jsonrpc": "2.0", "id": request_id, "error": error}


def mcp_tool_definitions() -> list[dict[str, Any]]:
    static_tools = [
        {
            "name": "nemocode.run_headless",
            "description": "Run an autonomous coding task in a sandbox.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "objective": {"type": "string"},
                    "repo_path": {"type": "string"},
                    "target_files": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["objective", "repo_path"],
            },
        },
        {
            "name": "nemocode.list_runs",
            "description": "List all previous autonomous runs.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "nemocode.get_run_result",
            "description": "Retrieve a persisted autonomous run result JSON.",
            "inputSchema": {"type": "object", "properties": {"source_json": {"type": "string"}}, "required": ["source_json"]},
        },
        {
            "name": "nemocode.self_modify",
            "description": "Run a controlled self-modification task against NEMOCODE itself in an isolated runtime.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "task_type": {"type": "string", "enum": ["bug_fix", "test_coverage", "refactor", "tool_expansion", "documentation", "architecture_hardening"]},
                    "target_files": {"type": "array", "items": {"type": "string"}},
                    "validation_policy": {"type": "string", "enum": ["none", "smoke", "targeted", "full"]},
                    "repair_budget": {"type": "integer"},
                    "memory_db": {"type": "string"},
                    "repo_root": {"type": "string"},
                    "provider_mode": {"type": "string", "enum": ["fake", "subprocess"]},
                    "bounded_simulation": {"type": "boolean"},
                    "real_validation": {"type": "boolean"},
                },
                "required": ["description"],
            },
        },
        {
            "name": "nemocode.self_mod_status",
            "description": "Read status for a persisted self-modification run JSON.",
            "inputSchema": {"type": "object", "properties": {"run_json": {"type": "string"}}, "required": ["run_json"]},
        },
        {
            "name": "nemocode.self_mod_review",
            "description": "Build a review plan and risk report for a self-modification run.",
            "inputSchema": {
                "type": "object",
                "properties": {"run_json": {"type": "string"}, "permissions_file": {"type": "string"}},
                "required": ["run_json"],
            },
        },
        {
            "name": "nemocode.self_mod_apply",
            "description": "Apply an approved and mergeable self-modification run.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_json": {"type": "string"},
                    "approve_review": {"type": "boolean"},
                    "backup_dir": {"type": "string"},
                    "permissions_file": {"type": "string"},
                    "autonomy_profile": {"type": "string", "enum": ["manual", "trusted", "aggressive"]},
                    "memory_db": {"type": "string"},
                    "portfolio_id": {"type": "string"},
                },
                "required": ["run_json"],
            },
        },
        {
            "name": "nemocode.self_mod_rollback",
            "description": "Rollback a self-modification apply result after approval.",
            "inputSchema": {
                "type": "object",
                "properties": {"apply_json": {"type": "string"}, "approve_review": {"type": "boolean"}},
                "required": ["apply_json", "approve_review"],
            },
        },
        {
            "name": "nemocode.self_mod_trajectory",
            "description": "Build a replayable trajectory for a self-modification run.",
            "inputSchema": {"type": "object", "properties": {"run_json": {"type": "string"}}, "required": ["run_json"]},
        },
        {
            "name": "nemocode.self_mod_impact",
            "description": "Analyze changed surfaces, suggested validation, and risks for a self-modification run.",
            "inputSchema": {"type": "object", "properties": {"run_json": {"type": "string"}}, "required": ["run_json"]},
        },
        {
            "name": "nemocode.self_mod_similar_runs",
            "description": "Retrieve similar self-modification trajectories from NEMO memory.",
            "inputSchema": {
                "type": "object",
                "properties": {"memory_db": {"type": "string"}, "query": {"type": "string"}, "limit": {"type": "integer"}},
            },
        },
        {
            "name": "nemocode.record_self_mod_decision",
            "description": "Persist a self-modification strategy decision into NEMO memory.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "memory_db": {"type": "string"},
                    "objective": {"type": "string"},
                    "chosen_path": {"type": "string"},
                    "rationale": {"type": "string"},
                    "alternatives": {"type": "array", "items": {"type": "string"}},
                    "task_type": {"type": "string"},
                    "run_json": {"type": "string"},
                    "task_id": {"type": "string"},
                    "run_id": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "importance": {"type": "integer"},
                },
                "required": ["memory_db", "objective", "chosen_path", "rationale"],
            },
        },
        {
            "name": "nemocode.record_self_mod_feedback",
            "description": "Persist review/apply feedback for a self-modification run.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "memory_db": {"type": "string"},
                    "run_json": {"type": "string"},
                    "approved": {"type": "boolean"},
                    "issues_found": {"type": "array", "items": {"type": "string"}},
                    "follow_up_work": {"type": "array", "items": {"type": "string"}},
                    "portfolio_id": {"type": "string"},
                    "atom_ids": {"type": "array", "items": {"type": "string"}},
                    "evidence_handle": {"type": "string"},
                    "token_delta": {"type": "integer"},
                },
                "required": ["memory_db", "run_json", "approved"],
            },
        },
        {
            "name": "nemocode.learn_from_self_mod_failure",
            "description": "Convert a failed self-modification run into correction and risk-pattern memory.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "memory_db": {"type": "string"},
                    "run_json": {"type": "string"},
                    "failure_pattern": {"type": "string"},
                    "suggested_correction": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["memory_db", "run_json", "failure_pattern", "suggested_correction"],
            },
        },
        {
            "name": "nemocode.mark_portfolio_effective",
            "description": "Record that a self-modification context portfolio helped a run succeed.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "memory_db": {"type": "string"},
                    "portfolio_id": {"type": "string"},
                    "task_type": {"type": "string"},
                    "effectiveness_score": {"type": "integer"},
                    "context_savings": {"type": "integer"},
                    "run_json": {"type": "string"},
                },
                "required": ["memory_db", "portfolio_id"],
            },
        },
        {
            "name": "nemocode.get_self_mod_continuity",
            "description": "Retrieve prior self-modification attempts and lessons for an objective.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "memory_db": {"type": "string"},
                    "task_objective": {"type": "string"},
                    "task_type": {"type": "string"},
                    "limit": {"type": "integer"},
                    "include_abandoned": {"type": "boolean"},
                },
                "required": ["memory_db"],
            },
        },
        {
            "name": "nemocode.query_self_mod_risk_patterns",
            "description": "Retrieve learned risk patterns for self-modification files or modules.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "memory_db": {"type": "string"},
                    "file_or_module": {"type": "string"},
                    "risk_category": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["memory_db"],
            },
        },
    ]
    existing = {tool["name"] for tool in static_tools}
    dynamic_tools = [_nemo_tool_definition(tool) for tool in NEMO_TOOL_REGISTRY if f"{NEMOCODE_TOOL_PREFIX}{tool.name}" not in existing]
    return static_tools + dynamic_tools


def _nemo_tool_definition(tool: Any) -> dict[str, Any]:
    allowed_lifecycle_phases = [phase.value for phase in NemoLifecyclePhase if tool.name in lifecycle_contract(phase).tool_names]
    risk = tool.risk.value
    return {
        "name": f"{NEMOCODE_TOOL_PREFIX}{tool.name}",
        "description": tool.purpose,
        "annotations": {
            "risk": risk,
            "approval_required": _approval_required(risk),
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "lifecycle_phase": {"type": "string", "enum": allowed_lifecycle_phases},
                "memory_db": {"type": "string"},
                "approve_review": {"type": "boolean"},
            },
            "additionalProperties": True,
        },
    }


def run_mcp_server(host: str = "127.0.0.1", port: int = 8765):
    server = ThreadingHTTPServer((host, port), MCPServerHandler)
    print(f"NEMOCODE MCP Server listening on http://{host}:{port}/mcp/sse")
    server.serve_forever()


if __name__ == "__main__":
    run_mcp_server()
