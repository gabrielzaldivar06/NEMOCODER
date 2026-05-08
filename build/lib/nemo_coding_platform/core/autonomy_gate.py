from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from nemo_coding_platform.core.review_gate import MergePlan


class ApplyAutonomyProfile(StrEnum):
    MANUAL = "manual"
    TRUSTED = "trusted"
    AGGRESSIVE = "aggressive"


@dataclass(frozen=True, slots=True)
class AutoApplyDecision:
    allowed: bool
    profile: ApplyAutonomyProfile
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {"allowed": self.allowed, "profile": self.profile.value, "reasons": list(self.reasons)}


def evaluate_auto_apply(plan: MergePlan, profile: str | ApplyAutonomyProfile = ApplyAutonomyProfile.MANUAL) -> AutoApplyDecision:
    autonomy = ApplyAutonomyProfile(profile)
    reasons: list[str] = []
    if autonomy == ApplyAutonomyProfile.MANUAL:
        reasons.append("manual_profile_requires_review")
    if not plan.mergeable:
        reasons.append("merge_plan_not_mergeable")
    if plan.grade != "ready":
        reasons.append(f"run_grade_not_ready:{plan.grade}")
    if plan.risk_flags:
        reasons.extend(f"risk:{flag}" for flag in plan.risk_flags)
    if not plan.files:
        reasons.append("no_files_to_apply")
    if autonomy == ApplyAutonomyProfile.TRUSTED and len(plan.files) > 8:
        reasons.append("trusted_profile_file_limit_exceeded")
    if autonomy == ApplyAutonomyProfile.AGGRESSIVE:
        reasons = [reason for reason in reasons if not reason.startswith("trusted_profile_")]
    allowed = autonomy != ApplyAutonomyProfile.MANUAL and not reasons
    return AutoApplyDecision(allowed, autonomy, tuple(dict.fromkeys(reasons)))