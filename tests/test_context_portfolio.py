import unittest

from nemo_coding_platform.core.context_portfolio import build_context_portfolio, make_portfolio_request
from nemo_coding_platform.core.contracts import ExecutionPhase
from nemo_coding_platform.core.memory import MemoryAtom, MemoryAtomType


class ContextPortfolioTests(unittest.TestCase):
    def test_low_budget_keeps_corrections_and_preferences_first(self) -> None:
        request = make_portfolio_request("build nemo code handoff", "nemo", ExecutionPhase.PLAN, token_budget=15)
        atoms = (
            MemoryAtom(MemoryAtomType.EVIDENCE, "Long optional background evidence that can be omitted safely.", "repo", "ev-1"),
            MemoryAtom(MemoryAtomType.CORRECTION, "NEMO CODE is the active base.", "project"),
            MemoryAtom(MemoryAtomType.PREFERENCE, "Use all NEMO tools.", "user"),
        )

        result = build_context_portfolio(request, atoms)

        self.assertIn("NEMO CODE is the active base", result.context)
        self.assertIn("Use all NEMO tools", result.context)
        self.assertEqual(result.evidence_handles, ("ev-1",))
        self.assertLessEqual(result.estimated_tokens, request.token_budget)

    def test_payload_is_json_ready(self) -> None:
        result = build_context_portfolio(make_portfolio_request("task", "topic"))

        payload = result.to_payload()

        self.assertIn("context", payload)
        self.assertEqual(payload["phase"], "plan")


if __name__ == "__main__":
    unittest.main()
