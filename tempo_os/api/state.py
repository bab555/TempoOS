# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
State API — Blackboard read/write. WIRED to real TenantBlackboard.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from tempo_os.api.deps import get_current_tenant
from tempo_os.core.tenant import TenantContext
from tempo_os.core.context import get_platform_context

router = APIRouter(prefix="/state", tags=["state"])


class StateWriteRequest(BaseModel):
    value: Any


@router.get("/{session_id}")
async def get_all_state(
    session_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Read all Blackboard state for a session."""
    ctx = get_platform_context()
    bb = ctx.get_blackboard(tenant.tenant_id)
    state = await bb.get_state(session_id)
    return {"session_id": session_id, "state": state}


@router.get("/{session_id}/{key}")
async def get_state_key(
    session_id: str,
    key: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Read a specific Blackboard key."""
    ctx = get_platform_context()
    bb = ctx.get_blackboard(tenant.tenant_id)
    value = await bb.get_state(session_id, key)
    return {"session_id": session_id, "key": key, "value": value}


@router.put("/{session_id}/{key}")
async def put_state_key(
    session_id: str,
    key: str,
    req: StateWriteRequest,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Write a Blackboard key (debug/admin only)."""
    ctx = get_platform_context()
    bb = ctx.get_blackboard(tenant.tenant_id)
    await bb.set_state(session_id, key, req.value)
    return {"session_id": session_id, "key": key, "status": "written"}
