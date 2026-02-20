# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""Unit tests for API dependencies (tenant + user ID parsing)."""

import pytest
from fastapi import HTTPException

from tempo_os.api.deps import get_current_tenant


class TestGetCurrentTenant:
    @pytest.mark.asyncio
    async def test_tenant_from_header(self):
        ctx = await get_current_tenant(
            authorization=None,
            x_tenant_id="my_tenant",
            x_user_id=None,
        )
        assert ctx.tenant_id == "my_tenant"
        assert ctx.user_id is None

    @pytest.mark.asyncio
    async def test_tenant_with_user_id(self):
        ctx = await get_current_tenant(
            authorization=None,
            x_tenant_id="t1",
            x_user_id="user-abc-123",
        )
        assert ctx.tenant_id == "t1"
        assert ctx.user_id == "user-abc-123"

    @pytest.mark.asyncio
    async def test_tenant_from_bearer(self):
        ctx = await get_current_tenant(
            authorization="Bearer fallback_tenant",
            x_tenant_id=None,
            x_user_id=None,
        )
        assert ctx.tenant_id == "fallback_tenant"

    @pytest.mark.asyncio
    async def test_missing_tenant_raises(self):
        with pytest.raises(HTTPException) as exc_info:
            await get_current_tenant(
                authorization=None,
                x_tenant_id=None,
                x_user_id=None,
            )
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_header_takes_priority_over_bearer(self):
        ctx = await get_current_tenant(
            authorization="Bearer bearer_tenant",
            x_tenant_id="header_tenant",
            x_user_id=None,
        )
        assert ctx.tenant_id == "header_tenant"
