from __future__ import annotations

import json
from typing import Any


PLATFORM_COMMAND = "python -m nemo_code_runtime.nemo_platform"
NEMO_TOOLS_BY_PHASE = {
    "plan": ["prime_context", "build_context_portfolio", "search_memories"],
    "build": ["build_context_portfolio", "search_memories", "expand_context_evidence", "record_context_feedback"],
    "review": ["record_context_feedback", "store_conversation", "compress_context_artifact"],
}


def platform_info() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "product_base": "nemo_code",
        "extension_package": "nemo_code_runtime.nemo_platform",
        "command": {
            "entrypoint": PLATFORM_COMMAND,
            "output": "json",
            "stable_schema": True,
        },
        "capabilities": {
            "full_handoff": True,
            "permission_gates": True,
            "nemo_memory_plane": True,
            "audit_events": True,
            "supported_isolation": ["worktree", "container"],
        },
        "autonomy": {},
        "permissions": [],
        "nemo_tools": [],
        "nemo_tools_by_phase": NEMO_TOOLS_BY_PHASE,
        "audit_event_capabilities": {
            "event_types": ["context_bootstrapped", "mutation_created", "validation_run", "checkpoint"],
            "risk_flags": ["validation_failure", "no_changed_files"],
            "default_checkpoint_minutes": 15,
        },
    }


def platform_info_json(indent: int | None = 2) -> str:
    return json.dumps(platform_info(), indent=indent, sort_keys=True)


def main() -> None:
    print(platform_info_json())


if __name__ == "__main__":
    main()
