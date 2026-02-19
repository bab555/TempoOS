# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.
"""Unit tests for AtomicFSM (Lua CAS)."""

import pytest
from tempo_os.memory.fsm import TempoFSM, InvalidTransitionError
from tempo_os.memory.fsm_atomic import AtomicFSM, ConflictError

SIMPLE_CONFIG = {
    "states": ["idle", "working", "done"],
    "initial_state": "idle",
    "transitions": [
        {"from": "idle", "event": "START", "to": "working"},
        {"from": "working", "event": "FINISH", "to": "done"},
    ],
}


class TestAtomicFSM:
    @pytest.mark.asyncio
    async def test_advance_from_initial(self, mock_redis):
        fsm = TempoFSM(SIMPLE_CONFIG)
        atomic = AtomicFSM(fsm, mock_redis, "test_tenant")

        new_state = await atomic.advance_atomic("s_001", "START")
        assert new_state == "working"

    @pytest.mark.asyncio
    async def test_advance_chain(self, mock_redis):
        fsm = TempoFSM(SIMPLE_CONFIG)
        atomic = AtomicFSM(fsm, mock_redis, "test_tenant")

        await atomic.advance_atomic("s_001", "START")
        new_state = await atomic.advance_atomic("s_001", "FINISH")
        assert new_state == "done"

    @pytest.mark.asyncio
    async def test_invalid_transition_raises(self, mock_redis):
        fsm = TempoFSM(SIMPLE_CONFIG)
        atomic = AtomicFSM(fsm, mock_redis, "test_tenant")

        with pytest.raises(InvalidTransitionError):
            await atomic.advance_atomic("s_001", "FINISH")  # idle cannot FINISH

    @pytest.mark.asyncio
    async def test_get_current_state_initial(self, mock_redis):
        fsm = TempoFSM(SIMPLE_CONFIG)
        atomic = AtomicFSM(fsm, mock_redis, "test_tenant")

        state = await atomic.get_current_state("new_session")
        assert state == "idle"

    @pytest.mark.asyncio
    async def test_get_current_state_after_advance(self, mock_redis):
        fsm = TempoFSM(SIMPLE_CONFIG)
        atomic = AtomicFSM(fsm, mock_redis, "test_tenant")

        await atomic.advance_atomic("s_001", "START")
        state = await atomic.get_current_state("s_001")
        assert state == "working"

    @pytest.mark.asyncio
    async def test_force_set_state(self, mock_redis):
        fsm = TempoFSM(SIMPLE_CONFIG)
        atomic = AtomicFSM(fsm, mock_redis, "test_tenant")

        await atomic.set_state("s_001", "done")
        state = await atomic.get_current_state("s_001")
        assert state == "done"
