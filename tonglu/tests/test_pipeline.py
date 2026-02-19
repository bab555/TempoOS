# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""Tests for IngestionPipeline."""

import asyncio

import pytest

from tonglu.parsers.registry import ParserRegistry
from tonglu.pipeline.ingestion import IngestionPipeline, IngestionResult


class TestIngestionPipeline:
    @pytest.fixture
    def pipeline(self, mock_llm, mock_repo):
        registry = ParserRegistry(mock_llm)
        return IngestionPipeline(
            parser_registry=registry,
            llm_service=mock_llm,
            repo=mock_repo,
            max_concurrent=5,  # Lower for tests
        )

    @pytest.mark.asyncio
    async def test_process_text_success(self, pipeline, mock_repo, tenant_id):
        """Text ingestion should create source + record + vector."""
        result = await pipeline.process(
            source_type="text",
            content_ref="华为技术有限公司合同，金额100万元",
            tenant_id=tenant_id,
        )

        assert result.status == "ready"
        assert result.source_id is not None
        assert result.record_id is not None

        # Verify source was saved
        assert len(mock_repo.sources) == 1

        # Verify record was saved with correct data
        assert len(mock_repo.records) == 1
        record = list(mock_repo.records.values())[0]
        assert record.tenant_id == tenant_id
        assert record.status == "ready"
        assert "party_a" in record.data  # From mock LLM extract

        # Verify vector was saved
        assert len(mock_repo.vectors) == 1

    @pytest.mark.asyncio
    async def test_process_with_schema_type(self, pipeline, mock_llm, mock_repo, tenant_id):
        """When schema_type is provided, should skip type detection."""
        result = await pipeline.process(
            source_type="text",
            content_ref="some contract text",
            tenant_id=tenant_id,
            schema_type="contract",
        )

        assert result.status == "ready"
        # route task_type should NOT be called (type detection skipped)
        route_calls = [c for c in mock_llm.call_log if c["task_type"] == "route"]
        assert len(route_calls) == 0

    @pytest.mark.asyncio
    async def test_process_empty_text_fails(self, pipeline, tenant_id):
        """Empty text should result in error."""
        result = await pipeline.process(
            source_type="text",
            content_ref="   ",
            tenant_id=tenant_id,
        )
        assert result.status == "error"
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_process_batch(self, pipeline, mock_repo, tenant_id):
        """Batch processing should handle multiple items."""
        items = [
            {"source_type": "text", "content_ref": f"合同内容 {i}", "tenant_id": tenant_id}
            for i in range(3)
        ]
        results = await pipeline.process_batch(items)

        assert len(results) == 3
        assert all(r.status == "ready" for r in results)
        assert len(mock_repo.sources) == 3
        assert len(mock_repo.records) == 3

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self, mock_llm, mock_repo, tenant_id):
        """Semaphore should limit concurrent processing."""
        max_concurrent = 3
        registry = ParserRegistry(mock_llm)
        pipeline = IngestionPipeline(
            parser_registry=registry,
            llm_service=mock_llm,
            repo=mock_repo,
            max_concurrent=max_concurrent,
        )

        active_count = 0
        max_active = 0
        original_call = mock_llm.call

        async def tracking_call(task_type, messages, **kwargs):
            nonlocal active_count, max_active
            active_count += 1
            max_active = max(max_active, active_count)
            await asyncio.sleep(0.01)  # Simulate work
            active_count -= 1
            return await original_call(task_type, messages, **kwargs)

        mock_llm.call = tracking_call

        items = [
            {"source_type": "text", "content_ref": f"内容 {i}", "tenant_id": tenant_id}
            for i in range(10)
        ]
        await pipeline.process_batch(items)

        # max_active should not exceed max_concurrent
        assert max_active <= max_concurrent

    @pytest.mark.asyncio
    async def test_single_failure_doesnt_block_batch(self, pipeline, mock_repo, tenant_id):
        """One failed item should not affect others in a batch."""
        items = [
            {"source_type": "text", "content_ref": "正常内容", "tenant_id": tenant_id},
            {"source_type": "text", "content_ref": "   ", "tenant_id": tenant_id},  # Empty → error
            {"source_type": "text", "content_ref": "另一条正常内容", "tenant_id": tenant_id},
        ]
        results = await pipeline.process_batch(items)

        assert len(results) == 3
        statuses = [r.status for r in results]
        assert statuses.count("ready") == 2
        assert statuses.count("error") == 1

    @pytest.mark.asyncio
    async def test_process_with_metadata(self, pipeline, mock_repo, tenant_id):
        """Metadata should be stored in the source."""
        result = await pipeline.process(
            source_type="text",
            content_ref="test content",
            tenant_id=tenant_id,
            metadata={"source": "event_sink", "session_id": "s1"},
        )

        assert result.status == "ready"
        source = list(mock_repo.sources.values())[0]
        assert source.metadata_ == {"source": "event_sink", "session_id": "s1"}
