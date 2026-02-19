# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.
"""Unit tests for RedisBus."""

import asyncio
import pytest
from tempo_os.kernel.bus import RedisBus
from tempo_os.protocols.schema import TempoEvent
from tempo_os.protocols.events import CMD_EXECUTE, EVENT_RESULT


class TestRedisBus:
    @pytest.mark.asyncio
    async def test_publish_and_subscribe(self, mock_redis):
        bus = RedisBus(mock_redis, "test_tenant")
        received = []

        async def handler(event: TempoEvent):
            received.append(event)

        await bus.subscribe(handler)

        evt = TempoEvent.create(
            type=CMD_EXECUTE,
            source="test",
            tenant_id="test_tenant",
            session_id="s_001",
            payload={"input": "hello"},
        )
        await bus.publish(evt)
        await asyncio.sleep(0.1)

        assert len(received) >= 1
        assert received[0].type == CMD_EXECUTE

    @pytest.mark.asyncio
    async def test_tenant_isolation(self, mock_redis):
        bus_a = RedisBus(mock_redis, "tenant_a")
        bus_b = RedisBus(mock_redis, "tenant_b")

        received_a = []
        received_b = []

        await bus_a.subscribe(lambda e: received_a.append(e))
        await bus_b.subscribe(lambda e: received_b.append(e))

        evt = TempoEvent.create(
            type=CMD_EXECUTE, source="test",
            tenant_id="tenant_a", session_id="s_001",
        )
        await bus_a.publish(evt)
        await asyncio.sleep(0.1)

        # tenant_b should NOT receive tenant_a's event
        assert len(received_b) == 0

    @pytest.mark.asyncio
    async def test_publish_wrong_tenant_raises(self, mock_redis):
        bus = RedisBus(mock_redis, "tenant_a")
        evt = TempoEvent.create(
            type=CMD_EXECUTE, source="test",
            tenant_id="tenant_b", session_id="s_001",
        )
        with pytest.raises(ValueError, match="does not match"):
            await bus.publish(evt)

    @pytest.mark.asyncio
    async def test_event_filter(self, mock_redis):
        bus = RedisBus(mock_redis, "test_tenant")
        results_only = []

        await bus.subscribe(lambda e: results_only.append(e), event_filter=EVENT_RESULT)

        cmd = TempoEvent.create(
            type=CMD_EXECUTE, source="test",
            tenant_id="test_tenant", session_id="s_001",
        )
        result = TempoEvent.create(
            type=EVENT_RESULT, source="worker",
            tenant_id="test_tenant", session_id="s_001",
        )
        await bus.publish(cmd)
        await bus.publish(result)
        await asyncio.sleep(0.1)

        # Only EVENT_RESULT should be received
        assert all(e.type == EVENT_RESULT for e in results_only)

    @pytest.mark.asyncio
    async def test_close_cleanup(self, mock_redis):
        bus = RedisBus(mock_redis, "test_tenant")
        await bus.subscribe(lambda e: None)
        await bus.close()
        assert len(bus._subscribers) == 0
