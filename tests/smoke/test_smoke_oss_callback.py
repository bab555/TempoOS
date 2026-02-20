# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Smoke test -- Tonglu OSS Callback endpoint (/api/oss/callback).

Verifies that:
  - get_settings() import works (was previously missing)
  - Missing session_id returns early with "skipped"
  - With session_id, pipeline.process is called and FILE_READY is published
  - URL reconstruction and filename inference work correctly

Uses mocked pipeline/repo to avoid real file downloads, but exercises
the full endpoint code path including get_settings() and Redis publish.

Run:  pytest tests/smoke/test_smoke_oss_callback.py -v -s --timeout=60
"""

import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tonglu.api.oss_callback import router as oss_callback_router


@dataclass
class FakeProcessResult:
    status: str = "ready"
    record_id: Optional[uuid.UUID] = None
    source_id: Optional[uuid.UUID] = None
    error: Optional[str] = None


@dataclass
class FakeRecord:
    id: uuid.UUID = None
    summary: str = "Test document summary"
    data: dict = None

    def __post_init__(self):
        if self.id is None:
            self.id = uuid.uuid4()
        if self.data is None:
            self.data = {"title": "Test", "content": "Hello world"}


def _make_app(pipeline_result=None, record=None) -> FastAPI:
    """Build a minimal FastAPI app with mocked pipeline and repo."""
    app = FastAPI()
    app.include_router(oss_callback_router)

    rid = uuid.uuid4()
    if pipeline_result is None:
        pipeline_result = FakeProcessResult(status="ready", record_id=rid)
    if record is None:
        record = FakeRecord(id=rid)

    mock_pipeline = AsyncMock()
    mock_pipeline.process = AsyncMock(return_value=pipeline_result)

    mock_repo = AsyncMock()
    mock_repo.get_record = AsyncMock(return_value=record)

    app.state.pipeline = mock_pipeline
    app.state.repo = mock_repo

    return app


class TestOssCallbackImport:
    """Verify the get_settings() import that was previously broken."""

    def test_get_settings_importable(self):
        from tonglu.config import get_settings
        s = get_settings()
        assert s is not None
        assert hasattr(s, "DATABASE_URL")
        assert hasattr(s, "REDIS_URL")

    def test_get_settings_is_singleton(self):
        from tonglu.config import get_settings
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2


class TestOssCallbackEndpoint:
    @pytest.mark.asyncio
    async def test_missing_session_id_skips(self):
        """Without x:session_id, endpoint should return early."""
        app = _make_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t", timeout=10) as c:
            resp = await c.post(
                "/api/oss/callback",
                data={
                    "bucket": "test-bucket",
                    "object": "tempoos/test/file.pdf",
                    "size": "1024",
                    "mimeType": "application/pdf",
                    "etag": "abc123",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "skipped" in data.get("message", "")
        print(f"\n  Missing session_id -> skipped: OK")

    @pytest.mark.asyncio
    async def test_callback_with_session_processes_file(self):
        """With session_id, pipeline.process should be called."""
        rid = uuid.uuid4()
        result = FakeProcessResult(status="ready", record_id=rid)
        record = FakeRecord(id=rid)
        app = _make_app(pipeline_result=result, record=record)

        transport = ASGITransport(app=app)

        mock_conn = AsyncMock()
        mock_conn.publish = AsyncMock(return_value=1)
        mock_conn.aclose = AsyncMock()

        with patch("redis.asyncio.from_url", return_value=mock_conn):
            async with AsyncClient(transport=transport, base_url="http://t", timeout=10) as c:
                resp = await c.post(
                    "/api/oss/callback",
                    data={
                        "bucket": "hdtsyg",
                        "object": "tempoos/tenant/t1/user/u1/2026/02/15/abc_test.xlsx",
                        "size": "2048",
                        "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        "etag": "def456",
                        "x:tenant_id": "smoke_cb",
                        "x:session_id": "sess_001",
                        "x:user_id": "user_001",
                        "x:file_id": "file_001",
                    },
                )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["file_id"] == "file_001"
        print(f"\n  Callback with session -> processed: OK")
        print(f"  file_id: {data['file_id']}")

        pipeline = app.state.pipeline
        pipeline.process.assert_called_once()
        call_kwargs = pipeline.process.call_args
        assert call_kwargs.kwargs.get("tenant_id") == "smoke_cb" or "smoke_cb" in str(call_kwargs)

    @pytest.mark.asyncio
    async def test_callback_pipeline_error_handled(self):
        """Pipeline error should not crash the endpoint."""
        result = FakeProcessResult(status="error", error="parse failed")
        app = _make_app(pipeline_result=result)

        transport = ASGITransport(app=app)

        mock_conn = AsyncMock()
        mock_conn.publish = AsyncMock(return_value=1)
        mock_conn.aclose = AsyncMock()

        with patch("redis.asyncio.from_url", return_value=mock_conn):
            async with AsyncClient(transport=transport, base_url="http://t", timeout=10) as c:
                resp = await c.post(
                    "/api/oss/callback",
                    data={
                        "bucket": "hdtsyg",
                        "object": "tempoos/bad_file.bin",
                        "size": "100",
                        "mimeType": "application/octet-stream",
                        "etag": "xxx",
                        "x:tenant_id": "smoke_cb",
                        "x:session_id": "sess_err",
                    },
                )

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        print(f"\n  Pipeline error handled gracefully: OK")

    @pytest.mark.asyncio
    async def test_url_reconstruction(self):
        """Verify the file URL is correctly built from bucket + object key."""
        app = _make_app()
        transport = ASGITransport(app=app)

        published_messages = []

        mock_conn = AsyncMock()

        async def capture_publish(channel, message):
            published_messages.append((channel, message))
            return 1

        mock_conn.publish = AsyncMock(side_effect=capture_publish)
        mock_conn.aclose = AsyncMock()

        with patch("redis.asyncio.from_url", return_value=mock_conn):
            async with AsyncClient(transport=transport, base_url="http://t", timeout=10) as c:
                resp = await c.post(
                    "/api/oss/callback",
                    data={
                        "bucket": "mybucket",
                        "object": "path/to/document.pdf",
                        "size": "500",
                        "mimeType": "application/pdf",
                        "etag": "e1",
                        "x:tenant_id": "t_url",
                        "x:session_id": "sess_url",
                    },
                )

        assert resp.status_code == 200

        assert len(published_messages) == 1
        channel, raw = published_messages[0]
        assert channel == "tempo:t_url:events"

        event = json.loads(raw)
        assert event["type"] == "FILE_READY"
        assert event["session_id"] == "sess_url"
        assert event["tenant_id"] == "t_url"
        assert "document.pdf" in event["payload"]["file_name"]
        assert "mybucket" in event["payload"]["file_url"]
        print(f"\n  URL reconstruction: {event['payload']['file_url']}")
        print(f"  FILE_READY channel: {channel}")
        print(f"  FILE_READY event published: OK")
