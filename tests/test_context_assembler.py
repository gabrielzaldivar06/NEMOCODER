"""Tests for core/context_assembler.py."""
import pytest

from nemo_coding_platform.core.context_assembler import ContextPacket, assemble_context


# ---------------------------------------------------------------------------
# ContextPacket
# ---------------------------------------------------------------------------

class TestContextPacket:
    def test_utilization_zero_when_no_budget(self):
        p = ContextPacket(text="x", chars_budget=0)
        assert p.utilization == 0.0

    def test_utilization_computed(self):
        p = ContextPacket(text="x" * 100, chars_used=100, chars_budget=200)
        assert p.utilization == pytest.approx(0.5)

    def test_as_dict_shape(self):
        p = ContextPacket(text="hello", sections=["task_objective"], chars_used=5, chars_budget=100)
        d = p.as_dict()
        assert d["text"] == "hello"
        assert "sections" in d
        assert "evidence_handles" in d
        assert "utilization" in d


# ---------------------------------------------------------------------------
# assemble_context — no-run fast path
# ---------------------------------------------------------------------------

class TestAssembleContextNoRun:
    def test_no_run_returns_no_run_section(self):
        pkt = assemble_context()
        assert "no_run" in pkt.sections
        assert "No run is selected" in pkt.text

    def test_no_run_appends_cognitive_preload(self):
        pkt = assemble_context(cognitive_preload="Remember: always lint first.")
        assert "Remember: always lint first." in pkt.text

    def test_no_run_respects_max_chars(self):
        pkt = assemble_context(cognitive_preload="x" * 10000, max_chars=100)
        assert len(pkt.text) <= 100


# ---------------------------------------------------------------------------
# assemble_context — with run data
# ---------------------------------------------------------------------------

class TestAssembleContextWithRun:
    def _task(self, **kw):
        return {"id": "task-1", "objective": "Implement feature X", **kw}

    def _run(self, **kw):
        return {"id": "run-42", **kw}

    def test_includes_task_objective_section(self):
        pkt = assemble_context(task=self._task(), run=self._run(), max_chars=2000)
        assert "task_objective" in pkt.sections
        assert "task-1" in pkt.text
        assert "Implement feature X" in pkt.text

    def test_includes_nemo_context_when_present(self):
        portfolio = {"context": "NEMO memory facts here.", "evidence_handles": []}
        pkt = assemble_context(task=self._task(), run=self._run(), portfolio=portfolio, max_chars=2000)
        assert "nemo_context" in pkt.sections
        assert "NEMO memory facts here." in pkt.text

    def test_skips_nemo_context_section_when_empty(self):
        portfolio = {"context": "", "evidence_handles": []}
        pkt = assemble_context(task=self._task(), run=self._run(), portfolio=portfolio)
        assert "nemo_context" not in pkt.sections

    def test_includes_cognitive_preload_section(self):
        pkt = assemble_context(
            task=self._task(), run=self._run(),
            cognitive_preload="Reflexion: don't touch auth module.",
            max_chars=2000,
        )
        assert "cognitive_preload" in pkt.sections
        assert "don't touch auth module" in pkt.text

    def test_includes_run_metadata(self):
        pkt = assemble_context(task=self._task(), run=self._run(), mergeable=True)
        assert "Mergeable: True" in pkt.text

    def test_includes_changed_files(self):
        pkt = assemble_context(
            task=self._task(), run=self._run(),
            changed_files=("src/foo.py", "src/bar.py"),
        )
        assert "src/foo.py" in pkt.text

    def test_includes_risk_flags(self):
        pkt = assemble_context(
            task=self._task(), run=self._run(),
            risk_flags=("HIGH_RISK: db migration",),
        )
        assert "HIGH_RISK" in pkt.text

    def test_respects_max_chars(self):
        big_context = "x" * 5000
        portfolio = {"context": big_context, "evidence_handles": []}
        pkt = assemble_context(
            task=self._task(), run=self._run(),
            portfolio=portfolio,
            max_chars=500,
        )
        assert pkt.chars_used <= 500
        assert len(pkt.text) <= 500

    def test_chars_used_matches_text_length(self):
        pkt = assemble_context(task=self._task(), run=self._run(), max_chars=2000)
        assert pkt.chars_used == len(pkt.text)

    def test_chars_budget_set_correctly(self):
        pkt = assemble_context(task=self._task(), run=self._run(), max_chars=1234)
        assert pkt.chars_budget == 1234

    def test_parses_evidence_handles_from_portfolio(self):
        portfolio = {
            "context": "Some context",
            "evidence_handles": ["h-001", "h-002"],
        }
        pkt = assemble_context(task=self._task(), run=self._run(), portfolio=portfolio)
        assert len(pkt.evidence_handles) == 2
        assert pkt.evidence_handles[0].handle_id == "h-001"

    def test_no_evidence_handles_when_portfolio_empty(self):
        pkt = assemble_context(task=self._task(), run=self._run(), portfolio={})
        assert pkt.evidence_handles == []

    def test_task_title_used_when_objective_missing(self):
        pkt = assemble_context(task={"id": "t-1", "title": "My Title"}, run=self._run())
        assert "My Title" in pkt.text

    def test_unknown_objective_when_neither_field(self):
        pkt = assemble_context(task={"id": "t-1"}, run=self._run())
        assert "Untitled" in pkt.text
