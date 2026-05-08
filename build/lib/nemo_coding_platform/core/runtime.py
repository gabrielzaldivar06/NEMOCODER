from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

from nemo_coding_platform.core.contracts import RuntimeState
from nemo_coding_platform.core.worktree_runtime import WorktreeRuntimeSpec, create_worktree_runtime, reset_runtime_dirs


class SandboxKind(StrEnum):
    PROCESS = "process"
    WORKTREE = "worktree"
    CONTAINER = "container"


@dataclass(frozen=True, slots=True)
class RuntimeSession:
    session_id: str
    sandbox: SandboxKind
    state: RuntimeState = RuntimeState.INITIALIZING


class RuntimeController:
    _allowed: dict[RuntimeState, set[RuntimeState]] = {
        RuntimeState.INITIALIZING: {RuntimeState.READY, RuntimeState.FAILED},
        RuntimeState.READY: {RuntimeState.PLANNING, RuntimeState.PAUSED, RuntimeState.FAILED},
        RuntimeState.PLANNING: {RuntimeState.AWAITING_APPROVAL, RuntimeState.FAILED},
        RuntimeState.AWAITING_APPROVAL: {RuntimeState.EXECUTING, RuntimeState.PAUSED, RuntimeState.FAILED},
        RuntimeState.EXECUTING: {RuntimeState.REVIEWING, RuntimeState.PAUSED, RuntimeState.FAILED},
        RuntimeState.REVIEWING: {RuntimeState.COMPLETED, RuntimeState.PAUSED, RuntimeState.FAILED},
        RuntimeState.PAUSED: {RuntimeState.READY, RuntimeState.PLANNING, RuntimeState.AWAITING_APPROVAL, RuntimeState.EXECUTING, RuntimeState.REVIEWING, RuntimeState.FAILED},
        RuntimeState.COMPLETED: set(),
        RuntimeState.FAILED: set(),
    }

    def transition(self, session: RuntimeSession, new_state: RuntimeState) -> RuntimeSession:
        allowed = self._allowed[session.state]
        if new_state not in allowed:
            raise ValueError(f"invalid runtime transition: {session.state} -> {new_state}")
        return replace(session, state=new_state)


@dataclass(frozen=True, slots=True)
class AgentRuntime:
    session: RuntimeSession
    worktree: WorktreeRuntimeSpec
    controller: RuntimeController

    @classmethod
    def create(
        cls,
        task_id: str,
        run_id: str,
        runtime_root: str | Path,
        sandbox: SandboxKind = SandboxKind.WORKTREE,
        controller: RuntimeController | None = None,
        reset: bool = True,
    ) -> AgentRuntime:
        if sandbox != SandboxKind.WORKTREE:
            raise ValueError(f"unsupported agent runtime sandbox: {sandbox}")
        runtime_controller = controller or RuntimeController()
        worktree = create_worktree_runtime(task_id, run_id, runtime_root)
        if reset:
            worktree = reset_runtime_dirs(worktree)
        session = RuntimeSession(worktree.runtime_id, sandbox, RuntimeState.READY)
        return cls(session, worktree, runtime_controller)

    @property
    def runtime_id(self) -> str:
        return self.worktree.runtime_id

    @property
    def worktree_path(self) -> Path:
        return self.worktree.worktree_path

    @property
    def state(self) -> RuntimeState:
        return self.session.state

    def transition(self, new_state: RuntimeState) -> AgentRuntime:
        return replace(self, session=self.controller.transition(self.session, new_state))

    def to_status_dict(self) -> dict[str, str]:
        return {
            "runtime_id": self.runtime_id,
            "sandbox": self.session.sandbox.value,
            "state": self.session.state.value,
            "worktree_path": str(self.worktree.worktree_path),
        }
