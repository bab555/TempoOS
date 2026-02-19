# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.
"""Unit tests for TempoFSM."""

import pytest
from tempo_os.memory.fsm import TempoFSM, InvalidTransitionError
from tempo_os.memory.blackboard import TenantBlackboard


SIMPLE_FSM_CONFIG = {
    "states": ["idle", "working", "done"],
    "initial_state": "idle",
    "transitions": [
        {"from": "idle", "event": "START", "to": "working"},
        {"from": "working", "event": "FINISH", "to": "done"},
    ],
}


class TestTempoFSM:
    def test_pure_transition(self):
        fsm = TempoFSM(SIMPLE_FSM_CONFIG)
        assert fsm.transition("idle", "START") == "working"
        assert fsm.transition("working", "FINISH") == "done"

    def test_invalid_transition_raises(self):
        fsm = TempoFSM(SIMPLE_FSM_CONFIG)
        with pytest.raises(InvalidTransitionError):
            fsm.transition("idle", "FINISH")

    def test_get_valid_events(self):
        fsm = TempoFSM(SIMPLE_FSM_CONFIG)
        events = fsm.get_valid_events("idle")
        assert events == ["START"]

    def test_states_property(self):
        fsm = TempoFSM(SIMPLE_FSM_CONFIG)
        assert fsm.states == ["idle", "working", "done"]

    def test_initial_state(self):
        fsm = TempoFSM(SIMPLE_FSM_CONFIG)
        assert fsm.initial_state == "idle"

    @pytest.mark.asyncio
    async def test_advance_with_blackboard(self, mock_redis):
        bb = TenantBlackboard(mock_redis, "test_tenant")
        fsm = TempoFSM(SIMPLE_FSM_CONFIG, blackboard=bb)

        new_state = await fsm.advance("s_001", "START")
        assert new_state == "working"

        current = await fsm.get_current_state("s_001")
        assert current == "working"

    @pytest.mark.asyncio
    async def test_advance_chain(self, mock_redis):
        bb = TenantBlackboard(mock_redis, "test_tenant")
        fsm = TempoFSM(SIMPLE_FSM_CONFIG, blackboard=bb)

        await fsm.advance("s_001", "START")
        await fsm.advance("s_001", "FINISH")

        current = await fsm.get_current_state("s_001")
        assert current == "done"

    @pytest.mark.asyncio
    async def test_advance_invalid_raises(self, mock_redis):
        bb = TenantBlackboard(mock_redis, "test_tenant")
        fsm = TempoFSM(SIMPLE_FSM_CONFIG, blackboard=bb)

        with pytest.raises(InvalidTransitionError):
            await fsm.advance("s_001", "FINISH")  # idle cannot FINISH

    @pytest.mark.asyncio
    async def test_initial_state_from_blackboard(self, mock_redis):
        bb = TenantBlackboard(mock_redis, "test_tenant")
        fsm = TempoFSM(SIMPLE_FSM_CONFIG, blackboard=bb)

        state = await fsm.get_current_state("new_session")
        assert state == "idle"  # Falls back to initial_state


CHAIN_FSM_CONFIG = {
    "states": ["a", "b", "c", "d"],
    "initial_state": "a",
    "transitions": [
        {"from": "a", "event": "NEXT", "to": "b"},
        {"from": "b", "event": "NEXT", "to": "c"},
        {"from": "c", "event": "NEXT", "to": "d"},
    ],
}


class TestFSMChain:
    @pytest.mark.asyncio
    async def test_four_state_chain(self, mock_redis):
        bb = TenantBlackboard(mock_redis, "test_tenant")
        fsm = TempoFSM(CHAIN_FSM_CONFIG, blackboard=bb)

        for expected in ["b", "c", "d"]:
            state = await fsm.advance("s_chain", "NEXT")
            assert state == expected
