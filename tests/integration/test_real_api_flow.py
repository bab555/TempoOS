# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.
"""
Integration test: Full API flow with REAL Redis.

Tests the complete pipeline:
  HTTP API → SessionManager → FSM → Node Execution → Blackboard → Response
"""

import pytest
from httpx import AsyncClient, ASGITransport
from tempo_os.main import app

HEADERS = {"X-Tenant-Id": "integration_test"}


class TestRealAPIFlow:
    @pytest.fixture(autouse=True)
    async def setup(self, real_redis):
        """Use real Redis for all tests in this class."""
        pass

    @pytest.mark.asyncio
    async def test_full_echo_flow_lifecycle(self):
        """
        Complete flow lifecycle:
        1. Start echo_test_flow
        2. Verify state = echoed (waiting_user)
        3. Push USER_CONFIRM
        4. Verify state = end (completed)
        """
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            # 1. Start
            resp = await c.post("/api/workflow/start",
                json={"flow_id": "echo_test_flow", "params": {"input": "real test"}},
                headers=HEADERS,
            )
            assert resp.status_code == 200
            data = resp.json()
            session_id = data["session_id"]
            assert data["state"] == "echoed"
            assert data["ui_schema"] is not None
            assert "echo" in str(data["ui_schema"]).lower() or "Echo" in str(data["ui_schema"])

            # 2. Check state
            resp = await c.get(f"/api/workflow/{session_id}/state", headers=HEADERS)
            assert resp.status_code == 200
            state = resp.json()
            assert state["current_state"] == "echoed"
            assert state["session_state"] == "waiting_user"
            assert "USER_CONFIRM" in state["valid_events"]

            # 3. Advance
            resp = await c.post(f"/api/workflow/{session_id}/event",
                json={"event_type": "USER_CONFIRM"},
                headers=HEADERS,
            )
            assert resp.status_code == 200
            result = resp.json()
            assert result["new_state"] == "end"
            assert result["session_state"] == "completed"

    @pytest.mark.asyncio
    async def test_single_node_execution(self):
        """Test implicit session with echo node."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post("/api/workflow/start",
                json={"node_id": "echo", "params": {"input": "direct call"}},
                headers=HEADERS,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["state"] == "done"
            assert data["ui_schema"] is not None

    @pytest.mark.asyncio
    async def test_blackboard_persists_across_steps(self):
        """Verify that node artifacts persist in Blackboard and are readable via API."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            # Start echo flow
            resp = await c.post("/api/workflow/start",
                json={"flow_id": "echo_test_flow", "params": {"input": "persist test"}},
                headers=HEADERS,
            )
            session_id = resp.json()["session_id"]

            # Read Blackboard via State API
            resp = await c.get(f"/api/state/{session_id}", headers=HEADERS)
            assert resp.status_code == 200
            state = resp.json()["state"]
            # Session should have flow_id and session_state stored
            assert state.get("_flow_id") == "echo_test_flow"
            assert state.get("_session_state") in ("running", "waiting_user")

    @pytest.mark.asyncio
    async def test_abort_session(self):
        """Test Hard Stop via API."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            # Start
            resp = await c.post("/api/workflow/start",
                json={"flow_id": "echo_test_flow", "params": {}},
                headers=HEADERS,
            )
            session_id = resp.json()["session_id"]

            # Abort
            resp = await c.delete(f"/api/workflow/{session_id}", headers=HEADERS)
            assert resp.status_code == 200
            assert resp.json()["status"] == "terminated"

            # State should reflect error
            resp = await c.get(f"/api/state/{session_id}/_session_state", headers=HEADERS)
            assert resp.status_code == 200
            assert resp.json()["value"] == "error"

    @pytest.mark.asyncio
    async def test_registry_operations(self):
        """Test node/flow registration with real backend."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            # List builtin nodes
            resp = await c.get("/api/registry/nodes", headers=HEADERS)
            assert resp.status_code == 200
            nodes = resp.json()
            assert len(nodes) >= 5  # 5 builtin nodes
            echo_node = next((n for n in nodes if n["node_id"] == "echo"), None)
            assert echo_node is not None

            # Register webhook
            resp = await c.post("/api/registry/nodes",
                json={"node_id": "ext_test", "endpoint": "http://localhost:9999/execute", "name": "Test External"},
                headers=HEADERS,
            )
            assert resp.status_code == 200

            # Verify it appears
            resp = await c.get("/api/registry/nodes", headers=HEADERS)
            node_ids = {n["node_id"] for n in resp.json()}
            assert "ext_test" in node_ids

            # List flows
            resp = await c.get("/api/registry/flows", headers=HEADERS)
            assert resp.status_code == 200
            flows = resp.json()
            flow_ids = {f["flow_id"] for f in flows}
            assert "echo_test_flow" in flow_ids

            # Get flow details
            resp = await c.get("/api/registry/flows/echo_test_flow", headers=HEADERS)
            assert resp.status_code == 200
            detail = resp.json()
            assert detail["states"] == ["start", "echoed", "end"]

    @pytest.mark.asyncio
    async def test_metrics_update(self):
        """Verify metrics are updated after real operations."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            # Do an operation
            await c.post("/api/workflow/start",
                json={"node_id": "echo", "params": {"input": "metrics"}},
                headers=HEADERS,
            )
            # Check metrics
            resp = await c.get("/api/metrics")
            assert resp.status_code == 200
            data = resp.json()
            assert data["counters"].get("sessions_total", 0) >= 1
            assert data["counters"].get("node_exec:echo", 0) >= 1

    @pytest.mark.asyncio
    async def test_health_endpoint(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/health")
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"
