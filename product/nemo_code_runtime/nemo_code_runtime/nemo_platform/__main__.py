from __future__ import annotations

import argparse
import json
import os
import sys

from nemo_code_runtime.nemo_platform.platform_info import platform_info_json


PHASE_CHOICES = ["plan", "build", "review"]


def main() -> None:
    parser = argparse.ArgumentParser(description="NEMO CODE runtime platform CLI")
    subparsers = parser.add_argument_group("Commands").add_subparsers(dest="command")

    subparsers.add_parser("info", help="Display platform information")

    call_parser = subparsers.add_parser("call", help="Call a NEMO tool")
    call_parser.add_argument("tool", help="Name of the tool to call")
    call_parser.add_argument("--args", help="JSON string of arguments", default="{}")
    call_parser.add_argument("--phase", help="Current phase", choices=PHASE_CHOICES, default="build")

    args = parser.parse_args()

    if args.command == "info":
        print(platform_info_json())
        return

    if args.command == "call":
        try:
            tool_args = json.loads(args.args)
        except json.JSONDecodeError:
            print(f"Error: Invalid JSON arguments: {args.args}", file=sys.stderr)
            raise SystemExit(1)

        db_path = os.environ.get("NEMO_DB_PATH")
        if not db_path:
            print("Error: NEMO_DB_PATH environment variable not set", file=sys.stderr)
            raise SystemExit(1)

        try:
            from nemo_coding_platform.core.memory_persistence import PersistentMemoryStore
            from nemo_coding_platform.core.nemo_adapter import PersistentNemoAdapter
            from nemo_coding_platform.core.nemo_lifecycle import NemoLifecyclePhase

            store = PersistentMemoryStore(db_path)
            adapter = PersistentNemoAdapter(store)
            phase_map = {
                "plan": NemoLifecyclePhase.PLAN,
                "build": NemoLifecyclePhase.BUILD,
                "review": NemoLifecyclePhase.REVIEW,
            }
            _, result = adapter.call(phase_map.get(args.phase, NemoLifecyclePhase.BUILD), args.tool, **tool_args)
            print(json.dumps(result.payload, indent=2))
            if not result.ok:
                raise SystemExit(1)
        except ImportError:
            print("Error: nemo_coding_platform not found in PYTHONPATH", file=sys.stderr)
            raise SystemExit(1)
        except Exception as error:
            print(f"Error calling tool {args.tool}: {error}", file=sys.stderr)
            raise SystemExit(1)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
