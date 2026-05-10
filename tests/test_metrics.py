"""Tests for core/metrics.py — MetricsSink, InMemoryMetricsSink."""
import pytest

from nemo_coding_platform.core.metrics import InMemoryMetricsSink, MetricSample, MetricsSink


class TestInMemoryMetricsSink:
    def setup_method(self):
        self.sink = InMemoryMetricsSink()

    def test_is_metrics_sink(self):
        assert isinstance(self.sink, MetricsSink)

    def test_record_and_snapshot(self):
        self.sink.record("my_metric", 1.0, {"k": "v"})
        samples = self.sink.snapshot()
        assert len(samples) == 1
        s = samples[0]
        assert s.name == "my_metric"
        assert s.value == 1.0
        assert s.labels == {"k": "v"}

    def test_empty_labels_default(self):
        self.sink.record("m", 5.0)
        samples = self.sink.snapshot()
        assert samples[0].labels == {}

    def test_multiple_records(self):
        self.sink.record("a", 1)
        self.sink.record("a", 2)
        self.sink.record("b", 10)
        assert len(self.sink.snapshot()) == 3

    def test_reset_clears_all(self):
        self.sink.record("x", 9)
        self.sink.reset()
        assert self.sink.snapshot() == []

    def test_totals_by_name(self):
        self.sink.record("req", 1)
        self.sink.record("req", 3)
        self.sink.record("dur", 100)
        totals = self.sink.totals_by_name()
        assert totals["req"] == 4
        assert totals["dur"] == 100

    def test_count(self):
        self.sink.record("req", 1)
        self.sink.record("req", 1)
        self.sink.record("dur", 50)
        assert self.sink.count("req") == 2
        assert self.sink.count("dur") == 1
        assert self.sink.count("unknown") == 0

    def test_snapshot_is_copy(self):
        self.sink.record("x", 1)
        snap = self.sink.snapshot()
        snap.clear()
        # Original should be untouched
        assert len(self.sink.snapshot()) == 1

    def test_canonical_metric_names_defined(self):
        assert hasattr(InMemoryMetricsSink, "AGENT_MESSAGE_DURATION_MS")
        assert hasattr(InMemoryMetricsSink, "NEMO_TOOL_CALL_TOTAL")
        assert hasattr(InMemoryMetricsSink, "SEARCH_REQUEST_TOTAL")
        assert hasattr(InMemoryMetricsSink, "URL_READ_TOTAL")
        assert hasattr(InMemoryMetricsSink, "SKILL_MATERIALIZATION_DURATION_MS")
