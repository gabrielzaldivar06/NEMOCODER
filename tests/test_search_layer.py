"""Tests for core/search_layer.py — SearchResult, SearchResponse, layers, runner."""
import pytest

from nemo_coding_platform.core.search_layer import (
    NemoMemoryLayer,
    NemoPortfolioLayer,
    SearchLayer,
    SearchResponse,
    SearchResult,
    UrlSourceLayer,
    resolve_layers,
    run_layered_search,
)


# ---------------------------------------------------------------------------
# SearchResult / SearchResponse
# ---------------------------------------------------------------------------

class TestSearchResult:
    def test_fields_present(self):
        r = SearchResult(source="nemo_memory", citation="NEMO#1", llm_facing_text="some text")
        assert r.source == "nemo_memory"
        assert r.citation == "NEMO#1"
        assert r.llm_facing_text == "some text"
        assert r.score == 0.0

    def test_frozen(self):
        r = SearchResult(source="x", citation="y", llm_facing_text="z")
        with pytest.raises((AttributeError, TypeError)):
            r.source = "changed"  # type: ignore


class TestSearchResponse:
    def test_llm_context_concatenates_with_citations(self):
        r1 = SearchResult(source="nemo_memory", citation="NEMO#1", llm_facing_text="Fact A")
        r2 = SearchResult(source="url", citation="URL·example", llm_facing_text="Fact B")
        resp = SearchResponse(query="q", results=[r1, r2])
        ctx = resp.llm_context
        assert "NEMO#1" in ctx
        assert "Fact A" in ctx
        assert "URL·example" in ctx
        assert "Fact B" in ctx

    def test_llm_context_skips_blank_results(self):
        r1 = SearchResult(source="x", citation="c", llm_facing_text="  ")
        resp = SearchResponse(query="q", results=[r1])
        assert resp.llm_context.strip() == ""

    def test_as_dict_shape(self):
        r = SearchResult(source="nemo_memory", citation="NEMO#1", llm_facing_text="text", score=0.9)
        resp = SearchResponse(query="q", results=[r])
        d = resp.as_dict()
        assert d["query"] == "q"
        assert len(d["results"]) == 1
        assert "llm_context_chars" in d
        assert d["results"][0]["score"] == 0.9


# ---------------------------------------------------------------------------
# Layers
# ---------------------------------------------------------------------------

def _make_nemo_call(tool_name, **kwargs):
    if tool_name == "search_memories":
        return {
            "memories": [
                {"content": "Memory fact 1", "memory_type": "project_fact", "importance_level": 8},
                {"content": "Memory fact 2", "memory_type": "correction", "importance_level": 9},
            ]
        }
    if tool_name == "build_context_portfolio":
        return {"context": "Portfolio context text", "estimated_tokens": 120}
    return {}


def _make_url_read_fn(content="Web content"):
    def read_fn(url):
        return ({"url": url, "title": "Example Page", "content": content}, False)
    return read_fn


class TestNemoMemoryLayer:
    def test_returns_results_from_nemo_call(self):
        layer = NemoMemoryLayer()
        results = layer.search("test query", limit=5, nemo_call=_make_nemo_call)
        assert len(results) == 2
        assert all(r.source == "nemo_memory" for r in results)

    def test_llm_facing_text_populated(self):
        layer = NemoMemoryLayer()
        results = layer.search("q", limit=5, nemo_call=_make_nemo_call)
        assert results[0].llm_facing_text == "Memory fact 1"

    def test_score_derived_from_importance(self):
        layer = NemoMemoryLayer()
        results = layer.search("q", limit=5, nemo_call=_make_nemo_call)
        assert results[0].score == pytest.approx(0.8)
        assert results[1].score == pytest.approx(0.9)

    def test_no_nemo_call_returns_empty(self):
        layer = NemoMemoryLayer()
        assert layer.search("q", limit=5) == []

    def test_nemo_call_exception_returns_empty(self):
        def bad_call(tool, **kwargs):
            raise RuntimeError("network error")
        layer = NemoMemoryLayer()
        assert layer.search("q", limit=5, nemo_call=bad_call) == []

    def test_limit_respected(self):
        layer = NemoMemoryLayer()
        results = layer.search("q", limit=1, nemo_call=_make_nemo_call)
        assert len(results) == 1


