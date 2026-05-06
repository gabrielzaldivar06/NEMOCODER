from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from nemo_coding_platform.core.memory import NEMO_TOOL_REGISTRY, NemoToolRisk, PHASE_PORTFOLIOS
from nemo_coding_platform.core.orchestrator import DEFAULT_WORKFLOW
from nemo_coding_platform.core.policy import default_supervised_rules
from nemo_coding_platform.core.product import AUTONOMY_CONTRACTS, DESKTOP_PRODUCT
from nemo_coding_platform.core.quality import DEFAULT_MUTATION_POLICY, DEFAULT_VALIDATIONS


class Plane(StrEnum):
    QUALITY = "quality"
    RUNTIME = "runtime"
    ORCHESTRATION = "orchestration"
    POLICY = "policy"
    MEMORY = "memory"


@dataclass(frozen=True, slots=True)
class PlaneSummary:
    plane: Plane
    purpose: str


@dataclass(frozen=True, slots=True)
class SystemBlueprint:
    name: str
    planes: tuple[PlaneSummary, ...]

    def to_text(self) -> str:
        lines = [self.name, ""]
        lines.append("Product target:")
        lines.append(f"- target_surface={DESKTOP_PRODUCT.target_surface}")
        lines.append(f"- cli_role={DESKTOP_PRODUCT.cli_role}")
        lines.append(f"- desktop_shell={DESKTOP_PRODUCT.shell}")
        lines.append(f"- backend_protocol={DESKTOP_PRODUCT.backend_protocol}")
        lines.append("")
        lines.append("Planes:")
        for plane in self.planes:
            lines.append(f"- {plane.plane}: {plane.purpose}")
        lines.append("")
        lines.append("Workflow phases:")
        for phase in DEFAULT_WORKFLOW.phases:
            lines.append(
                f"- {phase.phase}: mode={phase.portfolio_mode}, read_only={phase.read_only}, "
                f"mutation_allowed={phase.mutation_allowed}, approval={phase.approval_level}, "
                f"review_gate={phase.review_gate_required}"
            )
        lines.append("")
        lines.append("NEMO portfolio contracts:")
        for phase, contract in PHASE_PORTFOLIOS.items():
            lines.append(
                f"- {phase}: mode={contract.mode}, reads={len(contract.reads)}, "
                f"writes={len(contract.writes)}, expands_evidence={contract.expands_evidence}"
            )
        lines.append("")
        lines.append("NEMO tool registry:")
        lines.append(f"- total_tools={len(NEMO_TOOL_REGISTRY)}")
        for risk in NemoToolRisk:
            count = sum(1 for tool in NEMO_TOOL_REGISTRY if tool.risk == risk)
            lines.append(f"- risk={risk}: {count}")
        lines.append("")
        lines.append("Autonomy contracts:")
        for level, contract in AUTONOMY_CONTRACTS.items():
            lines.append(
                f"- {level}: isolation={contract.requires_isolation}, background={contract.can_run_background}, "
                f"subagents={contract.can_spawn_subagents}, direct_main_write={contract.can_write_main_workspace_directly}, "
                f"review_before_merge={contract.requires_review_before_merge}"
            )
        lines.append("")
        lines.append("Mutation policy:")
        lines.append(f"- only_quality_core_writes={DEFAULT_MUTATION_POLICY.only_quality_core_writes}")
        lines.append(f"- dry_run_required={DEFAULT_MUTATION_POLICY.dry_run_required}")
        lines.append(f"- review_gate_before_commit={DEFAULT_MUTATION_POLICY.review_gate_before_commit}")
        lines.append("")
        lines.append("Default validations:")
        for command in DEFAULT_VALIDATIONS:
            lines.append(f"- {command.name}: {command.command}")
        lines.append("")
        lines.append("Policy defaults:")
        for rule in default_supervised_rules():
            lines.append(
                f"- permission={rule.permission} pattern={rule.pattern} action={rule.action} precedence={rule.precedence}"
            )
        return "\n".join(lines)


def default_blueprint() -> SystemBlueprint:
    return SystemBlueprint(
        name="NEMO-native coding platform",
        planes=(
            PlaneSummary(Plane.QUALITY, "Owns git-aware mutations and validation."),
            PlaneSummary(Plane.RUNTIME, "Owns execution lifecycle and sandbox abstraction."),
            PlaneSummary(Plane.ORCHESTRATION, "Owns supervised plan/execute/review flow."),
            PlaneSummary(Plane.POLICY, "Owns allow/deny/ask rules and approvals."),
            PlaneSummary(Plane.MEMORY, "Owns all NEMO tools, portfolio-driven context, evidence, and feedback."),
        ),
    )
