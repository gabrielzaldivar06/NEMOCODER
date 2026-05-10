"""Spec-Driven Development (SDD) contract and feature status tracking.

SDD enforces a sequential lifecycle: Define (PRD) → Spec → Test → Implement → Validate.
Every feature must have verifiable traceability from PRD through implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Optional


class SDDPhase(StrEnum):
    """Sequential phases of Spec-Driven Development."""

    DEFINE = "define"  # PRD exists and is approved
    SPEC = "spec"  # Executable specifications written
    TEST = "test"  # Test cases implemented
    IMPLEMENT = "implement"  # Production code written
    VALIDATE = "validate"  # All tests pass and behavior verified


class ValidationStatus(StrEnum):
    """Validation outcome for a feature."""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    PASS = "pass"
    FAIL = "fail"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class FeatureStatus:
    """Tracks SDD lifecycle status and traceability for a feature.

    Attributes:
        feature_name: Unique identifier for the feature (e.g., "desktop.tauri.window")
        prd_ref: Reference/location in global PRD (e.g., "section 4.1, Desktop Shell")
        spec_file: Path to executable specification file (e.g., "docs/specs/desktop.md")
        test_file: Path to test module (e.g., "tests/test_desktop_product_contract.py")
        impl_file: Path to main implementation file (e.g., "src/nemo_coding_platform/core/product.py")
        validation_status: Current validation outcome
        current_phase: Current SDD phase
        completed_phases: Set of SDD phases already completed
        notes: Optional implementation notes or blockers
    """

    feature_name: str
    prd_ref: str
    spec_file: str
    test_file: str
    impl_file: str
    validation_status: ValidationStatus
    current_phase: SDDPhase
    completed_phases: frozenset[SDDPhase] = field(default_factory=frozenset)
    notes: Optional[str] = None

    def is_prd_defined(self) -> bool:
        """Check if PRD reference is non-empty."""
        return bool(self.prd_ref.strip())

    def is_fully_specified(self) -> bool:
        """Check if all specification artifacts exist."""
        return (
            self.is_prd_defined()
            and bool(self.spec_file.strip())
            and bool(self.test_file.strip())
            and bool(self.impl_file.strip())
        )

    def has_executable_tests(self) -> bool:
        """Check if test file is assigned (assumes tests are executable)."""
        return bool(self.test_file.strip())

    def is_validated(self) -> bool:
        """Check if validation passed."""
        return self.validation_status == ValidationStatus.PASS

    def can_proceed_to_phase(self, next_phase: SDDPhase) -> bool:
        """Determine if feature can advance to next SDD phase.

        Enforces sequential phase progression:
        - DEFINE: always allowed (starting point)
        - SPEC: requires DEFINE completed
        - TEST: requires SPEC completed
        - IMPLEMENT: requires TEST completed
        - VALIDATE: requires IMPLEMENT completed
        """
        if next_phase == SDDPhase.DEFINE:
            return True

        phase_order = [SDDPhase.DEFINE, SDDPhase.SPEC, SDDPhase.TEST, SDDPhase.IMPLEMENT, SDDPhase.VALIDATE]
        current_index = phase_order.index(self.current_phase)
        next_index = phase_order.index(next_phase)

        # Can only move forward or stay in current phase
        if next_index < current_index:
            return False

        # Must complete all prior phases
        required_phases = set(phase_order[:next_index])
        return required_phases.issubset(self.completed_phases)


@dataclass(frozen=True, slots=True)
class SDDContractRequirement:
    """A single SDD contract requirement enforced across all features.

    Used in testing to verify that the codebase adheres to SDD principles.
    """

    rule_id: str
    description: str
    enforcement_level: str  # "MUST", "SHOULD", "MAY"
    validation_method: str  # e.g., "test_check", "file_scan", "ast_analysis"


# Global SDD contract requirements enforced by the test suite
SDD_CONTRACT_REQUIREMENTS = [
    SDDContractRequirement(
        rule_id="SDD-001",
        description="Every feature must have a PRD reference in global-prd.md",
        enforcement_level="MUST",
        validation_method="test_check",
    ),
    SDDContractRequirement(
        rule_id="SDD-002",
        description="Every executable spec must have a corresponding test file",
        enforcement_level="MUST",
        validation_method="test_check",
    ),
    SDDContractRequirement(
        rule_id="SDD-003",
        description="Every implementation file must trace back to a spec",
        enforcement_level="MUST",
        validation_method="ast_analysis",
    ),
    SDDContractRequirement(
        rule_id="SDD-004",
        description="SDD phases must be completed sequentially: DEFINE → SPEC → TEST → IMPLEMENT → VALIDATE",
        enforcement_level="MUST",
        validation_method="test_check",
    ),
    SDDContractRequirement(
        rule_id="SDD-005",
        description="Feature status tracking must record all four traceability artifacts",
        enforcement_level="MUST",
        validation_method="test_check",
    ),
]
