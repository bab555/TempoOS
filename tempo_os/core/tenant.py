# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Tenant Context — Multi-tenancy support.

Every operation in TempoOS is scoped to a tenant_id.
TenantContext carries tenant identity through the call chain.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class TenantContext:
    """Immutable tenant identity for request-scoped operations."""

    tenant_id: str
    user_id: Optional[str] = None
    roles: list[str] = None

    def __post_init__(self):
        if not self.tenant_id:
            raise ValueError("tenant_id must not be empty")
        if self.roles is None:
            self.roles = []

    def __repr__(self) -> str:
        return f"TenantContext(tenant={self.tenant_id!r}, user={self.user_id!r})"