class TestNemoPortfolioLayer:
    def test_returns_one_result(self):
        layer = NemoPortfolioLayer()
        results = layer.search("q", limit=5, nemo_call=_make_nemo_call, token_budget=600, topic="test")
        assert len(results) == 1
        assert results[0].source == "nemo_portfolio"

    def test_llm_facing_text_contains_context(self):
        layer = NemoPortfolioLayer()
        results = layer.search("q", limit=5, nemo_call=_make_nemo_call, token_budget=600, topic="test")
        assert "Portfolio context text" in results[0].llm_facing_text

    def test_empty_context_returns_empty(self):
        def call_fn(tool, **kwargs):
            return {"context": "", "estimated_tokens": 0}
        layer = NemoPortfolioLayer()
        assert layer.search("q", limit=5, nemo_call=call_fn) == []

    def test_no_nemo_call_returns_empty(self):
        layer = NemoPortfolioLayer()
        assert layer.search("q", limit=5) == []


class TestUrlSourceLayer:
    def test_fetches_urls(self):
        layer = UrlSourceLayer()
        results = layer.search("q", limit=3, urls=["http://example.com"], url_read_fn=_make_url_read_fn())
        assert len(results) == 1
        assert results[0].source == "url"
        assert "Web content" in results[0].llm_facing_text

    def test_no_urls_returns_empty(self):
        layer = UrlSourceLayer()
        assert layer.search("q", limit=3, urls=[], url_read_fn=_make_url_read_fn()) == []

    def test_no_read_fn_returns_empty(self):
        layer = UrlSourceLayer()
        assert layer.search("q", limit=3, urls=["http://x.com"]) == []

    def test_limit_respected(self):
        urls = ["http://a.com", "http://b.com", "http://c.com"]
        layer = UrlSourceLayer()
        results = layer.search("q", limit=2, urls=urls, url_read_fn=_make_url_read_fn())
        assert len(results) == 2

    def test_failed_url_skipped(self):
        def bad_read(url):
            raise OSError("network fail")
        layer = UrlSourceLayer()
        results = layer.search("q", limit=3, urls=["http://bad.com"], url_read_fn=bad_read)
        assert results == []


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class TestRunLayeredSearch:
    def test_aggregates_results_from_all_layers(self):
        resp = run_layered_search(
            "test query",
            limit=5,
            nemo_call=_make_nemo_call,
            token_budget=600,
            topic="test",
            urls=["http://example.com"],
            url_read_fn=_make_url_read_fn(),
        )
        sources = {r.source for r in resp.results}
        assert "nemo_memory" in sources
        assert "nemo_portfolio" in sources
        assert "url" in sources

    def test_timings_recorded_for_each_layer(self):
        resp = run_layered_search("q", limit=5, nemo_call=_make_nemo_call)
        assert "nemo_memory" in resp.layer_timings_ms
        assert "nemo_portfolio" in resp.layer_timings_ms

    def test_layer_errors_captured_not_raised(self):
        def bad_call(tool, **kwargs):
            raise RuntimeError("boom")

        class BoomLayer(SearchLayer):
            @property
            def name(self):
                return "boom"

            def search(self, query, limit, **kwargs):
                raise RuntimeError("boom")

        resp = run_layered_search("q", limit=5, layers=[BoomLayer()])
        assert "boom" in resp.errors
        assert resp.results == []

    def test_empty_query_no_crash(self):
        resp = run_layered_search("", limit=5)
        assert resp.query == ""

    def test_llm_context_accessible_from_response(self):
        resp = run_layered_search("q", limit=5, nemo_call=_make_nemo_call)
        assert isinstance(resp.llm_context, str)

    def test_custom_layers_override_defaults(self):
        class StaticLayer(SearchLayer):
            @property
            def name(self):
                return "static"
            def search(self, query, limit, **kwargs):
                return [SearchResult(source="static", citation="S1", llm_facing_text="static result")]

        resp = run_layered_search("q", limit=5, layers=[StaticLayer()])
        assert len(resp.results) == 1
        assert resp.results[0].source == "static"


class TestResolveLayers:
    def test_selects_specific_layers_by_name(self):
        layers = resolve_layers(["url", "nemo_memory"])
        assert [layer.name for layer in layers] == ["url", "nemo_memory"]

    def test_ignores_unknown_and_duplicates(self):
        layers = resolve_layers(["url", "unknown", "url"])
        assert [layer.name for layer in layers] == ["url"]

    def test_falls_back_to_defaults_when_no_valid_names(self):
        layers = resolve_layers(["bad", "none"])
        names = [layer.name for layer in layers]
        assert names == ["nemo_memory", "nemo_portfolio", "url"]

    def test_falls_back_to_defaults_for_non_list(self):
        layers = resolve_layers("url")
        names = [layer.name for layer in layers]
        assert names == ["nemo_memory", "nemo_portfolio", "url"]
