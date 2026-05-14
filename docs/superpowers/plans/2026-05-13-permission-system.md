# Permission System (GAP-03 Phase A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pre-run permission gate that blocks handoff jobs until the user explicitly approves risky action categories, with audit trail stored in the job state.

**Architecture:** A pure-Python `PermissionAnalyzer` inspects the job's objective and target files to compute a `PermissionRequest`. `HandoffJobManager.start()` checks it before launching the subprocess — if approval is needed, the job parks in `"awaiting_permission"` status. Two new API endpoints let the user grant or deny from Mission Control's new `PermissionRequestPanel` component.

**Tech Stack:** Python 3.11 dataclasses/enums (backend), React 18 + TypeScript (frontend), existing `HandoffJob`/`HandoffJobManager` patterns, existing dynamic-route pattern in `do_POST`.

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `src/nemo_coding_platform/core/permission_engine.py` | Create | Enums, dataclasses, `PolicyProfile`, `PermissionAnalyzer` |
| `tests/test_permission_engine.py` | Create | 7 unit tests for the engine |
| `src/nemo_coding_platform/mission_control_server.py` | Modify | `HandoffJob` field, `start()` gate, `grant_permission()`, `deny_permission()`, 2 API handlers, 2 routes |
| `apps/mission-control/src/components/PermissionRequestPanel.tsx` | Create | Approval UI panel |
| `apps/mission-control/src/styles.css` | Modify | CSS for the panel |
| `apps/mission-control/src/main.tsx` | Modify | `jobAwaitingPermission` memo, grant/deny handlers, AgentPane prop |

---

## Task 1: Core permission engine

**Files:**
- Create: `src/nemo_coding_platform/core/permission_engine.py`

- [ ] **Step 1: Create the file with enums, dataclasses, and PolicyProfile**

