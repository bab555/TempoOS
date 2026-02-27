# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.
"""Unit tests for TempoClock."""

import asyncio
import pytest
from tempo_os.kernel.clock import TempoClock


class TestTempoClock:
    @pytest.mark.asyncio
    async def test_tick_increments(self):
        clock = TempoClock(interval=0.05)
        await clock.start()
        await asyncio.sleep(0.2)
        await clock.stop()
        assert clock.tick >= 2  # At least 2 ticks in 0.2s at 0.05 interval

    @pytest.mark.asyncio
    async def test_callback_invoked(self):
        ticks_seen = []
        clock = TempoClock(interval=0.05)
        clock.on_tick(lambda t: ticks_seen.append(t) or asyncio.sleep(0))
        await clock.start()
        await asyncio.sleep(0.15)
        await clock.stop()
        assert len(ticks_seen) >= 1

    @pytest.mark.asyncio
    async def test_stop_is_idempotent(self):
        clock = TempoClock(interval=0.05)
        await clock.start()
        await clock.stop()
        await clock.stop()  # Should not raise

    def test_initial_state(self):
        clock = TempoClock()
        assert clock.tick == 0
        assert clock.running is False
