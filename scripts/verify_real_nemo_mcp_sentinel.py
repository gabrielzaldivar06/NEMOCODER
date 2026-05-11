from __future__ import annotations

import argparse
import json
import time
import urllib.request
from typing import Any

from nemo_coding_platform.nemocode_mcp_tools import mcp_call_nemo_tool


def normalize_nemo_payload(result: dict[str, Any]) -> dict[str, Any]:
    outer = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    nested_result = outer.get("result") if isinstance(outer.get("result"), dict) else None
    if nested_result is None:
        return outer
    nested_payload = nested_result.get("payload") if isinstance(nested_result.get("payload"), dict) else None
    return nested_payload if nested_payload is not None else nested_result


def post_agent(api_url: str, mcp_url: str, message: str, selected_tools: list[str]) -> dict[str, Any]:
    body = {
        "message": message,
        "provider": "subprocess",
        "nemo_mcp_url": mcp_url,
        "require_nemo_mcp_capabilities": True,
        "require_nemo_roundtrip": True,
        "selected_nemo_tools": selected_tools,
    }
    request = urllib.request.Request(
        api_url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def tool_names(payload: dict[str, Any]) -> list[str]:
    return [str(tool.get("name")) for tool in payload.get("message", {}).get("tool_calls", [])]


def neutralize_sentinel(mcp_url: str, sentinel: str) -> list[tuple[str, bool]]:
    result = mcp_call_nemo_tool("search_memories", mcp_url=mcp_url, query=sentinel, limit=20, compact=False)
    payload = normalize_nemo_payload(result) if result.get("ok") else {}
    memories = payload.get("memories") if isinstance(payload.get("memories"), list) else []
    updated: list[tuple[str, bool]] = []
    for memory in memories:
        memory_id = memory.get("id") or memory.get("memory_id")
        content = str(memory.get("content") or "")
        if not memory_id or sentinel not in content:
            continue
        update = mcp_call_nemo_tool(
            "update_memory",
            mcp_url=mcp_url,
            memory_id=memory_id,
            content="TEST CLEANUP / IGNORE: live real MCP sentinel neutralized after verification.",
            importance_level=1,
            tags=["test-cleanup", "discarded", "ignore", "real-mcp-sentinel"],
            metadata={"topic": "NEMOCODE cleanup/test-discarded"},
        )
        updated.append((str(memory_id), bool(update.get("ok"))))
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Mission Control uses real NEMO MCP with a write/read sentinel.")
    parser.add_argument("--api-url", default="http://127.0.0.1:8787/api/agent/message")
    parser.add_argument("--mcp-url", default="http://127.0.0.1:8765/mcp/sse")
    parser.add_argument("--cleanup-prefix", default="NEMOCODE_REAL_MCP_SENTINEL")
    args = parser.parse_args()

    stamp = time.strftime("%Y%m%d%H%M%S")
    sentinel = f"{args.cleanup_prefix}_{stamp}"
    fact = f"FACTO_VERIFICACION_MCP_{stamp}"
    print(f"SENTINEL={sentinel}")
    print(f"FACT={fact}")

    store = post_agent(
        args.api_url,
        args.mcp_url,
        f"guarda en NEMO MCP que {sentinel} significa {fact}",
        selected_tools=[
            "context_bootstrap",
            "prime_context",
            "build_context_portfolio",
            "search_memories",
            "anticipate",
            "store_conversation",
            "cognitive_ingest",
        ],
    )
    print("STORE_RESPONSE=" + str(store.get("message", {}).get("content", "")))
    print("STORE_TOOLS=" + ",".join(tool_names(store)))

    lookup = post_agent(
        args.api_url,
        args.mcp_url,
        f"BUSCA EN NEMO MCP QUE ES {sentinel}, EN LAS MEMORIAS",
        selected_tools=[
            "context_bootstrap",
            "prime_context",
            "build_context_portfolio",
            "search_memories",
            "anticipate",
            "store_conversation",
        ],
    )
    response_text = str(lookup.get("message", {}).get("content", ""))
    lookup_json = json.dumps(lookup, ensure_ascii=False)
    print("LOOKUP_RESPONSE=" + response_text)
    print("LOOKUP_TOOLS=" + ",".join(tool_names(lookup)))

    failures: list[str] = []
    if fact not in response_text:
        failures.append(f"lookup response did not include sentinel fact {fact}")
    if "nemo_memory.search_memories" not in lookup_json:
        failures.append("lookup did not call nemo_memory.search_memories")
    if "mission_control.verified_nemo_answer" not in lookup_json:
        failures.append("lookup did not use mission_control.verified_nemo_answer")
    if "lmstudio.chat_completions" in lookup_json:
        failures.append("lookup delegated the memory answer to LM Studio")

    cleanup = neutralize_sentinel(args.mcp_url, args.cleanup_prefix)
    print("CLEANUP_UPDATED=" + repr(cleanup))

    if failures:
        print("FAIL_REAL_MCP_SENTINEL_TEST")
        for failure in failures:
            print("FAILURE=" + failure)
        return 1
    print("PASS_REAL_MCP_SENTINEL_TEST")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())