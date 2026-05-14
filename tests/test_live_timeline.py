import json
import pytest


def test_emit_event_writes_nemo_prefix(capsys):
    from nemo_coding_platform.core.event_emitter import emit_event, reset_sequence
    reset_sequence()
    emit_event("plan_created", "Plan ready", "plan")
    captured = capsys.readouterr()
    assert captured.out.startswith("NEMO_EVENT:")
    data = json.loads(captured.out[len("NEMO_EVENT:"):].strip())
    assert data["kind"] == "plan_created"
    assert data["summary"] == "Plan ready"
    assert data["phase"] == "plan"
    assert data["sequence"] == 1
    assert "ts" in data


def test_emit_event_sequence_increments(capsys):
    from nemo_coding_platform.core.event_emitter import emit_event, reset_sequence
    reset_sequence()
    emit_event("heartbeat", "tick", "execute")
    emit_event("heartbeat", "tock", "execute")
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2
    e1 = json.loads(lines[0][len("NEMO_EVENT:"):])
    e2 = json.loads(lines[1][len("NEMO_EVENT:"):])
    assert e1["sequence"] == 1
    assert e2["sequence"] == 2


def test_reset_sequence(capsys):
    from nemo_coding_platform.core.event_emitter import emit_event, reset_sequence
    reset_sequence()
    emit_event("heartbeat", "a", "execute")
    reset_sequence()
    emit_event("heartbeat", "b", "execute")
    lines = capsys.readouterr().out.strip().splitlines()
    seq_b = json.loads(lines[1][len("NEMO_EVENT:"):])["sequence"]
    assert seq_b == 1
