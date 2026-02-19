# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.
"""Unit tests for Registry API."""

import pytest
from httpx import AsyncClient, ASGITransport
from tempo_os.main import app
from tempo_os.kernel.redis_client import inject_redis_for_test


class TestRegistryAPI:
    @pytest.fixture(autouse=True)
    def setup_redis(self, mock_redis):
        inject_redis_for_test(mock_redis)

    @pytest.mark.asyncio
    async def test_list_nodes(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/registry/nodes",
                headers={"X-Tenant-Id": "test_tenant"},
            )
            assert resp.status_code == 200
            assert isinstance(resp.json(), list)

    @pytest.mark.asyncio
    async def test_register_webhook_node(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post("/api/registry/nodes",
                json={
                    "node_id": "ext_svc",
                    "endpoint": "http://example.com/webhook",
                    "name": "External Service",
                },
                headers={"X-Tenant-Id": "test_tenant"},
            )
            assert resp.status_code == 200
            assert resp.json()["node_type"] == "webhook"

    @pytest.mark.asyncio
    async def test_list_flows(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/registry/flows",
                headers={"X-Tenant-Id": "test_tenant"},
            )
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_register_flow(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post("/api/registry/flows",
                json={
                    "flow_id": "test_flow",
                    "name": "Test Flow",
                    "yaml_content": "states: [a, b]",
                },
                headers={"X-Tenant-Id": "test_tenant"},
            )
            assert resp.status_code == 200
            assert resp.json()["flow_id"] == "test_flow"
