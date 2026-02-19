# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
API Dependencies — FastAPI dependency injection.
"""

from __future__ import annotations

from typing import Optional

from fastapi import Header, HTTPException

from tempo_os.core.tenant import TenantContext


async def get_current_tenant(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-Id"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
) -> TenantContext:
    """
    Extract tenant and user context from request headers.

    Headers:
      - X-Tenant-Id: tenant isolation key (required)
      - X-User-Id:   user isolation key (optional, frontend-generated UUID)
      - Authorization: fallback tenant identification

    Phase 1: Simple header mapping. Future: JWT validation.
    """
    tenant_id = x_tenant_id
    if not tenant_id and authorization:
        # Simple: treat Bearer token as tenant_id for now
        parts = authorization.split(" ")
        if len(parts) == 2:
            tenant_id = parts[1]

    if not tenant_id:
        raise HTTPException(status_code=401, detail="Missing tenant identification")

    return TenantContext(tenant_id=tenant_id, user_id=x_user_id)
