# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Smoke test -- FILE_UPLOADED -> Tonglu EventSink -> FILE_READY chain.

This test mocks the Tonglu IngestionPipeline but tests the real EventSink
event handling and Redis pub/sub flow. This validates:
  1. Agent Controller publishes FILE_UPLOADED
  2. EventSink receives it and calls IngestionPipeline
  3. EventSink publishes FILE_READY back
  4. Agent Controller receives the parsed text

Does NOT require real OSS or Tonglu DB (those are mocked).

Run:  pytest tests/smoke/test_smoke_file_chain.py -v -s --timeout=60
"""

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
import fakeredis.aioredis

from tonglu.services.event_sink import EventSinkListener, FILE_UPLOADED, FILE_READY


class _FakeIngestionResult:
    def __init__(self, record_id=None, status="ready", error=None):
        self.record_id = record_id
        self.status = status
        self.error = error


class _FakeRecord:
    def __init__(self, summary="", data=None):
        self.summary = summary
        self.data = data or {}


class TestFileEventChain:
    @pytest.mark.asyncio
    async def test_file_uploaded_triggers_file_ready(self):
        """Publish FILE_UPLOADED, verify EventSink publishes FILE_READY."""
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        tenant_id = "test_tenant"
        session_id = str(uuid.uuid4())
        file_url = "https://hdtsyg.oss-cn-hangzhou.aliyuncs.com/tempoos/test/report.xlsx"

        mock_pipeline = MagicMock()
        mock_pipeline.process = AsyncMock(return_value=_FakeIngestionResult(
            record_id="rec_001", status="ready"
        ))

        mock_repo = MagicMock()
        mock_repo.get_record = AsyncMock(return_value=_FakeRecord(
            summary="Excel spreadsheet with 3 columns and 10 rows",
            data={"columns": ["A", "B", "C"], "row_count": 10},
        ))

        sink = EventSinkListener(
            redis_url="redis://fake",
            pipeline=mock_pipeline,
            repo=mock_repo,
            persist_rules=["*"],
            tenant_ids=[tenant_id],
        )
        # Inject our fake redis directly
        sink._redis = redis
        sink._running = True

        # Subscribe to capture FILE_READY
        pubsub = redis.pubsub()
        channel = f"tempo:{tenant_id}:events"
        await pubsub.subscribe(channel)

        # Build FILE_UPLOADED event
        file_event = {
            "id": str(uuid.uuid4()),
            "type": FILE_UPLOADED,
            "source": "agent_controller",
            "target": "*",
            "tick": 0,
            "payload": {
                "file_id": "f_001",
                "file_url": file_url,
                "file_name": "report.xlsx",
                "file_type": "application/xlsx",
                "user_id": "u1",
            },
            "tenant_id": tenant_id,
            "session_id": session_id,
            "priority": 7,
        }

        # Directly call _handle_event (simulates receiving from bus)
        await sink._handle_event(file_event)

        # Verify IngestionPipeline was called
        mock_pipeline.process.assert_called_once()
        call_kwargs = mock_pipeline.process.call_args
        assert call_kwargs.kwargs["source_type"] == "url"
        assert call_kwargs.kwargs["content_ref"] == file_url
        assert call_kwargs.kwargs["file_name"] == "report.xlsx"

        # Verify FILE_READY was published
        # Read messages from pubsub
        ready_msg = None
        for _ in range(10):
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if msg and msg["type"] == "message":
                ready_msg = json.loads(msg["data"])
                break
            await asyncio.sleep(0.1)

        assert ready_msg is not None, "FILE_READY not published"
        assert ready_msg["type"] == FILE_READY
        assert ready_msg["session_id"] == session_id
        assert ready_msg["payload"]["file_url"] == file_url
        assert ready_msg["payload"]["file_name"] == "report.xlsx"
        assert "Excel spreadsheet" in ready_msg["payload"]["text_content"]
        assert ready_msg["payload"]["record_id"] == "rec_001"

        print(f"\n--- FILE_READY payload ---")
        print(json.dumps(ready_msg["payload"], indent=2, ensure_ascii=False))

        await pubsub.unsubscribe()
        await pubsub.aclose()
        await redis.aclose()

    @pytest.mark.asyncio
    async def test_file_upload_failure_still_publishes_ready(self):
        """Even if file processing fails, FILE_READY is published with error."""
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        tenant_id = "test_tenant"
        session_id = str(uuid.uuid4())

        mock_pipeline = MagicMock()
        mock_pipeline.process = AsyncMock(return_value=_FakeIngestionResult(
            record_id=None, status="error", error="Unsupported file format"
        ))

        mock_repo = MagicMock()

        sink = EventSinkListener(
            redis_url="redis://fake",
            pipeline=mock_pipeline,
            repo=mock_repo,
            persist_rules=[],
            tenant_ids=[tenant_id],
        )
        sink._redis = redis
        sink._running = True

        pubsub = redis.pubsub()
        channel = f"tempo:{tenant_id}:events"
        await pubsub.subscribe(channel)

        file_event = {
            "id": str(uuid.uuid4()),
            "type": FILE_UPLOADED,
            "source": "agent_controller",
            "tenant_id": tenant_id,
            "session_id": session_id,
            "payload": {
                "file_id": "f_002",
                "file_url": "https://oss/bad_file.xyz",
                "file_name": "bad_file.xyz",
            },
        }

        await sink._handle_event(file_event)

        ready_msg = None
        for _ in range(10):
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if msg and msg["type"] == "message":
                ready_msg = json.loads(msg["data"])
                break
            await asyncio.sleep(0.1)

        assert ready_msg is not None, "FILE_READY not published on error"
        assert ready_msg["type"] == FILE_READY
        assert "error" in ready_msg["payload"] or "failed" in ready_msg["payload"].get("text_content", "").lower()

        print(f"\n--- Error FILE_READY ---")
        print(json.dumps(ready_msg["payload"], indent=2, ensure_ascii=False))

        await pubsub.unsubscribe()
        await pubsub.aclose()
        await redis.aclose()

    @pytest.mark.asyncio
    async def test_missing_url_is_skipped(self):
        """FILE_UPLOADED without file_url should be silently skipped."""
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

        mock_pipeline = MagicMock()
        mock_repo = MagicMock()

        sink = EventSinkListener(
            redis_url="redis://fake",
            pipeline=mock_pipeline,
            repo=mock_repo,
            persist_rules=[],
            tenant_ids=["t"],
        )
        sink._redis = redis
        sink._running = True

        await sink._handle_event({
            "id": str(uuid.uuid4()),
            "type": FILE_UPLOADED,
            "tenant_id": "t",
            "session_id": "s",
            "payload": {"file_id": "f", "file_url": "", "file_name": "x.pdf"},
        })

        # Pipeline should NOT have been called
        mock_pipeline.process.assert_not_called()

        await redis.aclose()
