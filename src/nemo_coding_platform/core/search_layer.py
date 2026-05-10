"""Layered search: protocol, result types, and runner.

Three search layers, executed in priority order:
  1. NEMO memory search — fast, durable facts and corrections.
  2. NEMO context portfolio — compiled, token-bounded context.
  3. URL reader — live web content (optional, slower).

Each layer returns SearchResult items with:
  - llm_facing_text: compact text suitable for injection into an LLM prompt.
  - ui_facing_data: rich dict for display in the frontend.
  - source: which layer produced the result.
  - citation: a short human-readable reference string.

The runner is synchronous and deterministic. Layers that fail gracefully
return an empty list rather than raising.
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SearchResult:
    """A single result from any search layer."""

    source: str            # layer name: "nemo_memory" | "nemo_portfolio" | "url"
    citation: str          # short human-readable reference (e.g. "NEMO#3 ·memory" or URL)
    llm_facing_text: str   # compact text for LLM prompt injection
    ui_facing_data: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0     # relevance score 0–1 (best effort)


@dataclass
class SearchResponse:
    """Aggregated result from a layered search run."""

    query: str
    results: list[SearchResult] = field(default_factory=list)
    layer_timings_ms: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def llm_context(self) -> str:
        """Concatenated llm_facing_text for all results, ready for prompt injection."""
        parts: list[str] = []
        for r in self.results:
            if r.llm_facing_text.strip():
                parts.append(f"[{r.citation}]\n{r.llm_facing_text.strip()}")
        return "\n\n".join(parts)

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "results": [
                {
                    "source": r.source,
                    "citation": r.citation,
                    "llm_facing_text": r.llm_facing_text,
                    "ui_facing_data": r.ui_facing_data,
                    "score": r.score,
                }
                for r in self.results
            ],
            "layer_timings_ms": self.layer_timings_ms,
            "errors": self.errors,
            "llm_context_chars": len(self.llm_context),
        }


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

class SearchLayer(ABC):
    """Abstract base for a single search layer."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique layer identifier."""

    @abstractmethod
    def search(self, query: str, limit: int, **kwargs: Any) -> list[SearchResult]:
        """Execute the search. Must not raise — return [] on error."""


# ---------------------------------------------------------------------------
# Built-in layers
# ---------------------------------------------------------------------------

class NemoMemoryLayer(SearchLayer):
    """Layer 1: NEMO memory search — fast durable facts and corrections."""

    @property
    def name(self) -> str:
        return "nemo_memory"

    def search(self, query: str, limit: int, **kwargs: Any) -> list[SearchResult]:
        call_fn = kwargs.get("nemo_call")
        if call_fn is None:
            return []
        try:
            payload = call_fn("search_memories", query=query, limit=limit, compact=True)
            memories = payload.get("memories") or []
            results: list[SearchResult] = []
            for i, m in enumerate(memories[:limit]):
                content = str(m.get("content") or "")
                mem_type = str(m.get("memory_type") or "memory")
                importance = float(m.get("importance_level") or 0)
                score = min(1.0, importance / 10.0)
                citation = f"NEMO#{i + 1} ·{mem_type}"
                results.append(SearchResult(
                    source=self.name,
                    citation=citation,
                    llm_facing_text=content[:600],
                    ui_facing_data={"content": content, "memory_type": mem_type, "importance_level": importance},
                    score=score,
                ))
            return results
        except Exception as exc:
            logger.warning("NemoMemoryLayer failed: %s", exc)
            return []


