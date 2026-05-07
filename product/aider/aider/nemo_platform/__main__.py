from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from aider.nemo_platform.nemo_tools import NEMO_TOOLS, AgentPhase
from aider.nemo_platform.platform_info import platform_info_json


def main():
    parser = argparse.ArgumentParser(description="NEMO Platform CLI for Aider")
    subparsers = parser.add_argument_group("Commands").add_subparsers(dest="command")

    # Info command
    subparsers.add_parser("info", help="Display platform information")

    # Call command
    call_parser = subparsers.add_parser("call", help="Call a NEMO tool")
    call_parser.add_argument("tool", help="Name of the tool to call")
    call_parser.add_argument("--args", help="JSON string of arguments", default="{}")
    call_parser.add_argument("--phase", help="Current phase", choices=["plan", "build", "review"], default="build")

    args = parser.parse_args()

    if args.command == "info":
        print(platform_info_json())
        return

    if args.command == "call":
        tool_name = args.tool
        try:
            tool_args = json.loads(args.args)
        except json.JSONDecodeError:
            print(f"Error: Invalid JSON arguments: {args.args}", file=sys.stderr)
            sys.exit(1)

        # In a real implementation, this would use an adapter to talk to NEMO.
        # For now, we will try to use the PersistentNemoAdapter if NEMO_DB_PATH is set.
        db_path = os.environ.get("NEMO_DB_PATH")
        if not db_path:
            print("Error: NEMO_DB_PATH environment variable not set", file=sys.stderr)
            sys.exit(1)

        try:
            # We need to import the core platform to use the adapter.
            # This assumes the परियोजना/src is in PYTHONPATH.
            from nemo_coding_platform.core.memory_persistence import PersistentMemoryStore
            from nemo_coding_platform.core.nemo_adapter import PersistentNemoAdapter
            from nemo_coding_platform.core.nemo_lifecycle import NemoLifecyclePhase

            store = PersistentMemoryStore(db_path)
            adapter = PersistentNemoAdapter(store)
            
            phase_map = {
                "plan": NemoLifecyclePhase.PLAN,
                "build": NemoLifecyclePhase.BUILD,
                "review": NemoLifecyclePhase.REVIEW
            }
            phase = phase_map.get(args.phase, NemoLifecyclePhase.BUILD)

            _, result = adapter.call(phase, tool_name, **tool_args)
            print(json.dumps(result.payload, indent=2))
            if not result.ok:
                sys.exit(1)
        except ImportError:
            print("Error: nemo_coding_platform not found in PYTHONPATH", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"Error calling tool {tool_name}: {str(e)}", file=sys.stderr)
            sys.exit(1)
        return

    parser.print_help()


if __name__ == "__main__":
    main()