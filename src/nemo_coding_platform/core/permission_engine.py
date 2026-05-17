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
        "execute", "pytest", "npm run", "pip install", "script",
        "bash", "cmd", "make ", "invoke", "deploy", "test suite",
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
                or fl.startswith("scripts/")
                or fl.startswith("scripts\\")
            ):
                detected.add(PermissionCategory.CONFIG_FILE_WRITE)

        # file_write_outside_worktree — path traversal or absolute paths
        for f in target_files:
            if ".." in f or f.startswith("/") or (len(f) > 2 and f[1] == ":") or f.startswith("\\\\"):
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


# ── Backward-compatibility: Legacy permission ruleset system ──────────────────
# headless_runner.py and self_modification.py import these symbols.
# Kept here for import compatibility while the full handoff system uses them.
import fnmatch as _fnmatch
import json as _json
from enum import StrEnum as _StrEnum
from pathlib import Path as _Path


class PermissionAction(_StrEnum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class PermissionRule:
    permission: str
    pattern: str
    action: PermissionAction


@dataclass
class PermissionRuleset:
    rules: list[PermissionRule]

    def evaluate(self, permission: str, pattern: str) -> PermissionAction:
        result = PermissionAction.DENY
        for rule in self.rules:
            if rule.permission == "*" or rule.permission == permission:
                if _fnmatch.fnmatch(pattern, rule.pattern):
                    result = rule.action
        return result


def default_ruleset(autonomy: object) -> PermissionRuleset:
    try:
        from nemo_coding_platform.core.product import AutonomyLevel
        if autonomy == AutonomyLevel.FULL_HANDOFF:
            return PermissionRuleset([PermissionRule("*", "*", PermissionAction.ALLOW)])
        if autonomy == AutonomyLevel.MANUAL:
            return PermissionRuleset([PermissionRule("*", "*", PermissionAction.DENY)])
    except Exception:
        pass
    return PermissionRuleset([
        PermissionRule("write_file", "*", PermissionAction.ALLOW),
        PermissionRule("run_command", "*", PermissionAction.ALLOW),
    ])


def load_ruleset_from_file(path: str | _Path) -> PermissionRuleset:
    path = _Path(path)
    if not path.exists():
        return PermissionRuleset([])
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
        rules = []
        for permission, value in data.items():
            if isinstance(value, str):
                rules.append(PermissionRule(permission, "*", PermissionAction(value)))
            elif isinstance(value, dict):
                for pattern, action in value.items():
                    rules.append(PermissionRule(permission, pattern, PermissionAction(action)))
        return PermissionRuleset(rules)
    except Exception:
        return PermissionRuleset([])
