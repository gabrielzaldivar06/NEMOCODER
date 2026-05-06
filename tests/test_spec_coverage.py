import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SpecCoverageTests(unittest.TestCase):
    def test_required_specs_exist(self) -> None:
        required_specs = {
            "docs/specs/aider-platform-wiring.md",
            "docs/specs/full-handoff-autonomy.md",
            "docs/specs/headless-run-mvp.md",
            "docs/specs/nemo-lifecycle.md",
            "docs/specs/quality-mutation-pipeline.md",
            "docs/specs/task-run-model.md",
            "docs/specs/worktree-runtime.md",
        }

        for spec in required_specs:
            self.assertTrue((ROOT / spec).is_file(), spec)

    def test_full_handoff_spec_defines_unattended_prd_to_code(self) -> None:
        text = (ROOT / "docs/specs/full-handoff-autonomy.md").read_text(encoding="utf-8")

        self.assertIn("PRD-to-code", text)
        self.assertIn("without human interaction", text)
        self.assertIn("review-gated", text)

    def test_headless_mvp_precedes_desktop_ui(self) -> None:
        text = (ROOT / "docs/specs/headless-run-mvp.md").read_text(encoding="utf-8")

        self.assertIn("before desktop UI", text)
        self.assertIn("not require desktop UI", text)
        self.assertIn("Full Handoff", text)


if __name__ == "__main__":
    unittest.main()