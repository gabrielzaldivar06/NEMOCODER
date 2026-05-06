from __future__ import annotations

from nemo_coding_platform.core.aider_interface import MutationResult
from nemo_coding_platform.core.task_run import MemoryTrace
from nemo_coding_platform.core.validation import ValidationSuiteResult


def build_checkpoint_markdown(
    phase: str,
    changed_files: tuple[str, ...],
    validation: ValidationSuiteResult,
    memory_traces: tuple[MemoryTrace, ...],
    next_action: str,
    risk_flags: tuple[str, ...] = (),
    mutation_result: MutationResult | None = None,
) -> str:
    changed = "\n".join(f"- {path}" for path in changed_files) or "- none"
    memory = "\n".join(f"- {trace.nemo_tool}: {trace.summary}" for trace in memory_traces) or "- none"
    risks = "\n".join(f"- {risk}" for risk in risk_flags) or "- none"
    failures = "\n".join(
        f"- {result.command.command}: returncode={result.returncode} output={result.output or '<empty>'}"
        for result in validation.results
        if not result.passed and result.command.required
    ) or "- none"
    provider = "none"
    if mutation_result:
        model = mutation_result.model_profile.model if mutation_result.model_profile else "unknown-model"
        provider = f"{mutation_result.provider} model={model} changed={len(mutation_result.changed_files)}"
    return "\n".join(
        (
            "# Checkpoint",
            "",
            f"phase={phase}",
            f"provider={provider}",
            "",
            "## Changed Files",
            changed,
            "",
            "## Validation",
            validation.summary(),
            "",
            "## Failures",
            failures,
            "",
            "## NEMO Evidence",
            memory,
            "",
            "## Next Action",
            next_action,
            "",
            "## Risk Flags",
            risks,
        )
    )