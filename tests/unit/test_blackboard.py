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


class TestBlackboardTTL:
    """Verify that set_state refreshes TTL on session keys."""

    @pytest.mark.asyncio
    async def test_set_state_applies_ttl(self, mock_redis):
        bb = TenantBlackboard(mock_redis, "test_tenant", session_ttl=600)
        await bb.set_state("s_ttl", "key", "value")
        ttl = await mock_redis.ttl("tempo:test_tenant:session:s_ttl")
        assert 0 < ttl <= 600

    @pytest.mark.asyncio
    async def test_set_state_refreshes_ttl(self, mock_redis):
        bb = TenantBlackboard(mock_redis, "test_tenant", session_ttl=600)
        await bb.set_state("s_ttl", "a", 1)
        # Manually reduce TTL
        await mock_redis.expire("tempo:test_tenant:session:s_ttl", 10)
        # Another write should refresh TTL
        await bb.set_state("s_ttl", "b", 2)
        ttl = await mock_redis.ttl("tempo:test_tenant:session:s_ttl")
        assert ttl > 10


class TestBlackboardAppendResult:
    """Test accumulated tool results (append_result / get_results)."""

    @pytest.mark.asyncio
    async def test_append_and_get(self, mock_redis):
        bb = TenantBlackboard(mock_redis, "test_tenant")
        await bb.append_result("s_001", "search", {"query": "A4 paper", "count": 5})
        await bb.append_result("s_001", "search", {"query": "printer", "count": 3})
        results = await bb.get_results("s_001", "search")
        assert len(results) == 2
        assert results[0]["query"] == "A4 paper"
        assert results[1]["query"] == "printer"

    @pytest.mark.asyncio
    async def test_append_returns_length(self, mock_redis):
        bb = TenantBlackboard(mock_redis, "test_tenant")
        n1 = await bb.append_result("s_001", "data_query", {"data": 1})
        n2 = await bb.append_result("s_001", "data_query", {"data": 2})
        assert n1 == 1
        assert n2 == 2

    @pytest.mark.asyncio
    async def test_get_results_limit(self, mock_redis):
        bb = TenantBlackboard(mock_redis, "test_tenant")
        for i in range(10):
            await bb.append_result("s_001", "search", {"i": i})
        results = await bb.get_results("s_001", "search", limit=3)
        assert len(results) == 3
        assert results[0]["i"] == 7  # last 3 of 0..9

    @pytest.mark.asyncio
    async def test_tool_isolation(self, mock_redis):
        bb = TenantBlackboard(mock_redis, "test_tenant")
        await bb.append_result("s_001", "search", {"tool": "search"})
        await bb.append_result("s_001", "data_query", {"tool": "dq"})
        search_results = await bb.get_results("s_001", "search")
        dq_results = await bb.get_results("s_001", "data_query")
        assert len(search_results) == 1
        assert len(dq_results) == 1
        assert search_results[0]["tool"] == "search"
        assert dq_results[0]["tool"] == "dq"

    @pytest.mark.asyncio
    async def test_get_empty_results(self, mock_redis):
        bb = TenantBlackboard(mock_redis, "test_tenant")
        results = await bb.get_results("s_nonexistent", "search")
        assert results == []

    @pytest.mark.asyncio
    async def test_clear_session_removes_results(self, mock_redis):
        bb = TenantBlackboard(mock_redis, "test_tenant")
        await bb.append_result("s_001", "search", {"data": 1})
        await bb.append_result("s_001", "data_query", {"data": 2})
        await bb.clear_session("s_001")
        assert await bb.get_results("s_001", "search") == []
        assert await bb.get_results("s_001", "data_query") == []
