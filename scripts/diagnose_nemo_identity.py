"""
Diagnostic: call NEMO stdio directly and dump what search_memories returns
for identity queries. Run from the repo root:

    python scripts/diagnose_nemo_identity.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from nemo_coding_platform.core.vscode_mcp_config import discover_vscode_mcp_server
from nemo_coding_platform.core.nemo_adapter import _call_mcp_stdio_tool_once


def _call(tool_name: str, **args: object) -> dict:
    config = discover_vscode_mcp_server("nemo")
    if config is None:
        return {"error": "NEMO MCP server not found in mcp.json"}
    try:
        return _call_mcp_stdio_tool_once(
            command=config.command,
            args=config.args,
            cwd=config.cwd,
            env=config.env,
            tool_name=tool_name,
            arguments=args,
            timeout_seconds=30.0,
        )
    except Exception as exc:
        return {"error": str(exc)}


def main() -> None:
    print("=== NEMO MCP stdio diagnostic ===\n")

    queries = [
        "nombre del usuario",
        "user identity name",
        "mi nombre es",
        "user_name",
        "Gabriel",
    ]

    for q in queries:
        print(f"--- search_memories(query={q!r}, compact=False, database_filter=all) ---")
        result = _call("search_memories", query=q, limit=5, compact=False, database_filter="all")
        print(json.dumps(result, ensure_ascii=False, indent=2)[:800])
        print()

    print("--- context_bootstrap(topic='Mission Control conversation', task='test') ---")
    result = _call("context_bootstrap", topic="Mission Control conversation", task="quién soy?", token_budget=800, limit=8)
    ctx = result.get("context") or result.get("prime_context") or "(no context field)"
    print("context field:", str(ctx)[:600])
    print()


if __name__ == "__main__":
    main()
