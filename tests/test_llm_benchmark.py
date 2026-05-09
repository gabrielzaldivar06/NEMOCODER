import tempfile
import unittest
from pathlib import Path

from nemo_coding_platform.core.llm_benchmark import (
    benchmark_cases_for_suite,
    default_benchmark_cases,
    run_llm_benchmark,
    save_benchmark_report,
    summarize_iterations,
)


class LLMBenchmarkCoreTests(unittest.TestCase):
    def test_default_cases_are_stable_and_non_empty(self) -> None:
        cases = default_benchmark_cases()
        self.assertGreaterEqual(len(cases), 2)
        case_ids = {case.case_id for case in cases}
        self.assertEqual(len(case_ids), len(cases))
        self.assertTrue(all(case.target_files for case in cases))

    def test_standard_suite_is_larger_than_quick(self) -> None:
        quick = benchmark_cases_for_suite("quick")
        standard = benchmark_cases_for_suite("standard")
        self.assertLess(len(quick), len(standard))

    def test_run_llm_benchmark_fake_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = run_llm_benchmark(
                repo_path=tmp,
                provider_mode="fake",
                repeats=1,
                warmup=False,
            )

        payload = report.to_dict()
        self.assertEqual(payload["provider"], "fake")
        self.assertEqual(payload["summary"]["count"], len(payload["iterations"]))
        self.assertEqual(payload["summary"]["count"], len(default_benchmark_cases()))
        self.assertGreaterEqual(payload["summary"]["success_rate"], 0.0)
        self.assertLessEqual(payload["summary"]["success_rate"], 1.0)

    def test_save_benchmark_report_writes_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = run_llm_benchmark(
                repo_path=tmp,
                provider_mode="fake",
                repeats=1,
                warmup=False,
            )
            target = Path(tmp) / "benchmark.json"
            saved = save_benchmark_report(report, target)
            self.assertEqual(saved.name, "benchmark.json")
            self.assertTrue(saved.exists())

    def test_summarize_iterations_handles_empty(self) -> None:
        summary = summarize_iterations([])
        self.assertEqual(summary["count"], 0)
        self.assertEqual(summary["success_rate"], 0.0)


if __name__ == "__main__":
    unittest.main()
