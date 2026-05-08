from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from fnmatch import fnmatch


class PermissionAction(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass(frozen=True, slots=True)
class PermissionRule:
    permission: str
    pattern: str
    action: PermissionAction
    precedence: int = 0
    source: str = "default"


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    permission: str
    target: str
    action: PermissionAction
    matched_rule: PermissionRule | None


class PermissionEvaluator:
    def __init__(self, default_action: PermissionAction = PermissionAction.ASK) -> None:
        self.default_action = default_action

    def evaluate(self, permission: str, target: str, rules: list[PermissionRule]) -> PermissionDecision:
        matched: PermissionRule | None = None
        ordered = sorted(enumerate(rules), key=lambda item: (item[1].precedence, item[0]))
        for _, rule in ordered:
            if rule.permission == permission and fnmatch(target, rule.pattern):
                matched = rule
        if matched is None:
            return PermissionDecision(permission, target, self.default_action, None)
        return PermissionDecision(permission, target, matched.action, matched)


def default_aider_product_rules() -> list[PermissionRule]:
    return [
        PermissionRule("edit", "*", PermissionAction.ASK, precedence=10, source="aider-quality-core"),
        PermissionRule("bash", "git diff*", PermissionAction.ALLOW, precedence=20, source="safe-git"),
        PermissionRule("bash", "python -m pytest*", PermissionAction.ALLOW, precedence=20, source="validation"),
        PermissionRule("bash", "*", PermissionAction.ASK, precedence=10, source="opencode-style-default"),
        PermissionRule("network", "*", PermissionAction.DENY, precedence=30, source="sandbox-default"),
        PermissionRule("external_directory", "*", PermissionAction.DENY, precedence=30, source="workspace-boundary"),
        PermissionRule("merge_to_main_workspace", "*", PermissionAction.ASK, precedence=40, source="review-gate"),
    ]