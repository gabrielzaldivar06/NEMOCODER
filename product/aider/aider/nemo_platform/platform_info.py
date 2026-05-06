from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any

from aider.nemo_platform.audit import AuditEventType, RiskFlag
from aider.nemo_platform.autonomy import AUTONOMY_CONTRACTS
from aider.nemo_platform.nemo_tools import NEMO_TOOLS
from aider.nemo_platform.permissions import default_aider_product_rules
from aider.nemo_platform.phases import AgentPhase


PLATFORM_INFO_SCHEMA_VERSION = 1
PLATFORM_COMMAND = "python -m aider.nemo_platform"


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {key: _json_value(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {_json_value(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    return value


def platform_info() -> dict[str, Any]:
    autonomy = {
        level.value: _json_value(contract)
        for level, contract in sorted(AUTONOMY_CONTRACTS.items(), key=lambda item: item[0].value)
    }
    permissions = [_json_value(rule) for rule in default_aider_product_rules()]
    nemo_tools = [_json_value(tool) for tool in NEMO_TOOLS]
    nemo_tools_by_phase = {
        phase.value: [tool.name for tool in NEMO_TOOLS if phase in tool.phases]
        for phase in AgentPhase
    }

    return {
        "schema_version": PLATFORM_INFO_SCHEMA_VERSION,
        "product_base": "aider",
        "extension_package": "aider.nemo_platform",
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
        "autonomy": autonomy,
        "permissions": permissions,
        "nemo_tools": nemo_tools,
        "nemo_tools_by_phase": nemo_tools_by_phase,
        "audit_event_capabilities": {
            "event_types": [event.value for event in AuditEventType],
            "risk_flags": [flag.value for flag in RiskFlag],
            "default_checkpoint_minutes": 15,
        },
    }


def platform_info_json(indent: int | None = 2) -> str:
    return json.dumps(platform_info(), indent=indent, sort_keys=True)


def main() -> None:
    print(platform_info_json())


if __name__ == "__main__":
    main()