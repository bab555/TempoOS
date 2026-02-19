# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Tonglu Test Fixtures — Shared mocks and helpers for all Tonglu tests.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from tonglu.config import TongluSettings
from tonglu.parsers.base import ParseResult
from tonglu.parsers.registry import ParserRegistry
from tonglu.services.llm_service import LLMService
from tonglu.storage.models import DataRecord, DataSource, DataVector
from tonglu.storage.repositories import DataRepository


# ── Mock LLM Service ─────────────────────────────────────────


class MockLLMService:
    """Mock LLMService that returns predictable responses."""

    MODEL_MAP = LLMService.MODEL_MAP

    def __init__(self):
        self.call_log: List[Dict[str, Any]] = []
        self.embed_log: List[List[str]] = []

    async def call(self, task_type: str, messages: list, **kwargs) -> str:
        self.call_log.append({"task_type": task_type, "messages": messages})

        if task_type == "route":
            # Check if it's intent-to-filters or type detection
            content = messages[-1].get("content", "")
            if "判断" in str(messages):
                return "contract"
            return '{"schema_type": "contract", "data_conditions": {}}'

        if task_type == "extract":
            return '{"fields": {"party_a": "华为", "amount": 1000000}, "summary": "华为合同，金额100万"}'

        if task_type == "vision":
            return "图片中包含：合同编号 HW-2026-001，金额 500,000 元"

        if task_type == "validate":
            return "数据校验通过"

        return "mock response"

    async def embed(self, texts: List[str], model: Optional[str] = None) -> List[List[float]]:
        self.embed_log.append(texts)
        # Return a 1024-dim zero vector for each text
        return [[0.1] * 1024 for _ in texts]


# ── Mock Repository ───────────────────────────────────────────


class MockDataRepository:
    """In-memory mock of DataRepository for unit tests."""

    def __init__(self):
        self.sources: Dict[UUID, DataSource] = {}
        self.records: Dict[UUID, DataRecord] = {}
        self.vectors: List[DataVector] = []
        self.lineage: Dict[str, UUID] = {}  # "tenant:session:artifact" → record_id

    async def save_source(self, source: DataSource) -> DataSource:
        if source.id is None:
            source.id = uuid.uuid4()
        self.sources[source.id] = source
        return source

    async def save_record(self, record: DataRecord) -> DataRecord:
        if record.id is None:
            record.id = uuid.uuid4()
        self.records[record.id] = record
        return record

    async def get_record(self, record_id: UUID) -> Optional[DataRecord]:
        return self.records.get(record_id)

    async def list_records(
        self, tenant_id: str, schema_type=None, offset=0, limit=20, data_filters=None,
    ) -> List[DataRecord]:
        results = [
            r for r in self.records.values()
            if r.tenant_id == tenant_id and r.status == "ready"
        ]
        if schema_type:
            results = [r for r in results if r.schema_type == schema_type]
        return results[offset:offset + limit]

    async def update_record_status(self, record_id: UUID, status: str, log_entry=None):
        if record_id in self.records:
            self.records[record_id].status = status

    async def save_vectors(self, vectors: List[DataVector]):
        self.vectors.extend(vectors)

    async def vector_search(self, embedding, tenant_id, limit=10):
        # Return mock results from stored records
        results = []
        for r in self.records.values():
            if r.tenant_id == tenant_id and r.status == "ready":
                results.append({
                    "id": str(r.id),
                    "tenant_id": r.tenant_id,
                    "schema_type": r.schema_type,
                    "data": r.data,
                    "summary": r.summary,
                    "status": r.status,
                    "created_at": None,
                    "chunk_content": r.summary or "",
                    "distance": 0.1,
                })
        return results[:limit]

    async def is_lineage_persisted(self, tenant_id, session_id, artifact_id) -> bool:
        key = f"{tenant_id}:{session_id}:{artifact_id}"
        return key in self.lineage

    async def save_lineage(self, tenant_id, session_id, artifact_id, record_id):
        key = f"{tenant_id}:{session_id}:{artifact_id}"
        self.lineage[key] = record_id


# ── Fixtures ──────────────────────────────────────────────────


@pytest.fixture
def mock_llm() -> MockLLMService:
    """Provide a mock LLM service."""
    return MockLLMService()


@pytest.fixture
def mock_repo() -> MockDataRepository:
    """Provide an in-memory mock repository."""
    return MockDataRepository()


@pytest.fixture
def parser_registry(mock_llm) -> ParserRegistry:
    """Provide a parser registry with mock LLM."""
    return ParserRegistry(mock_llm)


@pytest.fixture
def tenant_id() -> str:
    return "test_tenant"