```python
# src/nemo_coding_platform/core/permission_engine.py
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PermissionCategory(str, Enum):
    FILE_WRITE_OUTSIDE_WORKTREE = "file_write_outside_worktree"
    SHELL_COMMAND               = "shell_command"
    NETWORK_CALL                = "network_call"
    CONFIG_FILE_WRITE           = "config_file_write"


class PermissionMode(str, Enum):
    FREEDOM     = "freedom"
    RESTRICTION = "restriction"


@dataclass(frozen=True, slots=True)
class PermissionRequest:
    job_id: str
    categories: tuple[PermissionCategory, ...]
    rationale: str
    auto_approved: tuple[PermissionCategory, ...]
    requires_user_approval: tuple[PermissionCategory, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "categories": [c.value for c in self.categories],
            "rationale": self.rationale,
            "auto_approved": [c.value for c in self.auto_approved],
            "requires_user_approval": [c.value for c in self.requires_user_approval],
        }


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    decided_at: str
    decided_by: str   # "user" | "policy_auto" | FUTURE: "mid_run_signal"
    approved: bool
    categories: tuple[PermissionCategory, ...]
    note: str

    def to_dict(self) -> dict[str, object]:
        return {
            "decided_at": self.decided_at,
            "decided_by": self.decided_by,
            "approved": self.approved,
            "categories": [c.value for c in self.categories],
            "note": self.note,
        }


# Auto-approved categories per mode. Categories absent from these sets are always-ask.
_AUTO_APPROVE: dict[PermissionMode, frozenset[PermissionCategory]] = {
    PermissionMode.FREEDOM:     frozenset({PermissionCategory.SHELL_COMMAND,
                                           PermissionCategory.NETWORK_CALL}),
    PermissionMode.RESTRICTION: frozenset(),
}


class PermissionAnalyzer:
    """Inspect objective + target_files to compute a PermissionRequest."""

    _SHELL_KEYWORDS = frozenset({
        "run ", "execute", "pytest", "npm run", "pip install", "script",
        "bash", "cmd", "make ", "invoke", "deploy", "build", "test suite",
    })
    _NETWORK_KEYWORDS = frozenset({
        "fetch", " http", "curl", "request", "download", "upload",
        "webhook", "api call", "endpoint", "url ",
    })
    _CONFIG_KEYWORDS = frozenset({
        "workflow", " ci ", "dockerfile", "docker", "config", "pipeline",
        ".github", "makefile", "setup.py",
    })
    _CONFIG_SUFFIXES = (".toml", ".cfg", ".ini", ".dockerfile")
    _CONFIG_NAMES = frozenset({
        "dockerfile", "makefile", "setup.py", "setup.cfg", "pyproject.toml",
        "docker-compose.yml", "docker-compose.yaml",
    })

    def analyze(
        self,
        job_id: str,
        objective: str,
        target_files: tuple[str, ...],
        mode: PermissionMode,
    ) -> PermissionRequest:
        obj_lower = objective.lower()
        detected: set[PermissionCategory] = set()

        # shell_command
        if any(kw in obj_lower for kw in self._SHELL_KEYWORDS):
            detected.add(PermissionCategory.SHELL_COMMAND)

        # network_call
        if any(kw in obj_lower for kw in self._NETWORK_KEYWORDS):
            detected.add(PermissionCategory.NETWORK_CALL)

        # config_file_write — keyword in objective OR suspicious target file
        if any(kw in obj_lower for kw in self._CONFIG_KEYWORDS):
            detected.add(PermissionCategory.CONFIG_FILE_WRITE)
        for f in target_files:
            fl = f.lower()
            name = fl.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            if (
                name in self._CONFIG_NAMES
                or fl.endswith(self._CONFIG_SUFFIXES)
                or "/.github/" in fl
                or "\\.github\\" in fl
                or fl.startswith(".github/")
                or "/scripts/" in fl
            ):
                detected.add(PermissionCategory.CONFIG_FILE_WRITE)

        # file_write_outside_worktree — path traversal or absolute paths
        for f in target_files:
            if ".." in f or f.startswith("/") or (len(f) > 2 and f[1] == ":"):
                detected.add(PermissionCategory.FILE_WRITE_OUTSIDE_WORKTREE)

        categories = tuple(sorted(detected, key=lambda c: c.value))
        auto_ok = _AUTO_APPROVE[mode]
        auto_approved = tuple(c for c in categories if c in auto_ok)
        requires_approval = tuple(c for c in categories if c not in auto_ok)

        rationale = _build_rationale(objective, requires_approval)
        return PermissionRequest(
            job_id=job_id,
            categories=categories,
            rationale=rationale,
            auto_approved=auto_approved,
            requires_user_approval=requires_approval,
        )


def _build_rationale(objective: str, requires: tuple[PermissionCategory, ...]) -> str:
    if not requires:
        return ""
    parts = []
    labels = {
        PermissionCategory.FILE_WRITE_OUTSIDE_WORKTREE: "escribe fuera del sandbox",
        PermissionCategory.SHELL_COMMAND:               "ejecuta comandos de sistema",
        PermissionCategory.NETWORK_CALL:                "realiza llamadas de red",
        PermissionCategory.CONFIG_FILE_WRITE:           "modifica archivos de configuración críticos",
    }
    parts = [labels[c] for c in requires if c in labels]
    summary = ", ".join(parts)
    short_obj = objective[:80] + ("…" if len(objective) > 80 else "")
    return f'El task "{short_obj}" {summary}.'


# ── FUTURE: Mid-Run Signal Protocol (Phase B) ─────────────────────────────────
# When implemented, MidRunSignalHandler will:
#   1. Poll worktree_path / ".nemo-permission-request.json" during subprocess execution
#   2. On detection: SIGSTOP the Aider process, surface PermissionRequest via same API
#   3. On user decision: clear the signal file, SIGCONT the process
#   4. Record PermissionDecision with decided_by="mid_run_signal"
# PermissionRequest / PermissionDecision dataclasses are already compatible.
# SIGNAL_FILE = ".nemo-permission-request.json"   # placeholder constant, not used yet
# class MidRunSignalHandler:  # placeholder, not implemented
#     pass
```

- [ ] **Step 2: Verify the file parses cleanly**

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -c "from nemo_coding_platform.core.permission_engine import PermissionAnalyzer, PermissionMode; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add src/nemo_coding_platform/core/permission_engine.py
git commit -m "feat: add permission_engine core (PermissionAnalyzer, dataclasses, modes)"
```

---

## Task 2: Tests for permission engine (TDD)

**Files:**
- Create: `tests/test_permission_engine.py`

- [ ] **Step 1: Write all 7 tests**

```python
# tests/test_permission_engine.py
from datetime import timezone, datetime

from nemo_coding_platform.core.permission_engine import (
    PermissionAnalyzer,
    PermissionCategory,
    PermissionDecision,
    PermissionMode,
    PermissionRequest,
)


