# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Structured Logging — JSON format with trace context.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Optional


class StructuredFormatter(logging.Formatter):
    """JSON log formatter with trace/tenant/session context."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "module": record.name,
            "message": record.getMessage(),
        }
        # Attach context if available
        for key in ("trace_id", "tenant_id", "session_id"):
            val = getattr(record, key, None)
            if val:
                log_entry[key] = val

        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    """Configure structured JSON logging for the platform."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter())

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()
    root.addHandler(handler)
