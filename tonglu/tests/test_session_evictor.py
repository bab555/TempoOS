# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""Tests for SessionEvictor — archive and restore logic."""

import json

import fakeredis.aioredis
import pytest

from tonglu.services.session_evictor import SessionEvictor
from tonglu.storage.models import SessionSnapshot


@pytest.fixture
def fake_redis():
    """Provide a FakeRedis async instance for evictor tests."""
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def evictor(fake_redis, mock_repo):
    """Create a SessionEvictor with FakeRedis and mock repo."""
    ev = SessionEvictor(
        redis_url="redis://fake",
        repo=mock_repo,
        tenant_ids=["test_tenant"],
        scan_interval=60,
        ttl_threshold=300,
    )
    ev._redis = fake_redis
    return ev


class TestEvictorExtractSessionId:
    def test_valid_key(self):
        sid = SessionEvictor._extract_session_id(
            "tempo:t1:session:abc-123", "t1",
        )
        assert sid == "abc-123"

    def test_wrong_tenant(self):
        sid = SessionEvictor._extract_session_id(
            "tempo:t2:session:abc-123", "t1",
        )
        assert sid is None

    def test_non_session_key(self):
        sid = SessionEvictor._extract_session_id(
            "tempo:t1:chat:abc-123", "t1",
        )
        assert sid is None


class TestEvictorArchive:
    """Test archive_session: Redis → PG snapshot."""

    @pytest.mark.asyncio
    async def test_archive_empty_session(self, evictor, mock_repo):
        result = await evictor.archive_session("test_tenant", "s_empty")
        assert result is False
        mock_repo._ensure_snapshots()
        assert "s_empty" not in mock_repo.snapshots

    @pytest.mark.asyncio
    async def test_archive_with_blackboard(self, evictor, fake_redis, mock_repo):
        bb_key = "tempo:test_tenant:session:s_001"
        await fake_redis.hset(bb_key, "user_name", '"Alice"')
        await fake_redis.hset(bb_key, "count", "42")

        result = await evictor.archive_session("test_tenant", "s_001")
        assert result is True

        mock_repo._ensure_snapshots()
        snap = mock_repo.snapshots["s_001"]
        assert snap.tenant_id == "test_tenant"
        assert snap.blackboard["user_name"] == "Alice"
        assert snap.blackboard["count"] == 42

    @pytest.mark.asyncio
    async def test_archive_with_chat_history(self, evictor, fake_redis, mock_repo):
        chat_key = "tempo:test_tenant:chat:s_001"
        msgs = [
            {"role": "user", "content": "hello", "id": "m1"},
            {"role": "assistant", "content": "hi", "id": "m2"},
        ]
        for m in msgs:
            await fake_redis.rpush(chat_key, json.dumps(m, ensure_ascii=False))

        # Need at least blackboard or chat to archive
        result = await evictor.archive_session("test_tenant", "s_001")
        assert result is True

        mock_repo._ensure_snapshots()
        snap = mock_repo.snapshots["s_001"]
        assert len(snap.chat_history) == 2
        assert snap.chat_history[0]["role"] == "user"
        assert snap.chat_history[1]["content"] == "hi"

    @pytest.mark.asyncio
    async def test_archive_preserves_summary_and_route(self, evictor, fake_redis, mock_repo):
        bb_key = "tempo:test_tenant:session:s_001"
        await fake_redis.hset(bb_key, "_chat_summary", "用户查询了产品A")
        await fake_redis.hset(bb_key, "_chat_summary_count", "15")
        await fake_redis.hset(bb_key, "_routed_scene", "procurement")
        await fake_redis.hset(bb_key, "other_key", '"value"')

        await evictor.archive_session("test_tenant", "s_001")

        mock_repo._ensure_snapshots()
        snap = mock_repo.snapshots["s_001"]
        assert snap.routed_scene == "procurement"
        assert "用户查询了产品A" in snap.chat_summary
        # _chat_summary and _routed_scene should be extracted, not in blackboard
        assert "_chat_summary" not in snap.blackboard
        assert "_routed_scene" not in snap.blackboard
        assert "other_key" in snap.blackboard

    @pytest.mark.asyncio
    async def test_archive_with_tool_results(self, evictor, fake_redis, mock_repo):
        results_key = "tempo:test_tenant:session:s_001:results:search"
        await fake_redis.rpush(results_key, json.dumps({"query": "A4"}))
        await fake_redis.rpush(results_key, json.dumps({"query": "printer"}))

        # Also add blackboard so it's not empty
        bb_key = "tempo:test_tenant:session:s_001"
        await fake_redis.hset(bb_key, "x", '"y"')

        await evictor.archive_session("test_tenant", "s_001")

        mock_repo._ensure_snapshots()
        snap = mock_repo.snapshots["s_001"]
        assert "search" in snap.tool_results
        assert len(snap.tool_results["search"]) == 2


