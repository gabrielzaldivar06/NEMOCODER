import tempfile
import unittest
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

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

    def test_quick_suite_disables_repair_for_latency_measurement(self) -> None:
        quick = benchmark_cases_for_suite("quick")
        self.assertTrue(all(case.repair_budget == 0 for case in quick))

    def test_quick_quality_suite_enables_targeted_repair(self) -> None:
        quick_quality = benchmark_cases_for_suite("quick-quality")
        self.assertEqual(len(quick_quality), 2)
        self.assertEqual(quick_quality[0].repair_budget, 1)
        self.assertEqual(quick_quality[1].repair_budget, 1)

    def test_quick_quality_suite_alias_is_supported(self) -> None:
        quick_quality = benchmark_cases_for_suite("quick-quality")
        quick_quality_alias = benchmark_cases_for_suite("quick_quality")
        self.assertEqual([case.case_id for case in quick_quality], [case.case_id for case in quick_quality_alias])
        self.assertEqual([case.repair_budget for case in quick_quality], [case.repair_budget for case in quick_quality_alias])

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

    def test_real_provider_benchmark_does_not_use_synthetic_validation_command(self) -> None:
        case = default_benchmark_cases()[0]
        fake_result = SimpleNamespace(
            effective_mutation_result=SimpleNamespace(duration_ms=10, stdout="", stderr="", token_usage=None),
            mutation_result=SimpleNamespace(duration_ms=10, stdout="", stderr="", token_usage=None),
            effective_changed_files=("bench-output/algorithms.py",),
            portfolio={},
            repair_plan=SimpleNamespace(attempts=()),
            validation=SimpleNamespace(passed=True),
            nemo_results=(),
            timeline=SimpleNamespace(events=()),
            artifacts=(),
        )

        with tempfile.TemporaryDirectory() as tmp:
            with patch("nemo_coding_platform.core.llm_benchmark.execute_headless_handoff", return_value=fake_result) as mocked:
                run_llm_benchmark(
                    repo_path=tmp,
                    provider_mode="subprocess",
                    repeats=1,
                    warmup=False,
                    cases=(case,),
                )

                request = mocked.call_args.args[0]
                self.assertEqual(request.validation_commands, (f"{sys.executable} --version",))
                self.assertNotIn("mutation changed files", request.validation_commands)
                kwargs = mocked.call_args.kwargs
        self.assertEqual(kwargs["validation_python_scripts"], case.validation_python_scripts)


if __name__ == "__main__":
    unittest.main()
