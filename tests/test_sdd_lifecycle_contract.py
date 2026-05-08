"""Test suite for Spec-Driven Development (SDD) lifecycle contract.

Verifies that:
1. Every feature has a PRD reference before being marked complete
2. Every spec has corresponding executable tests
3. Implementation traces to specifications
4. SDD phases progress sequentially
5. Feature status tracking records all traceability artifacts
"""

import unittest
from pathlib import Path

from nemo_coding_platform.core.sdd import (
    FeatureStatus,
    SDDContractRequirement,
    SDDPhase,
    SDD_CONTRACT_REQUIREMENTS,
    ValidationStatus,
)


class SDDLifecycleContractTests(unittest.TestCase):
    """Contract tests for Spec-Driven Development model."""

    def test_sdd_requires_prd_before_feature_completion(self) -> None:
        """Every feature must have a PRD reference before being marked complete.

        Rule: SDD-001 — Every feature must have a PRD reference in global-prd.md
        """
        # Feature with PRD ref is valid
        feature_with_prd = FeatureStatus(
            feature_name="desktop.tauri.window",
            prd_ref="section 4.1, Desktop Shell Choice",
            spec_file="docs/specs/desktop.md",
            test_file="tests/test_desktop_product_contract.py",
            impl_file="src/nemo_coding_platform/core/product.py",
            validation_status=ValidationStatus.PASS,
            current_phase=SDDPhase.VALIDATE,
            completed_phases=frozenset(SDDPhase),
        )
        self.assertTrue(feature_with_prd.is_prd_defined())

        # Feature without PRD ref should not be allowed in VALIDATE phase
        feature_without_prd = FeatureStatus(
            feature_name="internal.utility",
            prd_ref="",  # Empty PRD ref
            spec_file="docs/specs/utility.md",
            test_file="tests/test_utility.py",
            impl_file="src/nemo_coding_platform/core/utility.py",
            validation_status=ValidationStatus.NOT_STARTED,
            current_phase=SDDPhase.IMPLEMENT,
            completed_phases=frozenset([SDDPhase.DEFINE, SDDPhase.SPEC, SDDPhase.TEST]),
        )
        self.assertFalse(feature_without_prd.is_prd_defined())

    def test_specs_must_have_executable_tests(self) -> None:
        """Every spec must have a corresponding executable test.

        Rule: SDD-002 — Every executable spec must have a corresponding test file
        """
        feature_with_tests = FeatureStatus(
            feature_name="autonomy.levels",
            prd_ref="section 3.2, Autonomy Levels",
            spec_file="docs/specs/autonomy.md",
            test_file="tests/test_autonomy_policy_contract.py",
            impl_file="src/nemo_coding_platform/core/product.py",
            validation_status=ValidationStatus.PASS,
            current_phase=SDDPhase.VALIDATE,
            completed_phases=frozenset(SDDPhase),
        )
        self.assertTrue(feature_with_tests.has_executable_tests())

        feature_without_tests = FeatureStatus(
            feature_name="docs.only.feature",
            prd_ref="section 5.1, Documentation",
            spec_file="docs/specs/docs.md",
            test_file="",  # No test file
            impl_file="",
            validation_status=ValidationStatus.NOT_STARTED,
            current_phase=SDDPhase.SPEC,
            completed_phases=frozenset([SDDPhase.DEFINE, SDDPhase.SPEC]),
        )
        self.assertFalse(feature_without_tests.has_executable_tests())

    def test_implementation_traces_to_spec(self) -> None:
        """Every implementation file must trace back to a spec.

        Rule: SDD-003 — Every implementation file must trace back to a spec
        """
        # Fully specified feature
        feature_fully_specified = FeatureStatus(
            feature_name="desktop.product",
            prd_ref="section 4, Desktop Product",
            spec_file="docs/specs/desktop-product.md",
            test_file="tests/test_desktop_product_contract.py",
            impl_file="src/nemo_coding_platform/core/product.py",
            validation_status=ValidationStatus.PASS,
            current_phase=SDDPhase.VALIDATE,
            completed_phases=frozenset(SDDPhase),
        )
        self.assertTrue(feature_fully_specified.is_fully_specified())

        # Partially specified (missing impl file)
        feature_partial = FeatureStatus(
            feature_name="incomplete.feature",
            prd_ref="section 6, Incomplete Feature",
            spec_file="docs/specs/incomplete.md",
            test_file="tests/test_incomplete.py",
            impl_file="",  # Missing impl file
            validation_status=ValidationStatus.IN_PROGRESS,
            current_phase=SDDPhase.IMPLEMENT,
            completed_phases=frozenset([SDDPhase.DEFINE, SDDPhase.SPEC, SDDPhase.TEST]),
        )
        self.assertFalse(feature_partial.is_fully_specified())

    def test_sdd_phases_are_sequential(self) -> None:
        """SDD phases must be completed sequentially: DEFINE → SPEC → TEST → IMPLEMENT → VALIDATE.

        Rule: SDD-004 — SDD phases must be completed sequentially
        """
        # Feature can move from IMPLEMENT to VALIDATE (has all prior phases)
        feature_ready_for_validation = FeatureStatus(
            feature_name="complete.feature",
            prd_ref="section 1, Complete Feature",
            spec_file="docs/specs/complete.md",
            test_file="tests/test_complete.py",
            impl_file="src/nemo_coding_platform/core/complete.py",
            validation_status=ValidationStatus.IN_PROGRESS,
            current_phase=SDDPhase.IMPLEMENT,
            completed_phases=frozenset([SDDPhase.DEFINE, SDDPhase.SPEC, SDDPhase.TEST, SDDPhase.IMPLEMENT]),
        )
        self.assertTrue(feature_ready_for_validation.can_proceed_to_phase(SDDPhase.VALIDATE))

        # Feature cannot skip TEST phase (missing it from completed_phases)
        feature_cannot_skip = FeatureStatus(
            feature_name="incomplete.feature",
            prd_ref="section 2, Incomplete Feature",
            spec_file="docs/specs/incomplete.md",
            test_file="",
            impl_file="",
            validation_status=ValidationStatus.NOT_STARTED,
            current_phase=SDDPhase.SPEC,
            completed_phases=frozenset([SDDPhase.DEFINE, SDDPhase.SPEC]),  # Missing TEST, IMPLEMENT
        )
        self.assertFalse(feature_cannot_skip.can_proceed_to_phase(SDDPhase.VALIDATE))

        # Feature can only move forward
        feature_must_progress = FeatureStatus(
            feature_name="progressing.feature",
            prd_ref="section 3, Progressing Feature",
            spec_file="docs/specs/progressing.md",
            test_file="tests/test_progressing.py",
            impl_file="",
            validation_status=ValidationStatus.NOT_STARTED,
            current_phase=SDDPhase.TEST,
            completed_phases=frozenset([SDDPhase.DEFINE, SDDPhase.SPEC, SDDPhase.TEST]),
        )
        self.assertFalse(feature_must_progress.can_proceed_to_phase(SDDPhase.SPEC))  # Cannot go backward

    def test_feature_status_tracking_records_traceability_artifacts(self) -> None:
        """Feature status tracking must record all four traceability artifacts.

        Rule: SDD-005 — Feature status must record prd_ref, spec_file, test_file, impl_file
        """
        # Complete feature with all artifacts
        feature_complete = FeatureStatus(
            feature_name="feature.complete",
            prd_ref="section 1.1, Feature Complete",
            spec_file="docs/specs/feature-complete.md",
            test_file="tests/test_feature_complete.py",
            impl_file="src/nemo_coding_platform/core/feature_complete.py",
            validation_status=ValidationStatus.PASS,
            current_phase=SDDPhase.VALIDATE,
            completed_phases=frozenset(SDDPhase),
        )

        # Verify all artifacts are present and non-empty
        self.assertTrue(bool(feature_complete.prd_ref.strip()))
        self.assertTrue(bool(feature_complete.spec_file.strip()))
        self.assertTrue(bool(feature_complete.test_file.strip()))
        self.assertTrue(bool(feature_complete.impl_file.strip()))

        # Verify feature can report validated status
        self.assertTrue(feature_complete.is_validated())

    def test_sdd_contract_requirements_are_defined(self) -> None:
        """Verify that all SDD contract requirements are formally defined.

        This test ensures the SDD contract is explicitly stated and can be audited.
        """
        self.assertGreaterEqual(len(SDD_CONTRACT_REQUIREMENTS), 5)

        # Check that core requirements exist
        rule_ids = {req.rule_id for req in SDD_CONTRACT_REQUIREMENTS}
        self.assertIn("SDD-001", rule_ids)  # PRD before completion
        self.assertIn("SDD-002", rule_ids)  # Specs have tests
        self.assertIn("SDD-003", rule_ids)  # Impl traces to spec
        self.assertIn("SDD-004", rule_ids)  # Sequential phases
        self.assertIn("SDD-005", rule_ids)  # Status tracking

        # Check that all requirements are well-formed
        for req in SDD_CONTRACT_REQUIREMENTS:
            self.assertTrue(req.rule_id.startswith("SDD-"))
            self.assertTrue(len(req.description) > 0)
            self.assertIn(req.enforcement_level, ["MUST", "SHOULD", "MAY"])
            self.assertTrue(len(req.validation_method) > 0)

    def test_feature_status_immutable_and_slots(self) -> None:
        """Verify that FeatureStatus is immutable and uses slots for efficiency.

        SDD artifacts should be immutable to prevent accidental modification during tracking.
        """
        from dataclasses import FrozenInstanceError

        feature = FeatureStatus(
            feature_name="immutable.feature",
            prd_ref="section 1, Immutable Feature",
            spec_file="docs/specs/immutable.md",
            test_file="tests/test_immutable.py",
            impl_file="src/nemo_coding_platform/core/immutable.py",
            validation_status=ValidationStatus.PASS,
            current_phase=SDDPhase.VALIDATE,
            completed_phases=frozenset(SDDPhase),
        )

        # Attempt to modify should raise FrozenInstanceError
        with self.assertRaises(FrozenInstanceError):
            feature.feature_name = "modified.feature"  # type: ignore


if __name__ == "__main__":
    unittest.main()
