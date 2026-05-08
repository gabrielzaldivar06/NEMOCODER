import unittest

from nemo_coding_platform.core.evals import build_run_metrics_report
from nemo_coding_platform.core.persistence import export_run_metrics


def _base_payload() -> dict:
    return {
        "task": {"id": "task-1"},
        "run": {"id": "run-1"},
        "timeline": [
            {"kind": "permission_decided", "summary": "approved in sandbox"},
            {"kind": "mutation_created", "summary": "created patch"},
            {"kind": "validation_run", "summary": "validation passed"},
            {"kind": "checkpoint", "summary": "checkpoint written"},
            {"kind": "memory_written", "summary": "memory writeback prepared"},
        ],
        "artifacts": [
            {"artifact_type": "review_package", "path": "review-package.md", "summary": "review package"},
        ],
        "validation": {
            "results": [
                {"status": "passed", "summary": "unit tests passed", "command": {"command": "python -m unittest"}},
                {"status": "passed", "summary": "lint passed", "command": {"command": "ruff check"}},
            ]
        },
        "mutation_result": {"returncode": 0, "changed_files": ["src/a.py"]},
    }


class EvalsExportTests(unittest.TestCase):
    def test_metrics_report_happy_path(self) -> None:
        report = build_run_metrics_report(_base_payload())

        self.assertEqual(report.task_id, "task-1")
        self.assertEqual(report.run_id, "run-1")
        self.assertEqual(report.success_rate, 1.0)
        self.assertEqual(report.validation_pass_rate, 1.0)
        self.assertEqual(report.override_rate, 0.0)
        self.assertEqual(report.stale_memory_incidents, 0)
        self.assertEqual(report.tool_failure_rate, 0.0)

    def test_metrics_report_detects_failures_and_stale_incidents(self) -> None:
        payload = _base_payload()
        payload["timeline"] = [
            {"kind": "permission_denied", "summary": "blocked by policy"},
            {"kind": "permission_decided", "summary": "override denied due to stale memory"},
            {"kind": "mutation_created", "summary": "tool run failed"},
            {"kind": "validation_run", "summary": "stale runtime pass prevented"},
        ]
        payload["validation"] = {
            "results": [
                {"status": "passed", "summary": "one passed", "command": {"command": "cmd-1"}},
                {"status": "failed", "summary": "stale runtime detected", "command": {"command": "cmd-2"}},
            ]
        }
        payload["artifacts"] = []
        payload["mutation_result"] = {"returncode": 1, "changed_files": []}

        report = build_run_metrics_report(payload)

        self.assertEqual(report.success_rate, 0.0)
        self.assertEqual(report.validation_pass_rate, 0.5)
        self.assertEqual(report.override_rate, 1.0)
        self.assertGreaterEqual(report.stale_memory_incidents, 1)
        self.assertGreater(report.tool_failure_rate, 0.0)
        self.assertGreaterEqual(report.tool_failures_total, 2)

    def test_export_run_metrics_returns_serializable_payload(self) -> None:
        exported = export_run_metrics(_base_payload())

        self.assertEqual(exported["task_id"], "task-1")
        self.assertEqual(exported["run_id"], "run-1")
        self.assertIn("success_rate", exported)
        self.assertIn("validation_pass_rate", exported)
        self.assertIn("override_rate", exported)
        self.assertIn("stale_memory_incidents", exported)
        self.assertIn("tool_failure_rate", exported)
        self.assertIsInstance(exported["readiness_reasons"], list)


if __name__ == "__main__":
    unittest.main()