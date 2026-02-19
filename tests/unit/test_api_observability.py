# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.
"""Unit tests for Observability API."""

import pytest
from httpx import AsyncClient, ASGITransport
from tempo_os.main import app
from tempo_os.kernel.redis_client import inject_redis_for_test


class TestObservabilityAPI:
    @pytest.fixture(autouse=True)
    def setup_redis(self, mock_redis):
        inject_redis_for_test(mock_redis)

    @pytest.mark.asyncio
    async def test_health(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert "metrics" in data

    @pytest.mark.asyncio
    async def test_metrics(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/metrics")
            assert resp.status_code == 200
            data = resp.json()
            assert "uptime_seconds" in data
            assert "counters" in data

    @pytest.mark.asyncio
    async def test_session_events(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workflow/test-session/events",
                headers={"X-Tenant-Id": "test_tenant"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["session_id"] == "test-session"
            assert "events" in data
