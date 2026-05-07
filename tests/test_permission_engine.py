import unittest
import os
import json
import tempfile
from pathlib import Path
from nemo_coding_platform.core.permission_engine import (
    PermissionRule, 
    PermissionRuleset, 
    PermissionAction, 
    default_ruleset, 
    load_ruleset_from_file
)
from nemo_coding_platform.core.product import AutonomyLevel


class TestPermissionEngine(unittest.TestCase):
    def test_evaluate_basic(self):
        rules = [
            PermissionRule("write_file", "*.py", PermissionAction.ALLOW),
            PermissionRule("write_file", "config.py", PermissionAction.DENY),
        ]
        ruleset = PermissionRuleset(rules)
        
        # Last matching rule wins
        self.assertEqual(ruleset.evaluate("write_file", "test.py"), PermissionAction.ALLOW)
        self.assertEqual(ruleset.evaluate("write_file", "config.py"), PermissionAction.DENY)
        self.assertEqual(ruleset.evaluate("write_file", "other.txt"), PermissionAction.DENY) # Default deny

    def test_evaluate_wildcard_permission(self):
        rules = [
            PermissionRule("*", "*", PermissionAction.ALLOW),
            PermissionRule("git_push", "*", PermissionAction.DENY),
        ]
        ruleset = PermissionRuleset(rules)
        
        self.assertEqual(ruleset.evaluate("write_file", "test.py"), PermissionAction.ALLOW)
        self.assertEqual(ruleset.evaluate("git_push", "anything"), PermissionAction.DENY)

    def test_default_ruleset(self):
        full = default_ruleset(AutonomyLevel.FULL_HANDOFF)
        self.assertEqual(full.evaluate("any", "any"), PermissionAction.ALLOW)
        
        manual = default_ruleset(AutonomyLevel.MANUAL)
        self.assertEqual(manual.evaluate("any", "any"), PermissionAction.DENY)

    def test_load_from_file(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            json.dump({
                "write_file": {"src/**": "allow", "secret.py": "deny"},
                "run_command": "allow"
            }, f)
            fname = f.name
            
        try:
            ruleset = load_ruleset_from_file(fname)
            self.assertEqual(ruleset.evaluate("write_file", "src/main.py"), PermissionAction.ALLOW)
            self.assertEqual(ruleset.evaluate("write_file", "secret.py"), PermissionAction.DENY)
            self.assertEqual(ruleset.evaluate("run_command", "any"), PermissionAction.ALLOW)
            self.assertEqual(ruleset.evaluate("other", "any"), PermissionAction.DENY)
        finally:
            os.unlink(fname)


if __name__ == "__main__":
    unittest.main()
