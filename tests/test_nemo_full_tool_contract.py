import unittest

from nemo_coding_platform.core.contracts import ExecutionPhase
from nemo_coding_platform.core.memory import (
    NEMO_TOOL_REGISTRY,
    NemoToolRisk,
    NemoToolSuite,
    nemo_tools_by_suite,
    nemo_tools_for_phase,
)


class NemoFullToolContractTests(unittest.TestCase):
    def test_all_prd_nemo_suites_are_represented(self) -> None:
        represented_suites = {tool.suite for tool in NEMO_TOOL_REGISTRY}

        self.assertEqual(represented_suites, set(NemoToolSuite))

    def test_startup_and_context_tools_are_available_in_plan_phase(self) -> None:
        plan_tools = {tool.name for tool in nemo_tools_for_phase(ExecutionPhase.PLAN)}

        self.assertIn("prime_context", plan_tools)
        self.assertIn("context_bootstrap", plan_tools)
        self.assertIn("build_context_portfolio", plan_tools)
        self.assertIn("search_memories", plan_tools)

    def test_scheduling_writes_are_review_gated(self) -> None:
        for tool in NEMO_TOOL_REGISTRY:
            if tool.risk == NemoToolRisk.SCHEDULING_WRITE:
                self.assertEqual(tool.phase_access, (ExecutionPhase.REVIEW,))

    def test_destructive_nemo_tools_are_review_only(self) -> None:
        destructive_tools = [tool for tool in NEMO_TOOL_REGISTRY if tool.risk == NemoToolRisk.DESTRUCTIVE]

        self.assertTrue(destructive_tools)
        for tool in destructive_tools:
            self.assertEqual(tool.phase_access, (ExecutionPhase.REVIEW,))

    def test_each_suite_has_at_least_one_tool(self) -> None:
        for suite in NemoToolSuite:
            self.assertTrue(nemo_tools_by_suite(suite), msg=f"suite {suite} should not be empty")


if __name__ == "__main__":
    unittest.main()