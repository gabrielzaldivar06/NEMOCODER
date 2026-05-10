"""Tests for core/evidence_handle.py."""
import pytest

from nemo_coding_platform.core.evidence_handle import EvidenceHandle, parse_evidence_handles


# ---------------------------------------------------------------------------
# EvidenceHandle
# ---------------------------------------------------------------------------

class TestEvidenceHandle:
    def test_basic_fields(self):
        h = EvidenceHandle(handle_id="h-123", claim="The spec says X.", source="nemo")
        assert h.handle_id == "h-123"
        assert h.claim == "The spec says X."
        assert h.source == "nemo"

    def test_frozen(self):
        h = EvidenceHandle(handle_id="h-1", claim="c")
        with pytest.raises((AttributeError, TypeError)):
            h.handle_id = "changed"  # type: ignore

    def test_expand_with_no_nemo_call_returns_claim(self):
        h = EvidenceHandle(handle_id="h-1", claim="fallback text")
        assert h.expand(None) == "fallback text"

    def test_expand_calls_nemo_and_returns_content(self):
        def nemo_call(tool_name, **kwargs):
            assert tool_name == "expand_context_evidence"
            assert kwargs["handle"] == "h-abc"
            return {"content": "Full expanded content."}

        h = EvidenceHandle(handle_id="h-abc", claim="compact claim")
        result = h.expand(nemo_call)
        assert result == "Full expanded content."

    def test_expand_passes_query_when_provided(self):
        received = {}

        def nemo_call(tool_name, **kwargs):
            received.update(kwargs)
            return {"content": "content"}

        h = EvidenceHandle(handle_id="h-1", claim="c")
        h.expand(nemo_call, query="what is the spec?")
        assert received.get("query") == "what is the spec?"

    def test_expand_falls_back_on_empty_content(self):
        def nemo_call(tool_name, **kwargs):
            return {"content": ""}

        h = EvidenceHandle(handle_id="h-1", claim="fallback")
        assert h.expand(nemo_call) == "fallback"

    def test_expand_falls_back_on_nemo_exception(self):
        def nemo_call(tool_name, **kwargs):
            raise RuntimeError("network error")

        h = EvidenceHandle(handle_id="h-1", claim="safe fallback")
        assert h.expand(nemo_call) == "safe fallback"

    def test_expand_falls_back_on_missing_handle(self):
        def nemo_call(tool_name, **kwargs):
            return {}  # no content key

        h = EvidenceHandle(handle_id="h-missing", claim="claim text")
        assert h.expand(nemo_call) == "claim text"

    def test_as_dict_shape(self):
        h = EvidenceHandle(handle_id="h-1", claim="c", source="portfolio")
        d = h.as_dict()
        assert d["handle_id"] == "h-1"
        assert d["claim"] == "c"
        assert d["source"] == "portfolio"


# ---------------------------------------------------------------------------
# parse_evidence_handles
# ---------------------------------------------------------------------------

class TestParseEvidenceHandles:
    def test_parses_string_list(self):
        raw = ["h-1", "h-2"]
        handles = parse_evidence_handles(raw, source="test")
        assert len(handles) == 2
        assert handles[0].handle_id == "h-1"
        assert handles[1].handle_id == "h-2"
        assert all(h.source == "test" for h in handles)

    def test_parses_dict_list(self):
        raw = [{"handle": "h-abc", "claim": "The claim."}]
        handles = parse_evidence_handles(raw)
        assert handles[0].handle_id == "h-abc"
        assert handles[0].claim == "The claim."

    def test_parses_dict_with_handle_id_key(self):
        raw = [{"handle_id": "h-xyz", "content": "Content text"}]
        handles = parse_evidence_handles(raw)
        assert handles[0].handle_id == "h-xyz"
        assert handles[0].claim == "Content text"

    def test_skips_empty_strings(self):
        raw = ["", "  ", "h-1"]
        handles = parse_evidence_handles(raw)
        assert len(handles) == 1
        assert handles[0].handle_id == "h-1"

    def test_skips_dicts_with_no_handle(self):
        raw = [{"no_handle_key": "val"}]
        handles = parse_evidence_handles(raw)
        assert handles == []

    def test_returns_empty_for_non_list(self):
        assert parse_evidence_handles(None) == []
        assert parse_evidence_handles({}) == []
        assert parse_evidence_handles("string") == []

    def test_returns_empty_for_empty_list(self):
        assert parse_evidence_handles([]) == []
