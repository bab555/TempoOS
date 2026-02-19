# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""Tests for QueryEngine."""

import uuid

import pytest

from tonglu.query.engine import QueryEngine
from tonglu.storage.models import DataRecord


class TestQueryEngine:
    @pytest.fixture
    def engine(self, mock_llm, mock_repo):
        return QueryEngine(repo=mock_repo, llm_service=mock_llm)

    @pytest.fixture
    async def seeded_repo(self, mock_repo, tenant_id):
        """Seed the mock repo with test records."""
        records = [
            DataRecord(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                schema_type="contract",
                data={"party_a": "华为", "amount": 1000000},
                summary="华为合同100万",
                status="ready",
            ),
            DataRecord(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                schema_type="invoice",
                data={"vendor": "腾讯", "amount": 500000},
                summary="腾讯发票50万",
                status="ready",
            ),
            DataRecord(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                schema_type="contract",
                data={"party_a": "阿里巴巴", "amount": 2000000},
                summary="阿里合同200万",
                status="ready",
            ),
        ]
        for r in records:
            mock_repo.records[r.id] = r
        return mock_repo

    @pytest.mark.asyncio
    async def test_sql_query_with_filters(self, engine, seeded_repo, tenant_id):
        """SQL query with explicit filters should work."""
        results = await engine.query(
            intent="",
            mode="sql",
            filters={"schema_type": "contract"},
            tenant_id=tenant_id,
        )
        assert len(results) == 2
        assert all(r["schema_type"] == "contract" for r in results)

    @pytest.mark.asyncio
    async def test_sql_query_natural_language(self, engine, seeded_repo, tenant_id):
        """SQL query with natural language should use LLM for filter conversion."""
        results = await engine.query(
            intent="华为的合同",
            mode="sql",
            tenant_id=tenant_id,
        )
        # Mock LLM returns {"schema_type": "contract", "data_conditions": {}}
        assert len(results) == 2  # Both contracts

    @pytest.mark.asyncio
    async def test_vector_query(self, engine, seeded_repo, mock_llm, tenant_id):
        """Vector query should call embed and return results."""
        results = await engine.query(
            intent="华为合同",
            mode="vector",
            tenant_id=tenant_id,
        )
        # Mock repo returns all ready records as vector results
        assert len(results) == 3
        assert len(mock_llm.embed_log) == 1

    @pytest.mark.asyncio
    async def test_hybrid_query(self, engine, seeded_repo, tenant_id):
        """Hybrid query should merge SQL and vector results."""
        results = await engine.query(
            intent="华为合同",
            mode="hybrid",
            tenant_id=tenant_id,
        )
        # Should have results from both SQL and vector, deduplicated
        assert len(results) >= 2
        # Check that _match_type is set
        match_types = {r.get("_match_type") for r in results}
        assert "sql" in match_types or "vector" in match_types

    @pytest.mark.asyncio
    async def test_hybrid_deduplication(self, engine, seeded_repo, tenant_id):
        """Hybrid query should not return duplicate records."""
        results = await engine.query(
            intent="合同",
            mode="hybrid",
            tenant_id=tenant_id,
        )
        ids = [r["id"] for r in results]
        assert len(ids) == len(set(ids)), "Duplicate records found in hybrid results"

    @pytest.mark.asyncio
    async def test_empty_results(self, engine, mock_repo):
        """Query on empty repo should return empty list."""
        results = await engine.query(
            intent="anything",
            mode="sql",
            filters={"schema_type": "nonexistent"},
            tenant_id="empty_tenant",
        )
        assert results == []

    @pytest.mark.asyncio
    async def test_limit_respected(self, engine, seeded_repo, tenant_id):
        """Limit parameter should cap results."""
        results = await engine.query(
            intent="",
            mode="sql",
            filters={"schema_type": "contract"},
            tenant_id=tenant_id,
            limit=1,
        )
        assert len(results) <= 1
