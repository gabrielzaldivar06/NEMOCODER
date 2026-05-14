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


def test_handoff_job_status_is_str_enum():
    from nemo_coding_platform.mission_control_server import HandoffJobStatus
    assert HandoffJobStatus.RUNNING == "running"
    assert HandoffJobStatus.AWAITING_PERMISSION == "awaiting_permission"
    assert HandoffJobStatus.COMPLETED == "completed"
    assert HandoffJobStatus.FAILED == "failed"
    assert HandoffJobStatus.PERMISSION_DENIED == "permission_denied"


def test_handoff_job_timeline_in_to_dict():
    from nemo_coding_platform.mission_control_server import HandoffJob
    job = HandoffJob(
        job_id="j1", task_id="t1", run_id="r1", run_json="/tmp/x.json",
        status="running", command=(), payload={}, logs=[],
    )
    job.timeline.append({"kind": "plan_created", "summary": "ok", "sequence": 1})
    d = job.to_dict()
    assert d["timeline"] == [{"kind": "plan_created", "summary": "ok", "sequence": 1}]


def test_handoff_job_from_snapshot_loads_timeline():
    from nemo_coding_platform.mission_control_server import HandoffJob
    snap = {
        "job_id": "j1", "task_id": "t1", "run_id": "r1", "run_json": "/tmp/x.json",
        "status": "completed", "command": [], "payload": {}, "logs": [],
        "timeline": [{"kind": "heartbeat", "sequence": 1}],
    }
    job = HandoffJob.from_snapshot(snap)
    assert len(job.timeline) == 1
    assert job.timeline[0]["kind"] == "heartbeat"
