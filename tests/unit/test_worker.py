# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.
"""Unit tests for BaseWorker and EchoWorker."""

import asyncio
import pytest
from tempo_os.kernel.bus import RedisBus
from tempo_os.workers.std.echo import EchoWorker
from tempo_os.protocols.schema import TempoEvent
from tempo_os.protocols.events import CMD_EXECUTE, EVENT_RESULT


class TestEchoWorker:
    @pytest.mark.asyncio
    async def test_echo_process(self, mock_redis):
        bus = RedisBus(mock_redis, "test_tenant")
        worker = EchoWorker("echo", bus)

        evt = TempoEvent.create(
            type=CMD_EXECUTE,
            source="test",
            target="echo",
            tenant_id="test_tenant",
            session_id="s_001",
            payload={"input": "hello world"},
        )
        result = await worker.process(evt)
        assert result == "echo: hello world"

    @pytest.mark.asyncio
    async def test_echo_empty_input(self, mock_redis):
        bus = RedisBus(mock_redis, "test_tenant")
        worker = EchoWorker("echo", bus)

        evt = TempoEvent.create(
            type=CMD_EXECUTE,
            source="test",
            target="echo",
            tenant_id="test_tenant",
            session_id="s_001",
            payload={},
        )
        result = await worker.process(evt)
        assert result == "echo: "