class TestEvictorRestore:
    """Test restore_session: PG snapshot → Redis."""

    @pytest.mark.asyncio
    async def test_restore_not_found(self, evictor, mock_repo):
        result = await evictor.restore_session("test_tenant", "s_missing")
        assert result is False

    @pytest.mark.asyncio
    async def test_restore_tenant_mismatch(self, evictor, mock_repo):
        snap = SessionSnapshot(
            session_id="s_001",
            tenant_id="other_tenant",
            chat_history=[],
            blackboard={},
            tool_results={},
        )
        await mock_repo.save_snapshot(snap)
        result = await evictor.restore_session("test_tenant", "s_001")
        assert result is False

    @pytest.mark.asyncio
    async def test_restore_chat_history(self, evictor, fake_redis, mock_repo):
        snap = SessionSnapshot(
            session_id="s_001",
            tenant_id="test_tenant",
            chat_history=[
                {"role": "user", "content": "hello", "id": "m1"},
                {"role": "assistant", "content": "hi", "id": "m2"},
            ],
            blackboard={},
            tool_results={},
        )
        await mock_repo.save_snapshot(snap)

        result = await evictor.restore_session("test_tenant", "s_001")
        assert result is True

        chat_key = "tempo:test_tenant:chat:s_001"
        raw_list = await fake_redis.lrange(chat_key, 0, -1)
        assert len(raw_list) == 2
        assert json.loads(raw_list[0])["role"] == "user"

    @pytest.mark.asyncio
    async def test_restore_blackboard(self, evictor, fake_redis, mock_repo):
        snap = SessionSnapshot(
            session_id="s_001",
            tenant_id="test_tenant",
            chat_history=[],
            blackboard={"user_name": "Alice", "count": 42},
            tool_results={},
        )
        await mock_repo.save_snapshot(snap)

        await evictor.restore_session("test_tenant", "s_001")

        bb_key = "tempo:test_tenant:session:s_001"
        val = await fake_redis.hget(bb_key, "user_name")
        assert json.loads(val) == "Alice"

    @pytest.mark.asyncio
    async def test_restore_summary_and_route(self, evictor, fake_redis, mock_repo):
        snap = SessionSnapshot(
            session_id="s_001",
            tenant_id="test_tenant",
            chat_history=[],
            blackboard={},
            tool_results={},
            chat_summary=json.dumps({"text": "用户查询了产品", "count": 10}),
            routed_scene="procurement",
        )
        await mock_repo.save_snapshot(snap)

        await evictor.restore_session("test_tenant", "s_001")

        bb_key = "tempo:test_tenant:session:s_001"
        summary = await fake_redis.hget(bb_key, "_chat_summary")
        assert summary == "用户查询了产品"
        scene = await fake_redis.hget(bb_key, "_routed_scene")
        assert scene == "procurement"

    @pytest.mark.asyncio
    async def test_restore_tool_results(self, evictor, fake_redis, mock_repo):
        snap = SessionSnapshot(
            session_id="s_001",
            tenant_id="test_tenant",
            chat_history=[],
            blackboard={},
            tool_results={
                "search": [{"query": "A4"}, {"query": "printer"}],
                "data_query": [{"intent": "revenue"}],
            },
        )
        await mock_repo.save_snapshot(snap)

        await evictor.restore_session("test_tenant", "s_001")

        search_key = "tempo:test_tenant:session:s_001:results:search"
        raw = await fake_redis.lrange(search_key, 0, -1)
        assert len(raw) == 2

        dq_key = "tempo:test_tenant:session:s_001:results:data_query"
        raw_dq = await fake_redis.lrange(dq_key, 0, -1)
        assert len(raw_dq) == 1

    @pytest.mark.asyncio
    async def test_restore_marks_restored_at(self, evictor, fake_redis, mock_repo):
        snap = SessionSnapshot(
            session_id="s_001",
            tenant_id="test_tenant",
            chat_history=[{"role": "user", "content": "hi"}],
            blackboard={},
            tool_results={},
        )
        await mock_repo.save_snapshot(snap)
        assert snap.restored_at is None

        await evictor.restore_session("test_tenant", "s_001")

        mock_repo._ensure_snapshots()
        assert mock_repo.snapshots["s_001"].restored_at is not None


