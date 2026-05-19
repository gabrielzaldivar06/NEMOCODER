"""Tests for the Progressive Refinement Loop (Sprint AAA — 2026-05-18)."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import nemo_coding_platform.mission_control_server as mcs
from nemo_coding_platform.mission_control_server import MissionControlServerConfig


# ── Shared helpers ────────────────────────────────────────────────────────────

def _make_config(tmp: str) -> MissionControlServerConfig:
    root = Path(tmp)
    repo = root / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / ".git").mkdir(exist_ok=True)
    return MissionControlServerConfig.from_paths(
        repo, root / "runtimes", root / "apply", memory_db=None
    )


def _fake_nemo(config, tool_calls, tool_name, *, lifecycle_phase=None, nemo_mcp_url="", **kw):
    return {
        "ok": True,
        "payload": {"context": "", "portfolio": {"estimated_tokens": 0}, "memories": []},
    }


def _lm_factory(gen_response: str, score: int):
    """_plan_lm_call side_effect: returns gen_response for generation, JSON score for critique."""
    def _fake(payload, system, user, **kw):
        if "evaluator" in system.lower():
            return (
                f'{{"score":{score},"present":[],"missing":[],'
                f'"improvements":[],"summary":"test"}}'
            )
        return gen_response
    return _fake


def _lm_sequence(gen_response: str, scores: list):
    """_plan_lm_call side_effect: cycles through different scores per critique call."""
    _it = iter(scores)
    _last = scores[-1]

    def _fake(payload, system, user, **kw):
        if "evaluator" in system.lower():
            return (
                f'{{"score":{next(_it, _last)},"present":[],"missing":[],'
                f'"improvements":[],"summary":"test"}}'
            )
        return gen_response
    return _fake


def _run_plan(config, scores, max_iter=5, gen="print('hello')",
              model_base_url="http://localhost:1234/v1"):
    """Run api_agent_plan_gen with mocked LM + NEMO + disabled sleep. Return (done_event, iter_events)."""
    with patch("nemo_coding_platform.mission_control_server._plan_lm_call",
               side_effect=_lm_sequence(gen, scores)), \
         patch("nemo_coding_platform.mission_control_server._nemo_chat_tool_call",
               side_effect=_fake_nemo), \
         patch("time.sleep"):
        events = list(mcs.api_agent_plan_gen(
            config,
            {
                "objective": "print hello world",
                "max_iterations": max_iter,
                "quality_threshold": 99.0,  # intentionally unreachable — forces new stop logic
                "model_base_url": model_base_url,
                "parallel_candidates": False,  # prevent iterator race in _lm_sequence
            },
        ))
    done = next(e for e in events if e["type"] == "done")
    iters = [e for e in events if e["type"] == "iteration"]
    return done, iters


# ── Unit tests for pure helpers ───────────────────────────────────────────────

class TestAdaptiveTemp(unittest.TestCase):
    def test_score_below_6_returns_08(self):
        self.assertEqual(mcs._adaptive_temp(0.0), 0.8)
        self.assertEqual(mcs._adaptive_temp(5.9), 0.8)

    def test_score_6_to_89_returns_05(self):
        self.assertEqual(mcs._adaptive_temp(6.0), 0.5)
        self.assertEqual(mcs._adaptive_temp(8.9), 0.5)

    def test_score_9_and_above_returns_02(self):
        self.assertEqual(mcs._adaptive_temp(9.0), 0.2)
        self.assertEqual(mcs._adaptive_temp(10.0), 0.2)


class TestBuildCritiqueSys(unittest.TestCase):
    def test_contains_json_format_in_all_modes(self):
        for score in (0.0, 5.0, 7.0, 9.5):
            result = mcs._build_critique_sys(score)
            self.assertIn('"score"', result)
            self.assertIn('"improvements"', result)

    def test_correction_mode_for_score_below_6(self):
        result = mcs._build_critique_sys(5.9)
        self.assertIn("CORRECTION", result)

    def test_improvement_mode_for_score_6_to_89(self):
        result = mcs._build_critique_sys(7.0)
        self.assertIn("IMPROVEMENT", result)

    def test_perfection_mode_for_score_9_and_above(self):
        result = mcs._build_critique_sys(9.5)
        self.assertIn("PERFECTION", result)

    def test_boundary_at_6(self):
        self.assertIn("IMPROVEMENT", mcs._build_critique_sys(6.0))
        self.assertIn("CORRECTION", mcs._build_critique_sys(5.99))

    def test_boundary_at_9(self):
        self.assertIn("PERFECTION", mcs._build_critique_sys(9.0))
        self.assertIn("IMPROVEMENT", mcs._build_critique_sys(8.99))


# ── Stop conditions ───────────────────────────────────────────────────────────

class TestConsecutivePerfectStop(unittest.TestCase):
    def test_stops_after_two_nines_half(self):
        with tempfile.TemporaryDirectory() as tmp:
            done, iters = _run_plan(_make_config(tmp), scores=[10, 10, 10, 10], max_iter=5)
        self.assertEqual(done["stop_reason"], "consecutive_perfect")
        self.assertEqual(len(iters), 2)

    def test_resets_counter_when_score_drops(self):
        with tempfile.TemporaryDirectory() as tmp:
            done, iters = _run_plan(_make_config(tmp), scores=[10, 8, 10, 10, 10], max_iter=5)
        self.assertEqual(done["stop_reason"], "consecutive_perfect")
        self.assertEqual(len(iters), 4)

    def test_single_perfect_does_not_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            done, iters = _run_plan(_make_config(tmp), scores=[10, 7, 7, 7, 7], max_iter=5)
        self.assertNotEqual(done["stop_reason"], "consecutive_perfect")


class TestRegressionStop(unittest.TestCase):
    def test_stops_on_regression_greater_than_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            done, iters = _run_plan(_make_config(tmp), scores=[8, 3, 10, 10], max_iter=5)
        self.assertEqual(done["stop_reason"], "regression")
        self.assertEqual(len(iters), 2)

    def test_no_regression_when_drop_exactly_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            done, iters = _run_plan(_make_config(tmp), scores=[8, 6, 6, 6, 6], max_iter=5)
        self.assertNotEqual(done["stop_reason"], "regression")

    def test_no_regression_check_on_first_iteration(self):
        with tempfile.TemporaryDirectory() as tmp:
            done, iters = _run_plan(_make_config(tmp), scores=[1, 1, 1, 1, 1], max_iter=5)
        self.assertNotEqual(done["stop_reason"], "regression")


class TestPlateauStop(unittest.TestCase):
    def test_stops_after_two_stagnant_iters_when_best_above_6(self):
        with tempfile.TemporaryDirectory() as tmp:
            done, iters = _run_plan(_make_config(tmp), scores=[7, 7, 7, 7], max_iter=5)
        self.assertEqual(done["stop_reason"], "plateau")
        self.assertEqual(len(iters), 3)

    def test_no_plateau_when_best_score_below_6(self):
        with tempfile.TemporaryDirectory() as tmp:
            done, iters = _run_plan(_make_config(tmp), scores=[5, 5, 5, 5, 5], max_iter=5)
        self.assertNotEqual(done["stop_reason"], "plateau")

    def test_plateau_resets_on_improvement(self):
        with tempfile.TemporaryDirectory() as tmp:
            done, iters = _run_plan(_make_config(tmp), scores=[7, 7, 8, 8, 8], max_iter=6)
        self.assertEqual(done["stop_reason"], "plateau")
        self.assertEqual(len(iters), 5)


class TestMaxIterationsStop(unittest.TestCase):
    def test_runs_all_iters_when_no_other_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            done, iters = _run_plan(_make_config(tmp), scores=[5, 6, 7, 8, 9], max_iter=5)
        self.assertEqual(done["stop_reason"], "max_iterations")
        self.assertEqual(len(iters), 5)


# ── Done event correctness ────────────────────────────────────────────────────

class TestDoneEvent(unittest.TestCase):
    def test_final_score_is_best_not_last_iteration(self):
        with tempfile.TemporaryDirectory() as tmp:
            done, _ = _run_plan(_make_config(tmp), scores=[8, 3], max_iter=5)
        self.assertEqual(done["final_score"], 8.0)

    def test_stop_reason_present_in_done_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            done, _ = _run_plan(_make_config(tmp), scores=[5, 6, 7, 8, 9], max_iter=5)
        self.assertIn("stop_reason", done)
        self.assertIsInstance(done["stop_reason"], str)

    def test_done_event_has_best_score_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            done, _ = _run_plan(_make_config(tmp), scores=[6, 8, 7], max_iter=5)
        self.assertIn("best_score", done)
        self.assertEqual(done["best_score"], 8.0)


# ── Conditional sleep ─────────────────────────────────────────────────────────

class TestConditionalSleep(unittest.TestCase):
    def test_no_sleep_for_remote_nim_url(self):
        sleep_calls = []
        with tempfile.TemporaryDirectory() as tmp:
            with patch("nemo_coding_platform.mission_control_server._plan_lm_call",
                       side_effect=_lm_factory("print('hi')", 5)), \
                 patch("nemo_coding_platform.mission_control_server._nemo_chat_tool_call",
                       side_effect=_fake_nemo), \
                 patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
                list(mcs.api_agent_plan_gen(
                    _make_config(tmp),
                    {
                        "objective": "print hello",
                        "max_iterations": 1,
                        "quality_threshold": 99.0,
                        "model_base_url": "https://integrate.api.nvidia.com/v1",
                    },
                ))
        self.assertEqual(sleep_calls, [], f"Expected no sleep for remote URL, got: {sleep_calls}")

    def test_sleep_is_called_for_localhost_url(self):
        sleep_calls = []
        with tempfile.TemporaryDirectory() as tmp:
            with patch("nemo_coding_platform.mission_control_server._plan_lm_call",
                       side_effect=_lm_factory("print('hi')", 5)), \
                 patch("nemo_coding_platform.mission_control_server._nemo_chat_tool_call",
                       side_effect=_fake_nemo), \
                 patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
                list(mcs.api_agent_plan_gen(
                    _make_config(tmp),
                    {
                        "objective": "print hello",
                        "max_iterations": 1,
                        "quality_threshold": 99.0,
                        "model_base_url": "http://localhost:1234/v1",
                    },
                ))
        self.assertGreater(len(sleep_calls), 0, "Expected sleep calls for localhost URL")


if __name__ == "__main__":
    unittest.main()
