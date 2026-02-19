# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Hard Stop — Emergency session termination.

Sets abort markers in Redis + Blackboard, publishes ABORT event.
Running builtin nodes check the abort signal and self-terminate.
Webhook callbacks are ignored after abort.
"""

from __future__ import annotations

import logging
from typing import Optional

import redis.asyncio as aioredis

from tempo_os.kernel.namespace import get_key
from tempo_os.kernel.bus import RedisBus
from tempo_os.memory.blackboard import TenantBlackboard
from tempo_os.protocols.schema import TempoEvent
from tempo_os.protocols.events import ABORT

logger = logging.getLogger("tempo.stopper")


class HardStopper:
    """Emergency session termination."""

    def __init__(
        self,
        redis: aioredis.Redis,
        bus: RedisBus,
        blackboard: TenantBlackboard,
    ) -> None:
        self._redis = redis
        self._bus = bus
        self._blackboard = blackboard

    async def abort(
        self,
        session_id: str,
        reason: str,
        trace_id: Optional[str] = None,
    ) -> None:
        """
        Immediately terminate a session.

        1. Set Redis abort marker
        2. Set Blackboard abort signal
        3. Publish ABORT event
        """
        tenant_id = self._blackboard.tenant_id

        # 1. Redis abort marker (for fast polling)
        abort_key = get_key(tenant_id, "abort", session_id)
        await self._redis.set(abort_key, reason, ex=3600)

        # 2. Blackboard signal (for node-level checks)
        await self._blackboard.set_signal(session_id, "abort", True)

        # 3. Update session state
        await self._blackboard.set_state(session_id, "_session_state", "error")

        # 4. Publish ABORT event
        await self._bus.publish(TempoEvent.create(
            type=ABORT,
            source="hard_stopper",
            tenant_id=tenant_id,
            session_id=session_id,
            payload={"reason": reason},
            trace_id=trace_id,
        ))

        logger.warning("Session %s ABORTED: %s", session_id, reason)

    async def is_aborted(self, session_id: str) -> bool:
        """Check if a session has been aborted (fast Redis check)."""
        tenant_id = self._blackboard.tenant_id
        abort_key = get_key(tenant_id, "abort", session_id)
        return await self._redis.exists(abort_key) > 0

    async def get_abort_reason(self, session_id: str) -> Optional[str]:
        """Get the abort reason (if aborted)."""
        tenant_id = self._blackboard.tenant_id
        abort_key = get_key(tenant_id, "abort", session_id)
        return await self._redis.get(abort_key)
