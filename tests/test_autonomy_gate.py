import tempfile
import unittest
from pathlib import Path

from nemo_coding_platform.core.autonomy_gate import evaluate_auto_apply
from nemo_coding_platform.core.review_gate import build_merge_plan
from tests.test_review_gate import ready_payload


class AutonomyGateTests(unittest.TestCase):
    def test_manual_profile_requires_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            repo.mkdir()
            sandbox.mkdir()
            (sandbox / "created.txt").write_text("created", encoding="utf-8")
            plan = build_merge_plan(ready_payload(repo, sandbox, ["created.txt"]))

            decision = evaluate_auto_apply(plan, "manual")

        self.assertFalse(decision.allowed)
        self.assertIn("manual_profile_requires_review", decision.reasons)

    def test_trusted_profile_allows_ready_risk_free_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sandbox = root / "sandbox"
            repo.mkdir()
            sandbox.mkdir()
            (sandbox / "created.txt").write_text("created", encoding="utf-8")
            plan = build_merge_plan(ready_payload(repo, sandbox, ["created.txt"]))

            decision = evaluate_auto_apply(plan, "trusted")

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.to_dict()["profile"], "trusted")


if __name__ == "__main__":
    unittest.main()