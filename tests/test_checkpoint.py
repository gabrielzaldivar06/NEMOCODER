import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nemo_coding_platform.core.checkpoint import load_execution_snapshot, save_execution_snapshot
from nemo_coding_platform.core.contracts import ExecutionPhase


class CheckpointIOTests(unittest.TestCase):
    def test_load_execution_snapshot_reads_saved_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint_path = save_execution_snapshot(
                runtime_path=tmp,
                checkpoint_id="checkpoint-execute-00001",
                phase=ExecutionPhase.EXECUTE,
                phase_elapsed_seconds=1.0,
                global_elapsed_seconds=2.0,
                changed_files=["src/module.py"],
                timeline_events=[],
                validation_results=["ok"],
            )
            payload = load_execution_snapshot(checkpoint_path)

        self.assertEqual(payload["checkpoint_format"], "nemo_checkpoint_v2")
        self.assertEqual(payload["snapshot"]["checkpoint_id"], "checkpoint-execute-00001")

    def test_load_execution_snapshot_retries_on_transient_empty_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint_file = Path(tmp) / "checkpoint.json"
            valid_payload = {
                "schema_version": 2,
                "checkpoint_format": "nemo_checkpoint_v2",
                "snapshot": {"checkpoint_id": "checkpoint-1"},
            }
            valid_json = json.dumps(valid_payload)
            checkpoint_file.write_text(valid_json, encoding="utf-8")

            with patch.object(Path, "read_text", side_effect=["", valid_json]):
                with patch("nemo_coding_platform.core.checkpoint.time.sleep"):
                    payload = load_execution_snapshot(checkpoint_file, retries=2, retry_delay_seconds=0.0)

        self.assertEqual(payload["snapshot"]["checkpoint_id"], "checkpoint-1")


if __name__ == "__main__":
    unittest.main()
