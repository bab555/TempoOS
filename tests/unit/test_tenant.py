# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.
"""Unit tests for TenantContext."""

import pytest
from tempo_os.core.tenant import TenantContext


class TestTenantContext:
    def test_create(self):
        ctx = TenantContext(tenant_id="t_001")
        assert ctx.tenant_id == "t_001"
        assert ctx.user_id is None
        assert ctx.roles == []

    def test_with_user(self):
        ctx = TenantContext(tenant_id="t_001", user_id="u_001", roles=["admin"])
        assert ctx.user_id == "u_001"
        assert "admin" in ctx.roles

    def test_empty_tenant_id_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            TenantContext(tenant_id="")

    def test_repr(self):
        ctx = TenantContext(tenant_id="t_001", user_id="u_001")
        assert "t_001" in repr(ctx)
