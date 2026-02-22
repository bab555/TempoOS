# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Session API — Restore archived sessions from PG back to Redis.

Called by TempoOS Agent Controller when a request arrives for a session
that no longer exists in Redis (TTL expired, data archived by Evictor).
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger("tonglu.api.session")

router = APIRouter(prefix="/session", tags=["session"])


class RestoreRequest(BaseModel):
    tenant_id: str = Field(..., description="Tenant ID")
    session_id: str = Field(..., description="Session ID to restore")
    session_ttl: int = Field(default=1800, description="Redis session TTL after restore")
    chat_ttl: int = Field(default=86400, description="Redis chat history TTL after restore")


class RestoreResponse(BaseModel):
    restored: bool
    session_id: str
    message: str


@router.post("/restore", response_model=RestoreResponse)
async def restore_session(req: RestoreRequest, request: Request):
    """
    Restore an archived session from PG snapshot back to Redis.

    Called by TempoOS when ChatStore/Blackboard is empty for a known session_id.
    """
    evictor = getattr(request.app.state, "session_evictor", None)
    if evictor is None:
        raise HTTPException(
            status_code=503,
            detail="Session Evictor is not enabled",
        )

    restored = await evictor.restore_session(
        tenant_id=req.tenant_id,
        session_id=req.session_id,
        session_ttl=req.session_ttl,
        chat_ttl=req.chat_ttl,
    )

    if restored:
        return RestoreResponse(
            restored=True,
            session_id=req.session_id,
            message="Session restored from PG snapshot",
        )
    else:
        return RestoreResponse(
            restored=False,
            session_id=req.session_id,
            message="No snapshot found for this session",
        )
