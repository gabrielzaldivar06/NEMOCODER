import unittest
from nemo_coding_platform.core.context_compaction import (
    should_compact, 
    compact_context, 
    prune_tool_output,
    COMPACTION_THRESHOLD_CHARS
)


class TestContextCompaction(unittest.TestCase):
    def test_should_compact(self):
        self.assertFalse(should_compact("small context"))
        self.assertTrue(should_compact("a" * (COMPACTION_THRESHOLD_CHARS + 1)))

    def test_prune_tool_output(self):
        long_output = "Line 1\n" + "X" * 3000 + "\nLine Last"
        pruned = prune_tool_output(long_output, max_chars=1000)
        self.assertIn("truncated", pruned)
        self.assertIn("Line 1", pruned)
        self.assertIn("Line Last", pruned)
        self.assertLessEqual(len(pruned), 1200) # approximate due to truncation message

    def test_compact_context_with_headers(self):
        context = (
            "# Objective\nDo something\n\n" +
            "# Repair Evidence\n" + "A" * 5000 + "\n\n" +
            "# Repair Evidence\n" + "B" * 5000 + "\n\n" +
            "# Previous Diff\nDiff content"
        )
        # Threshold is 12000, context is ~10000 + overhead.
        # Let's force compaction by lowering threshold.
        compacted = compact_context(context, max_chars=5000)
        self.assertIn("# Objective", compacted)
        self.assertIn("# Previous Diff", compacted)
        self.assertIn("truncated", compacted) # One of the middle sections should be pruned or truncated
        self.assertLessEqual(len(compacted), 6000) # allowance for overhead

    def test_compact_no_headers(self):
        context = "A" * 10000
        compacted = compact_context(context, max_chars=2000)
        self.assertIn("[context compacted]", compacted)
        self.assertEqual(len(compacted), 2000 + len("\n... [context compacted] ...\n"))


if __name__ == "__main__":
    unittest.main()
