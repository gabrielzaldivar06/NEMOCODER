import unittest
import os
import time
import tempfile
from pathlib import Path
from nemo_coding_platform.core.watch_mode import FileWatcher, AI_COMMENT_PATTERN


class TestWatchMode(unittest.TestCase):
    def test_pattern_matching(self):
        self.assertTrue(AI_COMMENT_PATTERN.search("# ai! Fix this"))
        self.assertTrue(AI_COMMENT_PATTERN.search("// ai? Refactor"))
        self.assertTrue(AI_COMMENT_PATTERN.search("-- ai! Test"))
        self.assertTrue(AI_COMMENT_PATTERN.search("; ai improve"))
        self.assertFalse(AI_COMMENT_PATTERN.search("# just a comment"))

    def test_scan_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            test_py = base / "test.py"
            test_py.write_text("def foo():\n    pass # ai! improve this function\n")
            
            watcher = FileWatcher(tmpdir, lambda x: None)
            requests = watcher.scan_file(test_py)
            
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0].objective, "improve this function")
            self.assertEqual(requests[0].line_number, 2)

    def test_polling_detection(self):
        detected_requests = []
        def callback(reqs):
            detected_requests.extend(reqs)

        with tempfile.TemporaryDirectory() as tmpdir:
            watcher = FileWatcher(tmpdir, callback)
            watcher.start()
            
            try:
                # Create a file with a comment
                test_py = Path(tmpdir) / "test.py"
                test_py.write_text("# ai! do something")
                
                # Wait for poll (polling every 2s)
                time.sleep(3.0)
                
                self.assertEqual(len(detected_requests), 1)
                self.assertEqual(detected_requests[0].objective, "do something")
            finally:
                watcher.stop()


if __name__ == "__main__":
    unittest.main()
