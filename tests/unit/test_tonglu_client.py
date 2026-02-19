# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""Unit tests for TongluClient — HTTP client for Tonglu API."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from tempo_os.runtime.tonglu_client import TongluClient


class TestTongluClientQuery:
    @pytest.mark.asyncio
    async def test_query_success(self):
        """query() should POST to /api/query and return results."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [{"id": "1", "schema_type": "contract"}],
            "count": 1,
        }
        mock_response.raise_for_status = MagicMock()

        client = TongluClient("http://fake:8100")
        client._client.post = AsyncMock(return_value=mock_response)

        results = await client.query("华为合同", tenant_id="t1")
        assert len(results) == 1
        assert results[0]["schema_type"] == "contract"

        # Verify correct URL and payload
        client._client.post.assert_called_once()
        call_args = client._client.post.call_args
        assert call_args[0][0] == "/api/query"

    @pytest.mark.asyncio
    async def test_query_with_filters(self):
        """query() should pass filters correctly."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"results": [], "count": 0}
        mock_response.raise_for_status = MagicMock()

        client = TongluClient()
        client._client.post = AsyncMock(return_value=mock_response)

        await client.query(
            "test",
            filters={"schema_type": "invoice"},
            tenant_id="t1",
            mode="sql",
        )

        payload = client._client.post.call_args[1]["json"]
        assert payload["mode"] == "sql"
        assert payload["filters"] == {"schema_type": "invoice"}


class TestTongluClientIngest:
    @pytest.mark.asyncio
    async def test_ingest_success(self):
        """ingest() should POST to /api/ingest/text and return record_id."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"record_id": "abc-123", "status": "ready"}
        mock_response.raise_for_status = MagicMock()

        client = TongluClient()
        client._client.post = AsyncMock(return_value=mock_response)

        record_id = await client.ingest(
            data={"party_a": "华为"},
            tenant_id="t1",
            schema_type="contract",
        )
        assert record_id == "abc-123"


class TestTongluClientUpload:
    @pytest.mark.asyncio
    async def test_upload_returns_task_id(self, tmp_path):
        """upload() should POST file and return task_id."""
        # Create a test file
        test_file = tmp_path / "test.pdf"
        test_file.write_bytes(b"fake pdf content")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"task_id": "task-456", "status": "processing"}
        mock_response.raise_for_status = MagicMock()

        client = TongluClient()
        client._client.post = AsyncMock(return_value=mock_response)

        task_id = await client.upload(
            file_path=str(test_file),
            file_name="test.pdf",
            tenant_id="t1",
        )
        assert task_id == "task-456"


class TestTongluClientRecord:
    @pytest.mark.asyncio
    async def test_get_record(self):
        """get_record() should GET /api/records/{id}."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "rec-1", "schema_type": "contract", "data": {},
        }
        mock_response.raise_for_status = MagicMock()

        client = TongluClient()
        client._client.get = AsyncMock(return_value=mock_response)

        record = await client.get_record("rec-1")
        assert record["id"] == "rec-1"

    @pytest.mark.asyncio
    async def test_get_task(self):
        """get_task() should GET /api/tasks/{id}."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "task_id": "task-1", "status": "ready", "record_id": "rec-1",
        }
        mock_response.raise_for_status = MagicMock()

        client = TongluClient()
        client._client.get = AsyncMock(return_value=mock_response)

        task = await client.get_task("task-1")
        assert task["status"] == "ready"


class TestTongluClientHealth:
    @pytest.mark.asyncio
    async def test_health_check_success(self):
        """health_check() should return True when service is up."""
        mock_response = MagicMock()
        mock_response.status_code = 200

        client = TongluClient()
        client._client.get = AsyncMock(return_value=mock_response)

        assert await client.health_check() is True

    @pytest.mark.asyncio
    async def test_health_check_failure(self):
        """health_check() should return False when service is down."""
        client = TongluClient()
        client._client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

        assert await client.health_check() is False
