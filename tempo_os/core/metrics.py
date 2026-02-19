# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Metrics — In-memory counters for platform observability.

Phase 1: Simple dict-based counters.
Future: Prometheus / OpenTelemetry integration.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any, Dict


class Metrics:
    """Simple in-memory metrics collector."""

    def __init__(self):
        self._counters: Dict[str, int] = defaultdict(int)
        self._gauges: Dict[str, float] = {}
        self._histograms: Dict[str, list] = defaultdict(list)
        self._start_time = time.time()

    # ── Counters ────────────────────────────────────────────────

    def inc(self, name: str, amount: int = 1) -> None:
        """Increment a counter."""
        self._counters[name] += amount

    def get_counter(self, name: str) -> int:
        return self._counters.get(name, 0)

    # ── Gauges ──────────────────────────────────────────────────

    def set_gauge(self, name: str, value: float) -> None:
        self._gauges[name] = value

    def get_gauge(self, name: str) -> float:
        return self._gauges.get(name, 0.0)

    # ── Histograms (for latency) ────────────────────────────────

    def observe(self, name: str, value: float) -> None:
        """Record an observation (e.g. execution time in ms)."""
        self._histograms[name].append(value)
        # Keep only last 1000 observations
        if len(self._histograms[name]) > 1000:
            self._histograms[name] = self._histograms[name][-1000:]

    # ── Export ──────────────────────────────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        """Export all metrics as a dict."""
        result = {
            "uptime_seconds": round(time.time() - self._start_time, 1),
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
        }
        # Add histogram summaries
        for name, values in self._histograms.items():
            if values:
                result[f"histogram_{name}"] = {
                    "count": len(values),
                    "avg": round(sum(values) / len(values), 2),
                    "max": round(max(values), 2),
                    "min": round(min(values), 2),
                }
        return result


# Global singleton
platform_metrics = Metrics()
