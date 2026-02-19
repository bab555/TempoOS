# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.
"""Unit tests for the FastAPI health endpoint."""

import pytest
from httpx import AsyncClient, ASGITransport

from tempo_os.main import app
from tempo_os.kernel.redis_client import inject_redis_for_test


class TestHealthAPI:
    @pytest.fixture(autouse=True)
    def inject_mock_redis(self, mock_redis):
        """Inject FakeRedis for all API tests."""
        inject_redis_for_test(mock_redis)

    @pytest.mark.asyncio
    async def test_health(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert "version" in data