_analyzer = PermissionAnalyzer()


class PermissionAnalyzerTests:
    def test_config_write_always_asks_in_both_modes(self):
        for mode in (PermissionMode.FREEDOM, PermissionMode.RESTRICTION):
            req = _analyzer.analyze("j1", "add CI workflow to .github/workflows/", (), mode)
            assert PermissionCategory.CONFIG_FILE_WRITE in req.requires_user_approval, mode
            assert PermissionCategory.CONFIG_FILE_WRITE not in req.auto_approved, mode

    def test_shell_auto_in_freedom_ask_in_restriction(self):
        req_free = _analyzer.analyze("j2", "run pytest and fix failures", (), PermissionMode.FREEDOM)
        assert PermissionCategory.SHELL_COMMAND in req_free.auto_approved
        assert PermissionCategory.SHELL_COMMAND not in req_free.requires_user_approval

        req_restr = _analyzer.analyze("j3", "run pytest and fix failures", (), PermissionMode.RESTRICTION)
        assert PermissionCategory.SHELL_COMMAND in req_restr.requires_user_approval
        assert PermissionCategory.SHELL_COMMAND not in req_restr.auto_approved

    def test_network_auto_in_freedom_ask_in_restriction(self):
        req_free = _analyzer.analyze("j4", "fetch data from http endpoint", (), PermissionMode.FREEDOM)
        assert PermissionCategory.NETWORK_CALL in req_free.auto_approved

        req_restr = _analyzer.analyze("j5", "fetch data from http endpoint", (), PermissionMode.RESTRICTION)
        assert PermissionCategory.NETWORK_CALL in req_restr.requires_user_approval

    def test_no_risky_actions_empty_requires(self):
        req = _analyzer.analyze("j6", "add docstring to calculate_total function", (), PermissionMode.RESTRICTION)
        assert req.requires_user_approval == ()
        assert req.categories == ()

    def test_file_outside_worktree_always_asks(self):
        for mode in (PermissionMode.FREEDOM, PermissionMode.RESTRICTION):
            req = _analyzer.analyze("j7", "update config", ("../../etc/passwd",), mode)
            assert PermissionCategory.FILE_WRITE_OUTSIDE_WORKTREE in req.requires_user_approval, mode

    def test_permission_request_serializable(self):
        req = _analyzer.analyze("j8", "run pytest", (), PermissionMode.RESTRICTION)
        d = req.to_dict()
        assert d["job_id"] == "j8"
        assert isinstance(d["requires_user_approval"], list)
        assert isinstance(d["auto_approved"], list)
        assert isinstance(d["rationale"], str)

    def test_permission_decision_serializable(self):
        dec = PermissionDecision(
            decided_at=datetime.now(timezone.utc).isoformat(),
            decided_by="user",
            approved=True,
            categories=(PermissionCategory.SHELL_COMMAND,),
            note="ok",
        )
        d = dec.to_dict()
        assert d["approved"] is True
        assert d["decided_by"] == "user"
        assert "shell_command" in d["categories"]
```

- [ ] **Step 2: Run tests — expect all to pass**

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m pytest tests/test_permission_engine.py -v
```

Expected: `7 passed`

- [ ] **Step 3: Commit**

```bash
git add tests/test_permission_engine.py
git commit -m "test: add 7 unit tests for permission_engine"
```

---

## Task 3: HandoffJob field + HandoffJobManager gate

**Files:**
- Modify: `src/nemo_coding_platform/mission_control_server.py`

The `HandoffJob` dataclass is around line 81. `HandoffJobManager.start()` is around line 156.

- [ ] **Step 1: Add `permission_request` field to `HandoffJob`**

Find the block after `stagnant_heartbeats: int = 0` (around line 97) and add:

```python
    stagnant_heartbeats: int = 0
    permission_request: dict[str, object] | None = None  # set when awaiting_permission
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
```

- [ ] **Step 2: Add `permission_request` to `HandoffJob.to_dict()`**

In `to_dict()` (around line 120), add to the payload dict before the `if include_logs` block:

```python
        payload: dict[str, object] = {
            ...
            "stagnant_heartbeats": self.stagnant_heartbeats,
            "permission_request": self.permission_request,  # add this line
        }
```

- [ ] **Step 3: Add `permission_request` to `HandoffJob.from_snapshot()`**

In `from_snapshot()` (around line 101), after the `stagnant_heartbeats` line add:

