# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.
"""Unit tests for HardStopper."""

import pytest
from tempo_os.kernel.bus import RedisBus
from tempo_os.memory.blackboard import TenantBlackboard
from tempo_os.resilience.stopper import HardStopper


class TestHardStopper:
    @pytest.mark.asyncio
    async def test_abort_sets_markers(self, mock_redis):
        bb = TenantBlackboard(mock_redis, "test_tenant")
        bus = RedisBus(mock_redis, "test_tenant")
        stopper = HardStopper(mock_redis, bus, bb)

        await stopper.abort("s_001", "test abort reason")

        assert await stopper.is_aborted("s_001") is True
        assert await bb.get_signal("s_001", "abort") is True
        state = await bb.get_state("s_001", "_session_state")
        assert state == "error"

    @pytest.mark.asyncio
    async def test_not_aborted_by_default(self, mock_redis):
        bb = TenantBlackboard(mock_redis, "test_tenant")
        bus = RedisBus(mock_redis, "test_tenant")
        stopper = HardStopper(mock_redis, bus, bb)

        assert await stopper.is_aborted("s_new") is False

    @pytest.mark.asyncio
    async def test_get_abort_reason(self, mock_redis):
        bb = TenantBlackboard(mock_redis, "test_tenant")
        bus = RedisBus(mock_redis, "test_tenant")
        stopper = HardStopper(mock_redis, bus, bb)

        await stopper.abort("s_001", "timeout exceeded")
        reason = await stopper.get_abort_reason("s_001")
        assert reason == "timeout exceeded"
