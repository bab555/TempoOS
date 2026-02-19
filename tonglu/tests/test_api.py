# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""Tests for Tonglu HTTP API layer.

Uses a separate FastAPI app without lifespan to avoid DB/Redis connections.
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tonglu.api.ingest import router as ingest_router, _task_store
from tonglu.api.query import router as query_router
from tonglu.api.tasks import router as tasks_router
from tonglu.pipeline.ingestion import IngestionResult
from tonglu.storage.models import DataRecord


def _create_test_app() -> FastAPI:
    """Create a minimal FastAPI app for testing (no lifespan)."""
    test_app = FastAPI()
    test_app.include_router(ingest_router)
    test_app.include_router(query_router)
    test_app.include_router(tasks_router)

    @test_app.get("/health")
    async def health():
        return {"status": "ok", "service": "tonglu", "version": "2.0.0"}

    return test_app


class TestHealthEndpoint:
    def test_health(self):
        """GET /health should return ok."""
        app = _create_test_app()
        with TestClient(app) as client:
            resp = client.get("/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert data["service"] == "tonglu"


class TestIngestTextAPI:
    def test_ingest_text_success(self):
        """POST /api/ingest/text should process text and return record_id."""
        app = _create_test_app()

        mock_result = IngestionResult(
            source_id=uuid.uuid4(),
            record_id=uuid.uuid4(),
            status="ready",
        )
        mock_pipeline = AsyncMock()
        mock_pipeline.process = AsyncMock(return_value=mock_result)
        app.state.pipeline = mock_pipeline

        with TestClient(app) as client:
            resp = client.post("/api/ingest/text", json={
                "data": {"party_a": "华为", "amount": 1000000},
                "tenant_id": "test_tenant",
                "schema_type": "contract",
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ready"
        assert "record_id" in data

    def test_ingest_text_error(self):
        """POST /api/ingest/text should return 500 on pipeline error."""
        app = _create_test_app()

        mock_result = IngestionResult(
            source_id=uuid.uuid4(),
            status="error",
            error="LLM call failed",
        )
        mock_pipeline = AsyncMock()
        mock_pipeline.process = AsyncMock(return_value=mock_result)
        app.state.pipeline = mock_pipeline

        with TestClient(app) as client:
            resp = client.post("/api/ingest/text", json={
                "data": "test",
                "tenant_id": "test_tenant",
            })

        assert resp.status_code == 500


class TestIngestBatchAPI:
    def test_batch_success(self):
        """POST /api/ingest/batch should process multiple items."""
        app = _create_test_app()

        mock_results = [
            IngestionResult(source_id=uuid.uuid4(), record_id=uuid.uuid4(), status="ready"),
            IngestionResult(source_id=uuid.uuid4(), record_id=uuid.uuid4(), status="ready"),
        ]
        mock_pipeline = AsyncMock()
        mock_pipeline.process_batch = AsyncMock(return_value=mock_results)
        app.state.pipeline = mock_pipeline

        with TestClient(app) as client:
            resp = client.post("/api/ingest/batch", json={
                "items": [
                    {"source_type": "text", "content_ref": "内容1", "tenant_id": "t1"},
                    {"source_type": "text", "content_ref": "内容2", "tenant_id": "t1"},
                ]
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert data["success"] == 2
        assert data["failed"] == 0


class TestQueryAPI:
    def test_query_success(self):
        """POST /api/query should return results."""
        app = _create_test_app()

        mock_engine = AsyncMock()
        mock_engine.query = AsyncMock(return_value=[
            {"id": "1", "schema_type": "contract", "summary": "test"},
        ])
        app.state.query_engine = mock_engine

        with TestClient(app) as client:
            resp = client.post("/api/query", json={
                "query": "华为合同",
                "tenant_id": "test_tenant",
                "mode": "hybrid",
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["mode"] == "hybrid"

    def test_get_record(self):
        """GET /api/records/{id} should return a record."""
        app = _create_test_app()

        record_id = uuid.uuid4()
        mock_record = DataRecord(
            id=record_id,
            tenant_id="t1",
            schema_type="contract",
            data={"party_a": "华为"},
            summary="test",
            status="ready",
            processing_log=[],
        )
        mock_repo = AsyncMock()
        mock_repo.get_record = AsyncMock(return_value=mock_record)
        app.state.repo = mock_repo

        with TestClient(app) as client:
            resp = client.get(f"/api/records/{record_id}")

        assert resp.status_code == 200
        data = resp.json()
        assert data["schema_type"] == "contract"

    def test_get_record_not_found(self):
        """GET /api/records/{id} should return 404 for missing record."""
        app = _create_test_app()

        mock_repo = AsyncMock()
        mock_repo.get_record = AsyncMock(return_value=None)
        app.state.repo = mock_repo

        with TestClient(app) as client:
            resp = client.get(f"/api/records/{uuid.uuid4()}")

        assert resp.status_code == 404

    def test_get_record_invalid_id(self):
        """GET /api/records/{id} should return 400 for invalid UUID."""
        app = _create_test_app()
        app.state.repo = AsyncMock()

        with TestClient(app) as client:
            resp = client.get("/api/records/not-a-uuid")

        assert resp.status_code == 400

    def test_list_records(self):
        """GET /api/records should return paginated list."""
        app = _create_test_app()

        mock_records = [
            DataRecord(
                id=uuid.uuid4(), tenant_id="t1", schema_type="contract",
                data={}, summary="", status="ready", processing_log=[],
            ),
        ]
        mock_repo = AsyncMock()
        mock_repo.list_records = AsyncMock(return_value=mock_records)
        app.state.repo = mock_repo

        with TestClient(app) as client:
            resp = client.get("/api/records?tenant_id=t1&limit=10")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["records"]) == 1


class TestTasksAPI:
    def test_task_not_found(self):
        """GET /api/tasks/{id} should return 404 for unknown task."""
        app = _create_test_app()

        with TestClient(app) as client:
            resp = client.get(f"/api/tasks/{uuid.uuid4()}")

        assert resp.status_code == 404

    def test_task_found(self):
        """GET /api/tasks/{id} should return task data when exists."""
        app = _create_test_app()

        task_id = str(uuid.uuid4())
        _task_store[task_id] = {
            "task_id": task_id,
            "status": "ready",
            "record_id": "rec-1",
        }

        try:
            with TestClient(app) as client:
                resp = client.get(f"/api/tasks/{task_id}")

            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ready"
        finally:
            _task_store.pop(task_id, None)
