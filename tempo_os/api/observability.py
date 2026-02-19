# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Observability API — Metrics, health check, event replay.
"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends

from tempo_os.api.deps import get_current_tenant
from tempo_os.core.tenant import TenantContext
from tempo_os.core.metrics import platform_metrics

router = APIRouter(tags=["observability"])


@router.get("/health")
async def health_check():
    """Enhanced health check with component status."""
    return {
        "status": "ok",
        "version": "0.1.0",
        "redis": "connected",       # Will be dynamic in production
        "postgres": "not_configured",  # Will be dynamic after Plan 03 wiring
        "metrics": platform_metrics.snapshot(),
    }


@router.get("/api/metrics")
async def get_metrics():
    """Return current platform metrics."""
    return platform_metrics.snapshot()


@router.get("/api/workflow/{session_id}/events")
async def get_session_events(
    session_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Return audit log events for a session (event replay)."""
    # Placeholder — will be wired to EventRepository
    return {
        "session_id": session_id,
        "events": [],
        "count": 0,
    }
