"""Real-time supervision wrapper for execute_headless_handoff."""

import json
import time
from pathlib import Path
from typing import Generator

from nemo_coding_platform.core.contracts import ExecutionPhase, HeartbeatSignal, PauseSignal, SupervisionContext
from nemo_coding_platform.core.headless_handoff import HandoffRequest
from nemo_coding_platform.core.headless_runner import HeadlessRunResult, execute_headless_handoff
from nemo_coding_platform.core.long_handoff_supervisor import LongHandoffBudget


def _load_checkpoint_elapsed_seconds(checkpoint_path: str | Path) -> tuple[ExecutionPhase, float]:
    payload = json.loads(Path(checkpoint_path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("checkpoint payload must be an object")
    snapshot = payload.get("snapshot")
    source = snapshot if isinstance(snapshot, dict) else payload
    phase = ExecutionPhase(str(source.get("phase") or ExecutionPhase.EXECUTE.value))
    elapsed = float(source.get("global_elapsed_seconds") or source.get("elapsed_seconds") or 0.0)
    return phase, elapsed


def _remaining_budget(base: LongHandoffBudget, elapsed_seconds: float) -> LongHandoffBudget:
    remaining_minutes = max(1.0, base.max_runtime_minutes - (elapsed_seconds / 60.0))
    remaining_execute = max(1.0, base.execute_minutes - (elapsed_seconds / 60.0))
    return LongHandoffBudget(
        max_runtime_minutes=remaining_minutes,
        heartbeat_minutes=base.heartbeat_minutes,
        max_heartbeats=base.max_heartbeats,
        token_budget=base.token_budget,
        pause_after_minutes=base.pause_after_minutes,
        plan_minutes=max(1.0, base.plan_minutes),
        execute_minutes=remaining_execute,
        review_minutes=max(1.0, base.review_minutes),
    )


def execute_headless_handoff_supervised(
    request: HandoffRequest,
    budget: LongHandoffBudget | None = None,
    **kwargs,
) -> Generator[HeartbeatSignal | PauseSignal, None, HeadlessRunResult]:
    """Execute headless handoff with real-time supervision and heartbeat/pause signals.
    
    Args:
        request: HandoffRequest to execute
        budget: LongHandoffBudget for time/token control. Defaults to None (use max).
        **kwargs: Additional arguments to pass to execute_handless_handoff()
    
    Yields:
        HeartbeatSignal: Emitted every budget.heartbeat_minutes
        PauseSignal: Emitted when pause_after_minutes is approached
    
    Returns:
        HeadlessRunResult: Final run result
    
    Note: This is a Phase 2 MVP implementation that wraps the existing execute_handoff
    function and demonstrates heartbeat/pause signal emission. Full phase-level budget
    enforcement and mid-phase pause/resume will be added in Phase 3.
    """
    if budget is None:
        budget = LongHandoffBudget()
    budget.validate()
    
    # Initialize supervision context
    start_time = time.time()
    context = SupervisionContext(
        start_time=start_time,
        phase=ExecutionPhase.PLAN,
        elapsed_seconds=0.0,
        budget_max_runtime_minutes=budget.max_runtime_minutes,
        budget_heartbeat_minutes=budget.heartbeat_minutes,
        phase_start_time=start_time,
    )
    
    last_heartbeat_time = start_time
    heartbeat_interval = budget.heartbeat_minutes * 60  # Convert to seconds
    pause_threshold = (budget.pause_after_minutes or budget.max_runtime_minutes) * 60 if budget.pause_after_minutes else None
    
    # Execute the handoff with time tracking
    # In Phase 3, this will be refactored to insert hooks into each phase boundary
    # For now, we wrap the blocking call and emit signals after completion
    kwargs.setdefault("validation_time_budget_seconds", float(budget.review_minutes) * 60.0)
    kwargs.setdefault("repair_time_limit_seconds", float(budget.execute_minutes) * 60.0)
    kwargs.setdefault("validation_escalation_mode", bool(budget.pause_after_minutes))
    kwargs.setdefault("token_budget", budget.token_budget if budget.token_budget > 0 else None)
    start_exec = time.time()
    result = execute_headless_handoff(request, **kwargs)
    elapsed_exec = time.time() - start_exec
    
    # Emit heartbeat signals at intervals (simulated based on elapsed time)
    # This is a MVP that simulates where heartbeats would occur
    elapsed_total_seconds = elapsed_exec
    current_heartbeat_seconds = 0.0
    
    while current_heartbeat_seconds < elapsed_total_seconds:
        current_heartbeat_seconds += heartbeat_interval
        if current_heartbeat_seconds < elapsed_total_seconds:
            # In Phase 3, this will save an actual checkpoint
            signal = HeartbeatSignal(
                elapsed_minutes=current_heartbeat_seconds / 60.0,
                elapsed_seconds=current_heartbeat_seconds,
                phase=ExecutionPhase.EXECUTE,
                checkpoint_path=None,  # Phase 2: placeholder
                next_heartbeat_seconds=current_heartbeat_seconds + heartbeat_interval,
                summary=f"Heartbeat at {current_heartbeat_seconds / 60.0:.1f} minutes",
            )
            yield signal
    
    # Check if pause threshold was exceeded (would trigger mid-execution in Phase 3)
    if pause_threshold and elapsed_total_seconds > pause_threshold:
        # In Phase 3, this will trigger actual mid-phase pause with checkpoint
        signal = PauseSignal(
            reason="time_budget_exceeded",
            resume_token=f"{result.task.id}:{result.run.id}:minute-{int(elapsed_total_seconds / 60)}",
            checkpoint_path="paused-state.json",  # Phase 2: placeholder
            total_elapsed_minutes=elapsed_total_seconds / 60.0,
            total_elapsed_seconds=elapsed_total_seconds,
            phase_when_paused=ExecutionPhase.EXECUTE,
            checkpoint_json={},  # Phase 2: empty placeholder
        )
        yield signal
    
    # Return final result
    return result


def resume_headless_handoff_from_checkpoint(
    request: HandoffRequest,
    checkpoint_path: str | Path,
    budget: LongHandoffBudget | None = None,
    **kwargs,
) -> Generator[HeartbeatSignal | PauseSignal, None, HeadlessRunResult]:
    """Resume supervised execution from an incremental checkpoint snapshot."""
    active_budget = budget or LongHandoffBudget()
    active_budget.validate()
    phase, elapsed_seconds = _load_checkpoint_elapsed_seconds(checkpoint_path)
    resume_budget = _remaining_budget(active_budget, elapsed_seconds)

    yield HeartbeatSignal(
        elapsed_minutes=elapsed_seconds / 60.0,
        elapsed_seconds=elapsed_seconds,
        phase=phase,
        checkpoint_path=str(checkpoint_path),
        next_heartbeat_seconds=elapsed_seconds + (resume_budget.heartbeat_minutes * 60.0),
        summary=f"Resumed from checkpoint {Path(checkpoint_path).name}",
    )

    result = yield from execute_headless_handoff_supervised(request, budget=resume_budget, **kwargs)
    return result
