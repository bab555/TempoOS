# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.
"""
Integration test: Full echo flow via API.

Verifies the complete platform pipeline:
  API → SessionManager → FSM → NodeExecution → Blackboard → Response
"""

import pytest
from httpx import AsyncClient, ASGITransport
from tempo_os.main import app
from tempo_os.kernel.redis_client import inject_redis_for_test

HEADERS = {"X-Tenant-Id": "test_tenant"}


class TestEchoFlowE2E:
    @pytest.fixture(autouse=True)
    def setup_redis(self, mock_redis):
        inject_redis_for_test(mock_redis)

    @pytest.mark.asyncio
    async def test_single_node_echo(self):
        """Test implicit session: call echo node directly."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post("/api/workflow/start",
                json={"node_id": "echo", "params": {"input": "hello world"}},
                headers=HEADERS,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["state"] == "done"
            assert data["ui_schema"] is not None
            assert "session_id" in data

    @pytest.mark.asyncio
    async def test_echo_flow_full_cycle(self):
        """Test explicit flow: echo_test_flow start → STEP_DONE → wait → USER_CONFIRM → end."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            # 1. Start the flow
            resp = await c.post("/api/workflow/start",
                json={"flow_id": "echo_test_flow", "params": {"input": "test data"}},
                headers=HEADERS,
            )
            assert resp.status_code == 200
            data = resp.json()
            session_id = data["session_id"]
            assert data["flow_id"] == "echo_test_flow"
            # After start: echo node executed, FSM at "echoed" (waiting_user)
            assert data["state"] == "echoed"
            assert data["ui_schema"] is not None

            # 2. Check state
            resp = await c.get(f"/api/workflow/{session_id}/state", headers=HEADERS)
            assert resp.status_code == 200
            state_data = resp.json()
            assert state_data["current_state"] == "echoed"
            assert state_data["session_state"] == "waiting_user"
            assert "USER_CONFIRM" in state_data["valid_events"]

            # 3. Push USER_CONFIRM to finish
            resp = await c.post(f"/api/workflow/{session_id}/event",
                json={"event_type": "USER_CONFIRM"},
                headers=HEADERS,
            )
            assert resp.status_code == 200
            event_data = resp.json()
            assert event_data["new_state"] == "end"
            assert event_data["session_state"] == "completed"

    @pytest.mark.asyncio
    async def test_list_builtin_nodes(self):
        """Verify builtin nodes are registered and listable."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/registry/nodes", headers=HEADERS)
            assert resp.status_code == 200
            nodes = resp.json()
            node_ids = {n["node_id"] for n in nodes}
            assert "echo" in node_ids
            assert "conditional" in node_ids
            assert "transform" in node_ids

    @pytest.mark.asyncio
    async def test_list_flows(self):
        """Verify example flows are loaded."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/registry/flows", headers=HEADERS)
            assert resp.status_code == 200
            flows = resp.json()
            flow_ids = {f["flow_id"] for f in flows}
            assert "echo_test_flow" in flow_ids

    @pytest.mark.asyncio
    async def test_get_flow_details(self):
        """Verify flow details endpoint returns full definition."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/registry/flows/echo_test_flow", headers=HEADERS)
            assert resp.status_code == 200
            data = resp.json()
            assert data["states"] == ["start", "echoed", "end"]
            assert "builtin://echo" in data["state_node_map"].values()

    @pytest.mark.asyncio
    async def test_register_webhook_node(self):
        """Test registering an external webhook node."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post("/api/registry/nodes",
                json={
                    "node_id": "ext_service",
                    "endpoint": "http://example.com/execute",
                    "name": "External Service",
                },
                headers=HEADERS,
            )
            assert resp.status_code == 200
            assert resp.json()["node_type"] == "webhook"

            # Now it should appear in list
            resp = await c.get("/api/registry/nodes", headers=HEADERS)
            node_ids = {n["node_id"] for n in resp.json()}
            assert "ext_service" in node_ids

    @pytest.mark.asyncio
    async def test_blackboard_state_api(self):
        """Test reading/writing Blackboard state via API."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            # Write
            resp = await c.put("/api/state/test-session/my_key",
                json={"value": {"data": 42}},
                headers=HEADERS,
            )
            assert resp.status_code == 200

            # Read back
            resp = await c.get("/api/state/test-session/my_key", headers=HEADERS)
            assert resp.status_code == 200
            assert resp.json()["value"]["data"] == 42

            # Read all
            resp = await c.get("/api/state/test-session", headers=HEADERS)
            assert resp.status_code == 200
            assert "my_key" in resp.json()["state"]

    @pytest.mark.asyncio
    async def test_terminate_session(self):
        """Test aborting a session."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            # Start a flow
            resp = await c.post("/api/workflow/start",
                json={"flow_id": "echo_test_flow", "params": {"input": "data"}},
                headers=HEADERS,
            )
            session_id = resp.json()["session_id"]

            # Terminate
            resp = await c.delete(f"/api/workflow/{session_id}", headers=HEADERS)
            assert resp.status_code == 200
            assert resp.json()["status"] == "terminated"

    @pytest.mark.asyncio
    async def test_metrics_endpoint(self):
        """Verify metrics are updated after operations."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            # Do something to generate metrics
            await c.post("/api/workflow/start",
                json={"node_id": "echo", "params": {"input": "metric test"}},
                headers=HEADERS,
            )
            resp = await c.get("/api/metrics")
            assert resp.status_code == 200
            data = resp.json()
            assert data["counters"].get("sessions_total", 0) >= 1
