# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
TempoClock — Heartbeat and timeout detection.

Provides a logical clock tick and periodic health checks.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Coroutine, Any, Optional

logger = logging.getLogger("tempo.clock")


class TempoClock:
    """
    Logical clock that emits periodic ticks.

    Used for:
      - Heartbeat / liveness detection
      - Session TTL expiry scanning
      - Timeout detection for long-running nodes
    """

    def __init__(self, interval: float = 0.2) -> None:
        """
        Args:
            interval: Tick interval in seconds.
        """
        self._interval = interval
        self._tick: int = 0
        self._running: bool = False
        self._task: Optional[asyncio.Task] = None
        self._callbacks: list[Callable[[int], Coroutine[Any, Any, None]]] = []

    @property
    def tick(self) -> int:
        """Current logical tick value."""
        return self._tick

    @property
    def running(self) -> bool:
        return self._running

    def on_tick(self, callback: Callable[[int], Coroutine[Any, Any, None]]) -> None:
        """Register an async callback to be invoked on each tick."""
        self._callbacks.append(callback)

    async def start(self) -> None:
        """Start the clock loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("TempoClock started (interval=%.2fs)", self._interval)

    async def _loop(self) -> None:
        """Internal tick loop."""
        while self._running:
            self._tick += 1
            for cb in self._callbacks:
                try:
                    await cb(self._tick)
                except Exception as exc:
                    logger.error("Clock callback error at tick %d: %s", self._tick, exc)
            await asyncio.sleep(self._interval)

    async def stop(self) -> None:
        """Stop the clock loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("TempoClock stopped at tick %d", self._tick)
