from __future__ import annotations

import json
from datetime import datetime, timezone

_seq: int = 0  # single-threaded use only — runner subprocess, no lock needed


def emit_event(kind: str, summary: str, phase: str, payload: dict | None = None) -> None:
    """Write a NEMO_EVENT line to stdout for the Mission Control server to parse."""
    global _seq
    _seq += 1
    event: dict[str, object] = {
        "kind": kind,
        "summary": summary,
        "phase": phase,
        "sequence": _seq,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    if payload is not None:
        event["payload"] = payload
    print(f"NEMO_EVENT:{json.dumps(event, separators=(',', ':'))}", flush=True)


def reset_sequence() -> None:
    """Reset sequence counter to 0. Call at the start of each run and in tests."""
    global _seq
    _seq = 0