```python
            stagnant_heartbeats=int(payload.get("stagnant_heartbeats") or 0),
            permission_request=payload.get("permission_request") if isinstance(payload.get("permission_request"), dict) else None,
```

- [ ] **Step 4: Add `grant_permission` and `deny_permission` methods to `HandoffJobManager`**

Add these two methods after `start_self_modify` (around line 200):

```python
    def grant_permission(
        self, config: "MissionControlServerConfig", job_id: str, note: str = ""
    ) -> "HandoffJob":
        from datetime import timezone
        from nemo_coding_platform.core.permission_engine import PermissionDecision, PermissionCategory

        job = self.get(job_id)
        if job.status != "awaiting_permission":
            raise ApiRequestError(
                f"job {job_id} is not awaiting_permission (status={job.status})",
                error_code="invalid_job_status",
            )
        req_dict = job.permission_request or {}
        all_cats = tuple(
            PermissionCategory(v)
            for v in (req_dict.get("categories") or [])
            if isinstance(v, str)
        )
        decision = PermissionDecision(
            decided_at=datetime.now(timezone.utc).isoformat(),
            decided_by="user",
            approved=True,
            categories=all_cats,
            note=note,
        )
        updated_request = {**(req_dict), "decision": decision.to_dict()}
        with self._lock:
            job.permission_request = updated_request
            job.status = "starting"
            self._jobs[job_id] = job
        self._persist_job(job)
        self._append_log(job, f"permission granted: {[c.value for c in all_cats]}")
        # Build command now and launch
        settings = _load_settings(config)
        merged_payload = _enforce_workspace_scope(config, {**settings, **job.payload})
        objective = _objective(merged_payload)
        provider = _provider_mode(merged_payload)
        timeout = _timeout_seconds(merged_payload)
        task_id = job.task_id
        run_id = job.run_id
        run_json = Path(job.run_json)
        command = self._build_command(config, merged_payload, objective, provider, timeout, task_id, run_id, run_json)
        with self._lock:
            job.command = command
        run_thread = threading.Thread(target=self._run_job, args=(config, job), daemon=True)
        job.run_thread = run_thread
        run_thread.start()
        return job

    def deny_permission(self, job_id: str, note: str = "") -> "HandoffJob":
        from datetime import timezone
        from nemo_coding_platform.core.permission_engine import PermissionDecision, PermissionCategory

        job = self.get(job_id)
        if job.status != "awaiting_permission":
            raise ApiRequestError(
                f"job {job_id} is not awaiting_permission (status={job.status})",
                error_code="invalid_job_status",
            )
        req_dict = job.permission_request or {}
        all_cats = tuple(
            PermissionCategory(v)
            for v in (req_dict.get("categories") or [])
            if isinstance(v, str)
        )
        decision = PermissionDecision(
            decided_at=datetime.now(timezone.utc).isoformat(),
            decided_by="user",
            approved=False,
            categories=all_cats,
            note=note,
        )
        updated_request = {**(req_dict), "decision": decision.to_dict()}
        with self._lock:
            job.permission_request = updated_request
            job.status = "permission_denied"
        self._persist_job(job)
        self._append_log(job, f"permission denied: {note or '(no note)'}")
        return job
```

- [ ] **Step 5: Add the permission gate at the top of `HandoffJobManager.start()`**

In `start()` (around line 156), insert the gate after `run_json = ...` and before `command = self._build_command(...)`:

