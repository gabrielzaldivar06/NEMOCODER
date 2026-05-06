import unittest

from nemo_coding_platform.core.policy import (
    PermissionAction,
    PermissionEvaluator,
    PermissionRule,
    default_supervised_rules,
)


class PermissionEvaluatorTests(unittest.TestCase):
    def test_last_match_wins_with_precedence(self) -> None:
        evaluator = PermissionEvaluator()
        rules = [
            PermissionRule("bash", "*", PermissionAction.ASK, precedence=10, source="base"),
            PermissionRule("bash", "git *", PermissionAction.ALLOW, precedence=20, source="git-safe"),
            PermissionRule("bash", "git push", PermissionAction.DENY, precedence=30, source="protected"),
        ]

        result = evaluator.evaluate("bash", "git push", rules)

        self.assertEqual(result.action, PermissionAction.DENY)
        self.assertEqual(result.matched_rule.source, "protected")

    def test_default_is_ask_when_no_rule_matches(self) -> None:
        evaluator = PermissionEvaluator()

        result = evaluator.evaluate("edit", "src/app.py", [])

        self.assertEqual(result.action, PermissionAction.ASK)
        self.assertIsNone(result.matched_rule)

    def test_supervised_defaults_block_network(self) -> None:
        evaluator = PermissionEvaluator()

        result = evaluator.evaluate("network", "https://example.com", default_supervised_rules())

        self.assertEqual(result.action, PermissionAction.DENY)


if __name__ == "__main__":
    unittest.main()
