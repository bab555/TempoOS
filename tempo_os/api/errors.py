# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
API Error Handling — Unified error structure.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from fastapi import Request
from fastapi.responses import JSONResponse


class APIError(Exception):
    """Base API error with structured response."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        details: Optional[Dict[str, Any]] = None,
        trace_id: Optional[str] = None,
    ):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        self.trace_id = trace_id or str(uuid.uuid4())
        super().__init__(message)


class SessionNotFoundError(APIError):
    def __init__(self, session_id: str, trace_id: str = None):
        super().__init__(
            code="SESSION_NOT_FOUND",
            message=f"Session '{session_id}' not found",
            status_code=404,
            trace_id=trace_id,
        )


class InvalidTransitionAPIError(APIError):
    def __init__(self, detail: str, trace_id: str = None):
        super().__init__(
            code="INVALID_TRANSITION",
            message=detail,
            status_code=422,
            trace_id=trace_id,
        )


class FlowValidationAPIError(APIError):
    def __init__(self, errors: list, trace_id: str = None):
        super().__init__(
            code="FLOW_VALIDATION_ERROR",
            message="Flow definition has validation errors",
            status_code=422,
            details={"errors": errors},
            trace_id=trace_id,
        )


async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    """Global exception handler for APIError."""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.code,
            "message": exc.message,
            "trace_id": exc.trace_id,
            "details": exc.details,
        },
    )
