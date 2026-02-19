# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.
"""Unit tests for SessionManager."""

import pytest
from tempo_os.kernel.session_manager import SessionManager
from tempo_os.kernel.flow_loader import load_flow_from_string

ECHO_FLOW_YAML = """
name: echo_flow
states: [start, echoed, end]
initial_state: start
state_node_map:
  start: builtin://echo
transitions:
  - { from: start, event: STEP_DONE, to: echoed }
  - { from: echoed, event: USER_CONFIRM, to: end }
user_input_states: [echoed]
"""


class TestSessionManager:
    @pytest.mark.asyncio
    async def test_start_flow(self, mock_redis):
        sm = SessionManager(mock_redis, "test_tenant")
        flow_def = load_flow_from_string(ECHO_FLOW_YAML)

        session_id = await sm.start_flow(flow_def, params={"input": "hello"})
        assert session_id is not None
        assert len(session_id) == 36  # UUID format

    @pytest.mark.asyncio
    async def test_start_flow_stores_metadata(self, mock_redis):
        sm = SessionManager(mock_redis, "test_tenant")
        flow_def = load_flow_from_string(ECHO_FLOW_YAML)

        session_id = await sm.start_flow(flow_def, params={"x": 1})
        state = await sm.get_session_state(session_id)
        assert state["_flow_id"] == "echo_flow"
        assert state["_session_state"] == "running"
        assert state["_params"]["x"] == 1

    @pytest.mark.asyncio
    async def test_start_single_node(self, mock_redis):
        sm = SessionManager(mock_redis, "test_tenant")

        session_id = await sm.start_single_node("echo", params={"input": "hi"})
        state = await sm.get_session_state(session_id)
        assert state["_node_id"] == "echo"
        assert state["_implicit"] is True

    @pytest.mark.asyncio
    async def test_get_session_status(self, mock_redis):
        sm = SessionManager(mock_redis, "test_tenant")
        flow_def = load_flow_from_string(ECHO_FLOW_YAML)

        session_id = await sm.start_flow(flow_def)
        status = await sm.get_session_status(session_id)
        assert status == "running"

    @pytest.mark.asyncio
    async def test_inherit_session(self, mock_redis):
        sm = SessionManager(mock_redis, "test_tenant")

        # Create first session with an artifact
        s1 = await sm.start_single_node("echo", params={"input": "data"})
        await sm.blackboard.push_artifact(s1, "result_01", {"value": 42})

        # Inherit into a new flow
        flow_def = load_flow_from_string(ECHO_FLOW_YAML)
        s2 = await sm.inherit_session(flow_def, from_session_id=s1)

        # New session should have the artifact
        art = await sm.blackboard.get_artifact("result_01")
        assert art is not None
        assert art["value"] == 42

    @pytest.mark.asyncio
    async def test_unknown_session_status(self, mock_redis):
        sm = SessionManager(mock_redis, "test_tenant")
        status = await sm.get_session_status("nonexistent")
        assert status == "unknown"
