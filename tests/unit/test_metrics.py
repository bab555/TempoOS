# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.
"""Unit tests for Metrics."""

from tempo_os.core.metrics import Metrics


class TestMetrics:
    def test_counter_increment(self):
        m = Metrics()
        m.inc("requests")
        m.inc("requests")
        assert m.get_counter("requests") == 2

    def test_counter_default_zero(self):
        m = Metrics()
        assert m.get_counter("nonexistent") == 0

    def test_gauge(self):
        m = Metrics()
        m.set_gauge("active_sessions", 5.0)
        assert m.get_gauge("active_sessions") == 5.0

    def test_observe(self):
        m = Metrics()
        m.observe("latency_ms", 100)
        m.observe("latency_ms", 200)
        snap = m.snapshot()
        assert "histogram_latency_ms" in snap
        assert snap["histogram_latency_ms"]["avg"] == 150.0

    def test_snapshot_has_uptime(self):
        m = Metrics()
        snap = m.snapshot()
        assert "uptime_seconds" in snap
        assert snap["uptime_seconds"] >= 0
