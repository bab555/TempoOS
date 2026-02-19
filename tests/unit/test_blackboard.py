# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.
"""Unit tests for TenantBlackboard."""

import pytest
from tempo_os.memory.blackboard import TenantBlackboard


class TestTenantBlackboard:
    @pytest.mark.asyncio
    async def test_set_and_get_state(self, mock_redis):
        bb = TenantBlackboard(mock_redis, "test_tenant")
        await bb.set_state("s_001", "count", 42)
        val = await bb.get_state("s_001", "count")
        assert val == 42

    @pytest.mark.asyncio
    async def test_get_all_state(self, mock_redis):
        bb = TenantBlackboard(mock_redis, "test_tenant")
        await bb.set_state("s_001", "name", "Alice")
        await bb.set_state("s_001", "age", 30)
        state = await bb.get_state("s_001")
        assert state["name"] == "Alice"
        assert state["age"] == 30

    @pytest.mark.asyncio
    async def test_get_missing_key_returns_none(self, mock_redis):
        bb = TenantBlackboard(mock_redis, "test_tenant")
        val = await bb.get_state("s_nonexistent", "key")
        assert val is None

    @pytest.mark.asyncio
    async def test_delete_state(self, mock_redis):
        bb = TenantBlackboard(mock_redis, "test_tenant")
        await bb.set_state("s_001", "temp", "value")
        await bb.delete_state("s_001", "temp")
        val = await bb.get_state("s_001", "temp")
        assert val is None

    @pytest.mark.asyncio
    async def test_push_and_get_artifact(self, mock_redis):
        bb = TenantBlackboard(mock_redis, "test_tenant")
        await bb.push_artifact("s_001", "result_001", {"data": [1, 2, 3]})
        artifact = await bb.get_artifact("result_001")
        assert artifact["data"] == [1, 2, 3]
        assert artifact["_session_id"] == "s_001"

    @pytest.mark.asyncio
    async def test_list_session_artifacts(self, mock_redis):
        bb = TenantBlackboard(mock_redis, "test_tenant")
        await bb.push_artifact("s_001", "art_a", {"x": 1})
        await bb.push_artifact("s_001", "art_b", {"y": 2})
        artifacts = await bb.list_session_artifacts("s_001")
        assert set(artifacts) == {"art_a", "art_b"}

    @pytest.mark.asyncio
    async def test_clear_session(self, mock_redis):
        bb = TenantBlackboard(mock_redis, "test_tenant")
        await bb.set_state("s_001", "key", "val")
        await bb.clear_session("s_001")
        state = await bb.get_state("s_001")
        assert state == {}

    @pytest.mark.asyncio
    async def test_signals(self, mock_redis):
        bb = TenantBlackboard(mock_redis, "test_tenant")
        assert await bb.get_signal("s_001", "abort") is False
        await bb.set_signal("s_001", "abort", True)
        assert await bb.get_signal("s_001", "abort") is True

    @pytest.mark.asyncio
    async def test_tenant_isolation(self, mock_redis):
        bb_a = TenantBlackboard(mock_redis, "tenant_a")
        bb_b = TenantBlackboard(mock_redis, "tenant_b")
        await bb_a.set_state("s_001", "key", "from_a")
        val = await bb_b.get_state("s_001", "key")
        assert val is None  # tenant_b cannot see tenant_a's data