```python
        run_json = config.run_results_path / f"{task_id}-{run_id}.json"

        # ── Permission Gate (Phase A: pre-run) ─────────────────────────────
        from nemo_coding_platform.core.permission_engine import (
            PermissionAnalyzer, PermissionMode, PermissionDecision,
        )
        raw_autonomy = str(merged_payload.get("autonomy_mode") or "trusted")
        perm_mode = PermissionMode.FREEDOM if raw_autonomy == "aggressive" else PermissionMode.RESTRICTION
        target_files = tuple(
            str(f) for f in merged_payload.get("files", []) if isinstance(f, str)
        )
        perm_request = PermissionAnalyzer().analyze(job_id, objective, target_files, perm_mode)
        if perm_request.requires_user_approval:
            job = HandoffJob(
                job_id, task_id, run_id, str(run_json),
                "awaiting_permission",
                (),
                dict(merged_payload),
                ["awaiting permission approval"],
                permission_request=perm_request.to_dict(),
            )
            with self._lock:
                self._jobs[job_id] = job
            self._persist_job(job)
            return job
        # Auto-approved path — record policy_auto decision in job metadata
        auto_decision = PermissionDecision(
            decided_at=datetime.now(timezone.utc).isoformat(),
            decided_by="policy_auto",
            approved=True,
            categories=perm_request.auto_approved,
            note="",
        )
        auto_perm_meta: dict[str, object] = {
            **perm_request.to_dict(),
            "decision": auto_decision.to_dict(),
        }
        # ── end Permission Gate ─────────────────────────────────────────────

        command = self._build_command(config, merged_payload, objective, provider, timeout, task_id, run_id, run_json)
        job = HandoffJob(
            job_id, task_id, run_id, str(run_json), "starting", command, dict(merged_payload),
            ["starting handoff job"],
            permission_request=auto_perm_meta,
        )
        with self._lock:
            self._jobs[job_id] = job
        self._persist_job(job)
        run_thread = threading.Thread(target=self._run_job, args=(config, job), daemon=True)
        job.run_thread = run_thread
        run_thread.start()
        return job
```

- [ ] **Step 6: Verify server imports cleanly**

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -c "from nemo_coding_platform.mission_control_server import HandoffJobManager; print('ok')"
```

Expected: `ok`

- [ ] **Step 7: Commit**

```bash
git add src/nemo_coding_platform/mission_control_server.py
git commit -m "feat: add HandoffJob.permission_request field and pre-run gate in HandoffJobManager.start()"
```

---

## Task 4: API endpoints — permission-grant and permission-deny

**Files:**
- Modify: `src/nemo_coding_platform/mission_control_server.py`

- [ ] **Step 1: Add two API handler functions**

Find the area near `api_worktree_cleanup` (after line ~6244) and add after it:

```python
def api_permission_grant(
    config: "MissionControlServerConfig", job_id: str, payload: dict[str, object], jobs: "HandoffJobManager"
) -> dict[str, object]:
    note = str(payload.get("note") or "")
    job = jobs.grant_permission(config, job_id, note)
    return {"granted": True, "job_id": job_id, "status": job.status}


def api_permission_deny(
    config: "MissionControlServerConfig", job_id: str, payload: dict[str, object], jobs: "HandoffJobManager"
) -> dict[str, object]:
    note = str(payload.get("note") or "")
    job = jobs.deny_permission(job_id, note)
    return {"denied": True, "job_id": job_id, "status": job.status}
