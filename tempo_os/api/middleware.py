# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
API Middleware — Trace ID propagation and idempotency.
"""

from __future__ import annotations

import uuid
import time
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("tempo.api")


class TraceMiddleware(BaseHTTPMiddleware):
    """
    Generates or propagates X-Trace-Id header for every request.
    Also logs request duration.
    """

    async def dispatch(self, request: Request, call_next):
        trace_id = request.headers.get("X-Trace-Id", str(uuid.uuid4()))
        request.state.trace_id = trace_id

        start = time.time()
        response: Response = await call_next(request)
        elapsed = (time.time() - start) * 1000

        response.headers["X-Trace-Id"] = trace_id
        logger.info(
            "[api] %s %s → %d (%.0fms) trace=%s",
            request.method, request.url.path,
            response.status_code, elapsed, trace_id,
        )
        return response
