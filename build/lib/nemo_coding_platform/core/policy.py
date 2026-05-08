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
    source: str = "global"


@dataclass(frozen=True, slots=True)
class PolicyEvaluation:
    permission: str
    target: str
    action: PermissionAction
    matched_rule: PermissionRule | None
    reason: str


class PermissionEvaluator:
    def __init__(self, default_action: PermissionAction = PermissionAction.ASK) -> None:
        self.default_action = default_action

    def evaluate(self, permission: str, target: str, rules: list[PermissionRule]) -> PolicyEvaluation:
        matched: PermissionRule | None = None
        ordered = sorted(enumerate(rules), key=lambda item: (item[1].precedence, item[0]))
        for _, rule in ordered:
            if rule.permission != permission:
                continue
            if fnmatch(target, rule.pattern):
                matched = rule
        if matched is None:
            return PolicyEvaluation(
                permission=permission,
                target=target,
                action=self.default_action,
                matched_rule=None,
                reason="no matching rule",
            )
        return PolicyEvaluation(
            permission=permission,
            target=target,
            action=matched.action,
            matched_rule=matched,
            reason=f"matched {matched.source}:{matched.pattern}",
        )


def default_supervised_rules() -> list[PermissionRule]:
    return [
        PermissionRule("edit", "*", PermissionAction.ASK, precedence=10, source="supervised-default"),
        PermissionRule("bash", "git diff*", PermissionAction.ALLOW, precedence=20, source="supervised-default"),
        PermissionRule("bash", "python -m unittest*", PermissionAction.ALLOW, precedence=20, source="supervised-default"),
        PermissionRule("bash", "*", PermissionAction.ASK, precedence=10, source="supervised-default"),
        PermissionRule("network", "*", PermissionAction.DENY, precedence=30, source="supervised-default"),
        PermissionRule("external_directory", "*", PermissionAction.DENY, precedence=30, source="supervised-default"),
        PermissionRule("commit", "*", PermissionAction.ASK, precedence=40, source="review-gate"),
        PermissionRule("pull_request", "*", PermissionAction.ASK, precedence=40, source="review-gate"),
    ]