```

- [ ] **Step 2: Add dynamic routes in `do_POST`**

In `do_POST`, after the `/worktree-cleanup` block (around line 6410) and before `handlers = {`, add:

```python
        if route.startswith("/api/run/") and route.endswith("/permission-grant"):
            job_id = route[len("/api/run/"): -len("/permission-grant")].strip("/")
            try:
                body = _load_body(self)
            except (ApiRequestError, json.JSONDecodeError, ValueError) as error:
                _json_response(self, 400, {"error": str(error), "error_code": "invalid_request"})
                return
            self._handle(lambda payload: api_permission_grant(self.server.config, job_id, payload, self.server.jobs), body)
            return
        if route.startswith("/api/run/") and route.endswith("/permission-deny"):
            job_id = route[len("/api/run/"): -len("/permission-deny")].strip("/")
            try:
                body = _load_body(self)
            except (ApiRequestError, json.JSONDecodeError, ValueError) as error:
                _json_response(self, 400, {"error": str(error), "error_code": "invalid_request"})
                return
            self._handle(lambda payload: api_permission_deny(self.server.config, job_id, payload, self.server.jobs), body)
            return
```

- [ ] **Step 3: Verify server imports cleanly**

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -c "from nemo_coding_platform.mission_control_server import MissionControlHttpServer; print('ok')"
```

Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add src/nemo_coding_platform/mission_control_server.py
git commit -m "feat: add /permission-grant and /permission-deny API endpoints"
```

---

## Task 5: PermissionRequestPanel component + CSS

**Files:**
- Create: `apps/mission-control/src/components/PermissionRequestPanel.tsx`
- Modify: `apps/mission-control/src/styles.css`

- [ ] **Step 1: Create the component**

```tsx
// apps/mission-control/src/components/PermissionRequestPanel.tsx
import { useState } from "react";
import { ShieldCheck, ShieldX, CheckCircle2, XCircle } from "lucide-react";

interface PermissionRequestData {
  job_id: string;
  categories: string[];
  rationale: string;
  auto_approved: string[];
  requires_user_approval: string[];
}

interface Props {
  jobId: string;
  objective: string;
  permissionRequest: PermissionRequestData;
  onGranted: () => void;
  onDenied: () => void;
}

const CATEGORY_LABELS: Record<string, string> = {
  file_write_outside_worktree: "Escritura fuera del sandbox",
  shell_command:               "Ejecución de comandos",
  network_call:                "Llamadas de red",
  config_file_write:           "Archivos de configuración",
};

const CATEGORY_ICONS: Record<string, string> = {
  file_write_outside_worktree: "📁",
  shell_command:               "▶",
  network_call:                "🌐",
  config_file_write:           "⚙",
};

export function PermissionRequestPanel({ jobId, objective, permissionRequest, onGranted, onDenied }: Props) {
  const [note, setNote] = useState("");
  const [status, setStatus] = useState<"idle" | "granting" | "denying">("idle");
  const [error, setError] = useState<string | null>(null);

  const handleGrant = async () => {
    setStatus("granting");
    setError(null);
    try {
      const res = await fetch(`/api/run/${jobId}/permission-grant`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ note }),
      });
      const data = await res.json();
      if (data.granted) {
        onGranted();
      } else {
        setError(data.error ?? "Error al conceder permiso");
        setStatus("idle");
      }
    } catch (e) {
      setError(String(e));
      setStatus("idle");
    }
  };

  const handleDeny = async () => {
    setStatus("denying");
    setError(null);
    try {
      const res = await fetch(`/api/run/${jobId}/permission-deny`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ note }),
      });
      const data = await res.json();
      if (data.denied) {
        onDenied();
      } else {
        setError(data.error ?? "Error al denegar permiso");
        setStatus("idle");
      }
    } catch (e) {
      setError(String(e));
      setStatus("idle");
    }
  };

  const shortObj = objective.length > 80 ? objective.slice(0, 80) + "…" : objective;
  const requires = permissionRequest.requires_user_approval;
  const autoOk = permissionRequest.auto_approved;

  return (
    <div className="permission-request-panel">
      <div className="permission-request-header">
        <ShieldX size={16} className="permission-request-icon" />
        <span>Permiso requerido antes de ejecutar</span>
      </div>

      <div className="permission-request-objective">
        <span className="permission-label">Task:</span>
        <em>{shortObj}</em>
      </div>

      {permissionRequest.rationale && (
        <p className="permission-rationale">{permissionRequest.rationale}</p>
      )}

      {requires.length > 0 && (
        <div className="permission-category-group">
          <span className="permission-label">Requiere aprobación:</span>
          <div className="permission-badges">
            {requires.map((cat) => (
              <span key={cat} className="permission-badge permission-badge--ask">
                {CATEGORY_ICONS[cat] ?? "?"} {CATEGORY_LABELS[cat] ?? cat}
              </span>
            ))}
          </div>
        </div>
      )}

      {autoOk.length > 0 && (
        <div className="permission-category-group">
          <span className="permission-label">Auto-aprobadas (política):</span>
          <div className="permission-badges">
            {autoOk.map((cat) => (
              <span key={cat} className="permission-badge permission-badge--auto">
                {CATEGORY_ICONS[cat] ?? "?"} {CATEGORY_LABELS[cat] ?? cat}
              </span>
            ))}
          </div>
        </div>
      )}

      <input
        className="permission-note-input"
        type="text"
        placeholder="Nota opcional…"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        disabled={status !== "idle"}
      />

      {error && <p className="permission-error">{error}</p>}

      <div className="permission-actions">
        <button
          className="permission-grant-btn"
          onClick={handleGrant}
          disabled={status !== "idle"}
        >
          <CheckCircle2 size={14} />
          {status === "granting" ? "Aprobando…" : "Aprobar y lanzar"}
        </button>
        <button
          className="permission-deny-btn"
          onClick={handleDeny}
          disabled={status !== "idle"}
        >
          <XCircle size={14} />
          {status === "denying" ? "Denegando…" : "Denegar"}
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Add CSS to `apps/mission-control/src/styles.css`**

Append at the very end of the file:

```css
/* ── PermissionRequestPanel ─────────────────────────────────────── */
.permission-request-panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 16px;
  background: #110c00;
  border: 1px solid #b45309;
  border-radius: 8px;
  margin-bottom: 12px;
}
.permission-request-header {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #fbbf24;
  font-size: 13px;
  font-weight: 600;
}
.permission-request-icon { color: #f59e0b; }
.permission-request-objective {
  font-size: 12px;
  color: #8b949e;
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.permission-request-objective em { color: #e6edf3; font-style: normal; }
.permission-rationale { font-size: 12px; color: #8b949e; margin: 0; }
.permission-label { font-size: 11px; color: #8b949e; font-weight: 500; }
.permission-category-group { display: flex; flex-direction: column; gap: 4px; }
.permission-badges { display: flex; gap: 6px; flex-wrap: wrap; }
.permission-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  padding: 3px 8px;
  border-radius: 4px;
  border: 1px solid;
}
.permission-badge--ask { color: #fbbf24; background: #1a1000; border-color: #b45309; }
.permission-badge--auto { color: #3fb950; background: #0f1a10; border-color: #238636; }
.permission-note-input {
  background: #0d1117;
  border: 1px solid #30363d;
  border-radius: 4px;
  color: #e6edf3;
  font-size: 12px;
  padding: 6px 8px;
  width: 100%;
  box-sizing: border-box;
}
.permission-note-input:focus { outline: 1px solid #b45309; }
.permission-error { font-size: 12px; color: #f85149; margin: 0; }
.permission-actions { display: flex; gap: 8px; }
.permission-grant-btn {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 6px 14px; border: 1px solid #238636; border-radius: 6px;
  background: #0f2d1a; color: #3fb950; font-size: 12px; cursor: pointer;
}
.permission-grant-btn:hover:not(:disabled) { background: #1a4428; border-color: #3fb950; }
.permission-grant-btn:disabled { opacity: 0.5; cursor: default; }
.permission-deny-btn {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 6px 14px; border: 1px solid #6e2a2a; border-radius: 6px;
  background: #200a0a; color: #f85149; font-size: 12px; cursor: pointer;
}
.permission-deny-btn:hover:not(:disabled) { background: #2d1010; border-color: #f85149; }
.permission-deny-btn:disabled { opacity: 0.5; cursor: default; }
```

- [ ] **Step 3: Commit**

```bash
git add apps/mission-control/src/components/PermissionRequestPanel.tsx
git add apps/mission-control/src/styles.css
git commit -m "feat: add PermissionRequestPanel component and CSS"
```

---

## Task 6: Wire PermissionRequestPanel into main.tsx

**Files:**
- Modify: `apps/mission-control/src/main.tsx`

- [ ] **Step 1: Add import at the top (after TelemetryColumn import, around line 12)**

```tsx
import { WorktreeDiffPanel } from "./components/WorktreeDiffPanel";
import { PermissionRequestPanel } from "./components/PermissionRequestPanel";  // add this
```

- [ ] **Step 2: Add `jobAwaitingPermission` memo alongside `selectedJob` (around line 969)**

```tsx
  const selectedJob = useMemo(() => state.jobs.find((j) => j.task_id === selectedRun?.task_id && j.run_id === selectedRun?.run_id), [state.jobs, selectedRun]);
  const jobAwaitingPermission = useMemo(() => state.jobs.find((j) => j.status === "awaiting_permission"), [state.jobs]);  // add this
```

- [ ] **Step 3: Add grant/deny handler functions**

Add these two functions in `App()`, near the other handler functions (around line 1170, alongside `selectFile`, `sendAgentMessage` etc.):

```tsx
  const grantPermission = async (jobId: string, note: string) => {
    await fetch(`/api/run/${jobId}/permission-grant`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note }),
    });
    refreshState();  // refreshState() already exists in App — fetches /api/state
  };

  const denyPermission = async (jobId: string, note: string) => {
    await fetch(`/api/run/${jobId}/permission-deny`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note }),
    });
    refreshState();
  };
```

- [ ] **Step 4: Render PermissionRequestPanel at the top of AgentPane's return, before the chat section**

In the `AgentPane` function (around line 3390), add at the very top of its `return` JSX, just after `<aside className="agent-pane">`:

First add `permissionJob` and `onGrantPermission`/`onDenyPermission` to `AgentPaneProps` (around line 3159):

```tsx
type AgentPaneProps = {
  run: MissionRun | undefined;
  state: MissionState;
  // ... existing props ...
  permissionJob?: { job_id: string; payload: { objective?: string }; permission_request?: { job_id: string; categories: string[]; rationale: string; auto_approved: string[]; requires_user_approval: string[] } } | null;
  onGrantPermission?: (jobId: string, note: string) => void;
  onDenyPermission?: (jobId: string, note: string) => void;
```

Then in `AgentPane`'s destructured props (around line 3386), add:
```tsx
function AgentPane({ run, state, ..., permissionJob, onGrantPermission, onDenyPermission, showSettingsPanel = true }: AgentPaneProps) {
```

Then in the JSX, after `<aside className="agent-pane">` (around line 3391):

```tsx
      {permissionJob && permissionJob.permission_request && onGrantPermission && onDenyPermission && (
        <PermissionRequestPanel
          jobId={permissionJob.job_id}
          objective={String(permissionJob.payload?.objective ?? "")}
          permissionRequest={permissionJob.permission_request as { job_id: string; categories: string[]; rationale: string; auto_approved: string[]; requires_user_approval: string[] }}
          onGranted={() => onGrantPermission(permissionJob.job_id, "")}
          onDenied={() => onDenyPermission(permissionJob.job_id, "")}
        />
      )}
```

- [ ] **Step 5: Pass the new props to `<AgentPane>` in the workspace (around line 2599)**

```tsx
            {runsWorkbenchTab !== "review" && <AgentPane
              run={selectedRun}
              state={state}
              ...
              permissionJob={jobAwaitingPermission}
              onGrantPermission={grantPermission}
              onDenyPermission={denyPermission}
```

- [ ] **Step 6: Add `HandoffJob.permission_request` to the TypeScript type (around line 119)**

```tsx
type HandoffJob = {
  job_id: string;
  task_id: string;
  run_id: string;
  run_json: string;
  status: string;
  returncode: number | null;
  error: string | null;
  logs: string[];
  permission_request?: {
    job_id: string;
    categories: string[];
    rationale: string;
    auto_approved: string[];
    requires_user_approval: string[];
    decision?: {
      decided_at: string;
      decided_by: string;
      approved: boolean;
      categories: string[];
      note: string;
    };
  } | null;
};
```

- [ ] **Step 7: TypeScript check**

```powershell
cd apps/mission-control
npx tsc --noEmit
```

Expected: no errors

- [ ] **Step 8: Commit**

```bash
git add apps/mission-control/src/main.tsx
git commit -m "feat: wire PermissionRequestPanel into AgentPane with grant/deny handlers"
```

---

## Task 7: Final verification

- [ ] **Step 1: Run full test suite for permission engine**

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m pytest tests/test_permission_engine.py -v
```

Expected: `7 passed`

- [ ] **Step 2: TypeScript clean**

```powershell
cd apps/mission-control && npx tsc --noEmit
```

Expected: no output (zero errors)

- [ ] **Step 3: Smoke test — restriction mode blocks launch**

With the server running (`start-mvp-local.ps1`), POST this to the handoff endpoint from a terminal:

```powershell
$body = @{ objective = "add CI workflow to .github/workflows/test.yml"; autonomy_mode = "trusted" } | ConvertTo-Json
Invoke-RestMethod -Method POST -Uri "http://127.0.0.1:8787/api/handoff/start" -Body $body -ContentType "application/json"
```

Expected response: `status = "awaiting_permission"`, `permission_request.requires_user_approval` contains `"config_file_write"`

- [ ] **Step 4: Smoke test — freedom mode auto-approves shell**

```powershell
$body = @{ objective = "run pytest and fix all failures"; autonomy_mode = "aggressive" } | ConvertTo-Json
Invoke-RestMethod -Method POST -Uri "http://127.0.0.1:8787/api/handoff/start" -Body $body -ContentType "application/json"
```

Expected response: `status = "starting"`, `permission_request.decision.decided_by = "policy_auto"`

- [ ] **Step 5: Smoke test — grant permission**

Take the `job_id` from step 3 and grant:

```powershell
$body = @{ note = "aprobado para CI" } | ConvertTo-Json
Invoke-RestMethod -Method POST -Uri "http://127.0.0.1:8787/api/run/<job_id>/permission-grant" -Body $body -ContentType "application/json"
```

Expected: `{"granted": true, "status": "starting"}`

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "feat: GAP-03 Phase A — permission system complete (pre-run gate, grant/deny API, PermissionRequestPanel)"
```