class NemoPortfolioLayer(SearchLayer):
    """Layer 2: NEMO context portfolio — compiled, token-bounded context."""

    @property
    def name(self) -> str:
        return "nemo_portfolio"

    def search(self, query: str, limit: int, **kwargs: Any) -> list[SearchResult]:
        call_fn = kwargs.get("nemo_call")
        token_budget: int = int(kwargs.get("token_budget", 600))
        topic: str = str(kwargs.get("topic", "search"))
        if call_fn is None:
            return []
        try:
            payload = call_fn(
                "build_context_portfolio",
                task=query,
                topic=topic,
                token_budget=token_budget,
                mode="balanced",
            )
            context_text = str(payload.get("context") or "")
            if not context_text.strip():
                return []
            tokens = int(payload.get("estimated_tokens") or 0)
            citation = f"NEMO·portfolio tokens≈{tokens}"
            return [SearchResult(
                source=self.name,
                citation=citation,
                llm_facing_text=context_text[:2000],
                ui_facing_data={"estimated_tokens": tokens, "topic": topic},
                score=0.85,
            )]
        except Exception as exc:
            logger.warning("NemoPortfolioLayer failed: %s", exc)
            return []


class UrlSourceLayer(SearchLayer):
    """Layer 3: URL reader — fetch and strip live web content."""

    @property
    def name(self) -> str:
        return "url"

    def search(self, query: str, limit: int, **kwargs: Any) -> list[SearchResult]:
        urls: list[str] = kwargs.get("urls") or []
        read_fn = kwargs.get("url_read_fn")
        if not urls or read_fn is None:
            return []
        results: list[SearchResult] = []
        for url in urls[:limit]:
            try:
                data, from_cache = read_fn(url)
                title = str(data.get("title") or url)
                content = str(data.get("content") or "")
                citation = f"URL·{title[:40]}"
                results.append(SearchResult(
                    source=self.name,
                    citation=citation,
                    llm_facing_text=content[:800],
                    ui_facing_data={"url": url, "title": title, "from_cache": from_cache},
                    score=0.7,
                ))
            except Exception as exc:
                logger.warning("UrlSourceLayer failed for %s: %s", url, exc)
        return results


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

_DEFAULT_LAYERS: list[SearchLayer] = [
    NemoMemoryLayer(),
    NemoPortfolioLayer(),
    UrlSourceLayer(),
]

_LAYER_REGISTRY: dict[str, SearchLayer] = {layer.name: layer for layer in _DEFAULT_LAYERS}


def resolve_layers(names: object) -> list[SearchLayer]:
    """Resolve user-provided layer names to concrete layer instances.

    Unknown names are ignored. If no valid names are provided, defaults are used.
    """
    if not isinstance(names, list):
        return list(_DEFAULT_LAYERS)
    selected: list[SearchLayer] = []
    seen: set[str] = set()
    for item in names:
        name = str(item).strip().lower()
        if not name or name in seen:
            continue
        layer = _LAYER_REGISTRY.get(name)
        if layer is None:
            continue
        selected.append(layer)
        seen.add(name)
    return selected if selected else list(_DEFAULT_LAYERS)


def run_layered_search(
    query: str,
    *,
    limit: int = 5,
    layers: list[SearchLayer] | None = None,
    nemo_call: Any = None,
    token_budget: int = 600,
    topic: str = "search",
    urls: list[str] | None = None,
    url_read_fn: Any = None,
) -> SearchResponse:
    """Execute all layers in order and aggregate results.

    Args:
        query: The search query string.
        limit: Max results per layer.
        layers: Override the default layer list.
        nemo_call: Callable(tool_name, **kwargs) -> dict for NEMO tools.
        token_budget: Portfolio token budget for layer 2.
        topic: Topic hint for portfolio building.
        urls: List of URLs for layer 3.
        url_read_fn: Callable(url) -> (dict, bool) for URL fetching.

    Returns:
        SearchResponse with aggregated results and timing.
    """
    active_layers = layers if layers is not None else _DEFAULT_LAYERS
    response = SearchResponse(query=query)

    kwargs: dict[str, Any] = {
        "nemo_call": nemo_call,
        "token_budget": token_budget,
        "topic": topic,
        "urls": urls or [],
        "url_read_fn": url_read_fn,
    }

    for layer in active_layers:
        t0 = time.monotonic()
        try:
            items = layer.search(query, limit=limit, **kwargs)
            response.results.extend(items)
        except Exception as exc:
            response.errors[layer.name] = str(exc)
            items = []
        elapsed = int((time.monotonic() - t0) * 1000)
        response.layer_timings_ms[layer.name] = elapsed

    return response