class TestEvictorRoundTrip:
    """Test full archive → restore cycle."""

    @pytest.mark.asyncio
    async def test_archive_then_restore(self, evictor, fake_redis, mock_repo):
        # Populate Redis with a full session
        bb_key = "tempo:test_tenant:session:s_rt"
        chat_key = "tempo:test_tenant:chat:s_rt"
        results_key = "tempo:test_tenant:session:s_rt:results:search"

        await fake_redis.hset(bb_key, "user_name", '"Bob"')
        await fake_redis.hset(bb_key, "_routed_scene", "data_analysis")
        await fake_redis.rpush(chat_key, json.dumps({"role": "user", "content": "analyze sales"}))
        await fake_redis.rpush(chat_key, json.dumps({"role": "assistant", "content": "sure"}))
        await fake_redis.rpush(results_key, json.dumps({"query": "sales data"}))

        # Archive
        archived = await evictor.archive_session("test_tenant", "s_rt")
        assert archived is True

        # Clear Redis (simulate TTL expiry)
        await fake_redis.delete(bb_key, chat_key, results_key)
        assert await fake_redis.exists(bb_key) == 0
        assert await fake_redis.exists(chat_key) == 0

        # Restore
        restored = await evictor.restore_session("test_tenant", "s_rt")
        assert restored is True

        # Verify data is back
        val = await fake_redis.hget(bb_key, "user_name")
        assert json.loads(val) == "Bob"

        scene = await fake_redis.hget(bb_key, "_routed_scene")
        assert scene == "data_analysis"

        chat = await fake_redis.lrange(chat_key, 0, -1)
        assert len(chat) == 2

        results = await fake_redis.lrange(results_key, 0, -1)
        assert len(results) == 1


class TestEvictorScanTenant:
    """Test the TTL-based scan logic."""

    @pytest.mark.asyncio
    async def test_scan_skips_high_ttl_sessions(self, evictor, fake_redis, mock_repo):
        bb_key = "tempo:test_tenant:session:s_fresh"
        await fake_redis.hset(bb_key, "key", '"val"')
        await fake_redis.expire(bb_key, 1800)  # 30 min remaining

        count = await evictor._scan_tenant("test_tenant")
        assert count == 0  # TTL is well above threshold

    @pytest.mark.asyncio
    async def test_scan_archives_low_ttl_sessions(self, evictor, fake_redis, mock_repo):
        bb_key = "tempo:test_tenant:session:s_expiring"
        await fake_redis.hset(bb_key, "key", '"val"')
        await fake_redis.expire(bb_key, 60)  # Only 60s left, below 300s threshold

        count = await evictor._scan_tenant("test_tenant")
        assert count == 1

        mock_repo._ensure_snapshots()
        assert "s_expiring" in mock_repo.snapshots
