# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Tick Logger — Structured logging for clock ticks and events.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("tempo.tick_logger")


class TickLogger:
    """
    Records tick-level events for debugging and auditing.

    In production, these logs are written to PG workflow_events.
    During development, they go to structured log output.
    """

    def __init__(self) -> None:
        self._entries: list[Dict[str, Any]] = []

    def log_tick(self, tick: int, event_type: str, details: Optional[Dict] = None) -> None:
        """Log a tick-level event."""
        entry = {
            "tick": tick,
            "event_type": event_type,
            "timestamp": time.time(),
            "details": details or {},
        }
        self._entries.append(entry)
        logger.debug("Tick %d: %s %s", tick, event_type, details or "")

    def get_entries(self, last_n: int = 100) -> list[Dict[str, Any]]:
        """Return the last N log entries."""
        return self._entries[-last_n:]

    def clear(self) -> None:
        """Clear all entries."""
        self._entries.clear()
