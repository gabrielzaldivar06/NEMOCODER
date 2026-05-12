from __future__ import annotations

import json
import fnmatch
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from nemo_coding_platform.core.product import AutonomyLevel


class PermissionAction(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class PermissionRule:
    permission: str      # "write_file", "run_command", "git_push"
    pattern: str         # glob: "*.py", "src/**", "*"
    action: PermissionAction


@dataclass
class PermissionRuleset:
    rules: list[PermissionRule]
    
    def evaluate(self, permission: str, pattern: str) -> PermissionAction:
        """
        Evaluate a permission request. Last matching rule wins.
        Defaults to DENY if no rule matches.
        """
        result = PermissionAction.DENY
        
        # We search in order. If multiple rules match, the later one overrides the earlier one.
        for rule in self.rules:
            # Check permission match (e.g., "write_file" matches "write_file" or "*")
            if rule.permission == "*" or rule.permission == permission:
                # Check pattern match (glob)
                if fnmatch.fnmatch(pattern, rule.pattern):
                    result = rule.action
                    
        return result


def default_ruleset(autonomy: AutonomyLevel) -> PermissionRuleset:
    """Generate a default ruleset based on autonomy level."""
    rules = []
    
    if autonomy == AutonomyLevel.FULL_HANDOFF:
        # Full handoff allows writing anything but might restrict sensitive files if we wanted
        rules.append(PermissionRule("*", "*", PermissionAction.ALLOW))
    elif autonomy == AutonomyLevel.MANUAL:
        rules.append(PermissionRule("*", "*", PermissionAction.DENY))
    else:
        # Intermediate levels
        rules.append(PermissionRule("write_file", "*", PermissionAction.ALLOW))
        rules.append(PermissionRule("run_command", "*", PermissionAction.ALLOW))
        
    return PermissionRuleset(rules)


def load_ruleset_from_file(path: str | Path) -> PermissionRuleset:
    """
    Load .spacecode-permissions.json from path.
    Format:
    {
      "write_file": {"src/**": "allow", "*.env": "deny"},
      "run_command": "allow",
      "git_push": "deny"
    }
    """
    path = Path(path)
    if not path.exists():
        return PermissionRuleset([])
        
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        rules = []
        
        for permission, value in data.items():
            if isinstance(value, str):
                # Simple "permission": "allow"
                rules.append(PermissionRule(permission, "*", PermissionAction(value)))
            elif isinstance(value, dict):
                # Map of "pattern": "action"
                for pattern, action in value.items():
                    rules.append(PermissionRule(permission, pattern, PermissionAction(action)))
                    
        return PermissionRuleset(rules)
    except Exception:
        # Fallback to empty on error
        return PermissionRuleset([])
