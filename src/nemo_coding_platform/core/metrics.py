"""Metrics sink: abstract interface and in-memory implementation.

Usage:
    sink = InMemoryMetricsSink()
    sink.record("agent_message_duration_ms", 142, {"mode": "chat"})
    snap = sink.snapshot()
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Sequence


@dataclass
class MetricSample:
    name: str
    value: float
    labels: dict[str, str]


class MetricsSink(ABC):
    """Abstract interface for recording operational metrics."""

    @abstractmethod
    def record(
        self,
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
    ) -> None:
        """Record a single metric observation."""

    @abstractmethod
    def snapshot(self) -> list[MetricSample]:
        """Return all recorded samples (unsorted)."""

    @abstractmethod
    def reset(self) -> None:
        """Clear all recorded samples."""


class InMemoryMetricsSink(MetricsSink):
    """O(1) in-process metrics sink. Prometheus-swappable later."""

    # Canonical metric names used by the platform
    AGENT_MESSAGE_DURATION_MS = "agent_message_duration_ms"
    NEMO_TOOL_CALL_TOTAL = "nemo_tool_call_total"
    SEARCH_REQUEST_TOTAL = "search_request_total"
    URL_READ_TOTAL = "url_read_total"
    SKILL_MATERIALIZATION_DURATION_MS = "skill_materialization_duration_ms"

    def __init__(self) -> None:
        self._samples: list[MetricSample] = []

    def record(
        self,
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
    ) -> None:
        self._samples.append(MetricSample(name=name, value=value, labels=labels or {}))

    def snapshot(self) -> list[MetricSample]:
        return list(self._samples)

    def reset(self) -> None:
        self._samples.clear()

    def totals_by_name(self) -> dict[str, float]:
        """Return summed values keyed by metric name (convenience for tests/stats)."""
        totals: dict[str, float] = defaultdict(float)
        for sample in self._samples:
            totals[sample.name] += sample.value
        return dict(totals)

    def count(self, name: str) -> int:
        """Return number of recorded samples for *name*."""
        return sum(1 for s in self._samples if s.name == name)
