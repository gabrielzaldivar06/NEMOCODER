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


def _make_job(**kwargs):
    from nemo_coding_platform.mission_control_server import HandoffJob
    defaults = dict(
        job_id="j1", task_id="t1", run_id="r1", run_json="/tmp/x.json",
        status="running", command=(), payload={}, logs=[],
    )
    defaults.update(kwargs)
    return HandoffJob(**defaults)


def test_nemo_event_prefix_detected():
    """NEMO_EVENT: line goes to timeline, not logs."""
    from nemo_coding_platform.mission_control_server import NEMO_EVENT_PREFIX
    import json as _json
    job = _make_job()
    line = f'{NEMO_EVENT_PREFIX}{_json.dumps({"kind": "plan_created", "sequence": 1, "summary": "ok", "phase": "plan"})}'
    if line.startswith(NEMO_EVENT_PREFIX):
        try:
            event = _json.loads(line[len(NEMO_EVENT_PREFIX):])
            job.timeline.append(event)
        except (_json.JSONDecodeError, ValueError):
            pass
    else:
        job.logs.append(line)
    assert len(job.timeline) == 1
    assert job.timeline[0]["kind"] == "plan_created"
    assert len(job.logs) == 0


def test_malformed_json_ignored():
    """NEMO_EVENT: with invalid JSON does not crash and is not added to timeline."""
    from nemo_coding_platform.mission_control_server import NEMO_EVENT_PREFIX
    import json as _json
    job = _make_job()
    line = f"{NEMO_EVENT_PREFIX}not-valid-json"
    if line.startswith(NEMO_EVENT_PREFIX):
        try:
            event = _json.loads(line[len(NEMO_EVENT_PREFIX):])
            job.timeline.append(event)
        except (_json.JSONDecodeError, ValueError):
            pass
    else:
        job.logs.append(line)
    assert len(job.timeline) == 0
    assert len(job.logs) == 0


def test_api_run_timeline_not_found(tmp_path):
    from nemo_coding_platform.mission_control_server import api_run_timeline, HandoffJobManager, MissionControlServerConfig
    manager = HandoffJobManager()
    config = MissionControlServerConfig(
        repo_path=tmp_path, runtimes_path=tmp_path, run_results_path=tmp_path,
        apply_results_path=tmp_path, memory_db=None,
    )
    result = api_run_timeline(config, "nonexistent", manager)
    assert "error" in result


def test_api_run_timeline_returns_events(tmp_path):
    from nemo_coding_platform.mission_control_server import api_run_timeline, HandoffJobManager, MissionControlServerConfig
    manager = HandoffJobManager()
    job = _make_job(job_id="j99")
    job.timeline.append({"kind": "heartbeat", "sequence": 1})
    manager._jobs["j99"] = job
    config = MissionControlServerConfig(
        repo_path=tmp_path, runtimes_path=tmp_path, run_results_path=tmp_path,
        apply_results_path=tmp_path, memory_db=None,
    )
    result = api_run_timeline(config, "j99", manager)
    assert result["job_id"] == "j99"
    assert result["event_count"] == 1
    assert result["timeline"][0]["kind"] == "heartbeat"


def test_supervisor_heartbeat_emitted_per_iteration(capsys):
    from nemo_coding_platform.core.event_emitter import emit_event, reset_sequence
    import json as _json
    reset_sequence()
    emit_event("heartbeat", "Iteration 1 — 0.0 min elapsed", "execute",
               payload={"iteration": 1, "elapsed_minutes": 0.0})
    emit_event("heartbeat", "Iteration 2 — 5.0 min elapsed", "execute",
               payload={"iteration": 2, "elapsed_minutes": 5.0})
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2
    e1 = _json.loads(lines[0][len("NEMO_EVENT:"):])
    e2 = _json.loads(lines[1][len("NEMO_EVENT:"):])
    assert e1["kind"] == "heartbeat"
    assert e1["payload"]["iteration"] == 1
    assert e1["payload"]["elapsed_minutes"] == 0.0
    assert e2["payload"]["iteration"] == 2
    assert e2["payload"]["elapsed_minutes"] == 5.0


def test_supervisor_paused_resumed_events(capsys):
    from nemo_coding_platform.core.event_emitter import emit_event, reset_sequence
    import json as _json
    reset_sequence()
    emit_event("paused", "Paused — resuming in 10 min", "execute",
               payload={"pause_minutes": 10, "elapsed_minutes": 20.0})
    emit_event("resumed", "Resumed — 30.0 min elapsed", "execute",
               payload={"elapsed_minutes": 30.0})
    lines = capsys.readouterr().out.strip().splitlines()
    paused  = _json.loads(lines[0][len("NEMO_EVENT:"):])
    resumed = _json.loads(lines[1][len("NEMO_EVENT:"):])
    assert paused["kind"] == "paused"
    assert paused["payload"]["pause_minutes"] == 10
    assert paused["payload"]["elapsed_minutes"] == 20.0
    assert resumed["kind"] == "resumed"
    assert resumed["payload"]["elapsed_minutes"] == 30.0
    assert resumed["sequence"] > paused["sequence"]
