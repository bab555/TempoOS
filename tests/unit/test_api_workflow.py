# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.
"""Unit tests for Workflow API (wired to real engine)."""

import pytest
from httpx import AsyncClient, ASGITransport
from tempo_os.main import app
from tempo_os.kernel.redis_client import inject_redis_for_test


class TestWorkflowAPI:
    @pytest.fixture(autouse=True)
    def setup_redis(self, mock_redis):
        inject_redis_for_test(mock_redis)

    @pytest.mark.asyncio
    async def test_start_single_node(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post("/api/workflow/start",
                json={"node_id": "echo", "params": {"input": "hi"}},
                headers={"X-Tenant-Id": "test_tenant"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "session_id" in data
            assert data["state"] == "done"

    @pytest.mark.asyncio
    async def test_start_flow(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post("/api/workflow/start",
                json={"flow_id": "echo_test_flow", "params": {"input": "data"}},
                headers={"X-Tenant-Id": "test_tenant"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["flow_id"] == "echo_test_flow"
            assert data["state"] == "echoed"

    @pytest.mark.asyncio
    async def test_push_event_and_complete(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            # Start flow
            resp = await c.post("/api/workflow/start",
                json={"flow_id": "echo_test_flow", "params": {"input": "data"}},
                headers={"X-Tenant-Id": "test_tenant"},
            )
            session_id = resp.json()["session_id"]

            # Push event to advance
            resp = await c.post(f"/api/workflow/{session_id}/event",
                json={"event_type": "USER_CONFIRM"},
                headers={"X-Tenant-Id": "test_tenant"},
            )
            assert resp.status_code == 200
            assert resp.json()["new_state"] == "end"

    @pytest.mark.asyncio
    async def test_get_state(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            # Start flow first
            resp = await c.post("/api/workflow/start",
                json={"flow_id": "echo_test_flow", "params": {}},
                headers={"X-Tenant-Id": "test_tenant"},
            )
            session_id = resp.json()["session_id"]

            # Query state
            resp = await c.get(f"/api/workflow/{session_id}/state",
                headers={"X-Tenant-Id": "test_tenant"},
            )
            assert resp.status_code == 200
            assert resp.json()["session_id"] == session_id

    @pytest.mark.asyncio
    async def test_terminate(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post("/api/workflow/start",
                json={"flow_id": "echo_test_flow", "params": {}},
                headers={"X-Tenant-Id": "test_tenant"},
            )
            session_id = resp.json()["session_id"]

            resp = await c.delete(f"/api/workflow/{session_id}",
                headers={"X-Tenant-Id": "test_tenant"},
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "terminated"

    @pytest.mark.asyncio
    async def test_missing_tenant_401(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post("/api/workflow/start",
                json={"node_id": "echo"},
            )
            assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_flow_404(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post("/api/workflow/start",
                json={"flow_id": "nonexistent_flow"},
                headers={"X-Tenant-Id": "test_tenant"},
            )
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_no_flow_or_node_400(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post("/api/workflow/start",
                json={"params": {}},
                headers={"X-Tenant-Id": "test_tenant"},
            )
            assert resp.status_code == 400
