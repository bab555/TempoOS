# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.
"""Integration tests with REAL Redis."""

import asyncio
import pytest
from tempo_os.kernel.bus import RedisBus
from tempo_os.memory.blackboard import TenantBlackboard
from tempo_os.memory.fsm import TempoFSM
from tempo_os.memory.fsm_atomic import AtomicFSM
from tempo_os.protocols.schema import TempoEvent
from tempo_os.protocols.events import CMD_EXECUTE, EVENT_RESULT
from tempo_os.resilience.stopper import HardStopper
from tempo_os.resilience.fan_in import FanInChecker

TENANT = "integration_test"


class TestRealRedisBus:
    @pytest.mark.asyncio
    async def test_publish_and_subscribe(self, real_redis):
        bus = RedisBus(real_redis, TENANT)
        received = []

        async def handler(event):
            received.append(event)

        await bus.subscribe(handler)

        evt = TempoEvent.create(
            type=CMD_EXECUTE, source="test",
            tenant_id=TENANT, session_id="s_int_001",
            payload={"data": "real redis test"},
        )
        await bus.publish(evt)
        await asyncio.sleep(0.2)

        assert len(received) >= 1
        assert received[0].payload["data"] == "real redis test"

        await bus.close()

    @pytest.mark.asyncio
    async def test_stream_push_and_read(self, real_redis):
        bus = RedisBus(real_redis, TENANT)

        evt = TempoEvent.create(
            type=EVENT_RESULT, source="test",
            tenant_id=TENANT, session_id="s_int_002",
            payload={"result": "stream test"},
        )
        stream_id = await bus.push_to_stream(evt)
        assert stream_id is not None

        events = await bus.read_stream()
        assert len(events) >= 1
        assert events[-1].payload["result"] == "stream test"

        await bus.close()


class TestRealBlackboard:
    @pytest.mark.asyncio
    async def test_state_roundtrip(self, real_redis):
        bb = TenantBlackboard(real_redis, TENANT)

        await bb.set_state("s_bb_001", "counter", 42)
        val = await bb.get_state("s_bb_001", "counter")
        assert val == 42

        await bb.set_state("s_bb_001", "name", "TempoOS")
        all_state = await bb.get_state("s_bb_001")
        assert all_state["name"] == "TempoOS"
        assert all_state["counter"] == 42

    @pytest.mark.asyncio
    async def test_artifact_roundtrip(self, real_redis):
        bb = TenantBlackboard(real_redis, TENANT)

        await bb.push_artifact("s_bb_002", "test_artifact", {
            "items": [1, 2, 3],
            "source": "integration_test",
        })
        art = await bb.get_artifact("test_artifact")
        assert art["items"] == [1, 2, 3]
        assert art["_session_id"] == "s_bb_002"

        artifacts = await bb.list_session_artifacts("s_bb_002")
        assert "test_artifact" in artifacts

    @pytest.mark.asyncio
    async def test_signals(self, real_redis):
        bb = TenantBlackboard(real_redis, TENANT)

        assert await bb.get_signal("s_bb_003", "abort") is False
        await bb.set_signal("s_bb_003", "abort", True)
        assert await bb.get_signal("s_bb_003", "abort") is True

    @pytest.mark.asyncio
    async def test_clear_session(self, real_redis):
        bb = TenantBlackboard(real_redis, TENANT)

        await bb.set_state("s_bb_clear", "key1", "val1")
        await bb.clear_session("s_bb_clear")
        state = await bb.get_state("s_bb_clear")
        assert state == {}


class TestRealFSM:
    @pytest.mark.asyncio
    async def test_fsm_advance_chain(self, real_redis):
        bb = TenantBlackboard(real_redis, TENANT)
        fsm = TempoFSM({
            "states": ["idle", "working", "done"],
            "initial_state": "idle",
            "transitions": [
                {"from": "idle", "event": "START", "to": "working"},
                {"from": "working", "event": "FINISH", "to": "done"},
            ],
        }, blackboard=bb)

        s1 = await fsm.advance("s_fsm_001", "START")
        assert s1 == "working"

        s2 = await fsm.advance("s_fsm_001", "FINISH")
        assert s2 == "done"

        current = await fsm.get_current_state("s_fsm_001")
        assert current == "done"

    @pytest.mark.asyncio
    async def test_atomic_fsm(self, real_redis):
        bb = TenantBlackboard(real_redis, TENANT)
        fsm = TempoFSM({
            "states": ["a", "b", "c"],
            "initial_state": "a",
            "transitions": [
                {"from": "a", "event": "GO", "to": "b"},
                {"from": "b", "event": "GO", "to": "c"},
            ],
        })
        atomic = AtomicFSM(fsm, real_redis, TENANT)

        new = await atomic.advance_atomic("s_atomic_001", "GO")
        assert new == "b"

        state = await atomic.get_current_state("s_atomic_001")
        assert state == "b"


class TestRealStopper:
    @pytest.mark.asyncio
    async def test_abort_and_check(self, real_redis):
        bb = TenantBlackboard(real_redis, TENANT)
        bus = RedisBus(real_redis, TENANT)
        stopper = HardStopper(real_redis, bus, bb)

        assert await stopper.is_aborted("s_stop_001") is False

        await stopper.abort("s_stop_001", "test abort")

        assert await stopper.is_aborted("s_stop_001") is True
        reason = await stopper.get_abort_reason("s_stop_001")
        assert reason == "test abort"

        await bus.close()


class TestRealFanIn:
    @pytest.mark.asyncio
    async def test_fan_in_with_real_artifacts(self, real_redis):
        bb = TenantBlackboard(real_redis, TENANT)
        checker = FanInChecker(bb)

        # Initially not all deps done
        assert await checker.all_deps_done("s_fan_001", ["dep_a", "dep_b"]) is False

        # Add one
        await bb.push_artifact("s_fan_001", "dep_a", {"done": True})
        assert await checker.all_deps_done("s_fan_001", ["dep_a", "dep_b"]) is False

        # Add second
        await bb.push_artifact("s_fan_001", "dep_b", {"done": True})
        assert await checker.all_deps_done("s_fan_001", ["dep_a", "dep_b"]) is True
