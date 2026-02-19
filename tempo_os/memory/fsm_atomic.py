# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Atomic FSM Advance — Redis Lua CAS for concurrent safety.

Solves the race condition in TempoFSM.advance() where read-then-write
across two Redis calls can cause duplicate state transitions under
multi-instance deployment.

Strategy: Lua script does compare-and-set in a single atomic Redis call.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional, Tuple

import redis.asyncio as aioredis

from tempo_os.kernel.namespace import get_key
from tempo_os.memory.fsm import TempoFSM, InvalidTransitionError, FSM_STATE_KEY

logger = logging.getLogger("tempo.fsm_atomic")

# Lua CAS script: atomically check current state and set new state
_LUA_CAS_SCRIPT = """
local key = KEYS[1]
local field = ARGV[1]
local expected = ARGV[2]
local new_state = ARGV[3]

local current = redis.call('HGET', key, field)
if current == false then
    -- No state set yet: treat as initial state match
    if expected == ARGV[4] then
        redis.call('HSET', key, field, new_state)
        return new_state
    else
        return redis.error_reply('CONFLICT:nil:expected=' .. expected)
    end
end

if current == expected then
    redis.call('HSET', key, field, new_state)
    return new_state
else
    return redis.error_reply('CONFLICT:' .. current .. ':expected=' .. expected)
end
"""


class ConflictError(Exception):
    """Raised when FSM advance conflicts with another concurrent advance."""
    def __init__(self, current_state: str, expected_state: str):
        self.current_state = current_state
        self.expected_state = expected_state
        super().__init__(
            f"FSM conflict: expected '{expected_state}' but found '{current_state}'"
        )


class AtomicFSM:
    """
    Thread-safe FSM that uses Redis Lua for atomic state transitions.

    Wraps TempoFSM for transition logic, but uses Lua CAS for the
    actual state read-compare-write operation.
    """

    def __init__(
        self,
        fsm: TempoFSM,
        redis: aioredis.Redis,
        tenant_id: str,
    ) -> None:
        self._fsm = fsm
        self._redis = redis
        self._tenant_id = tenant_id
        self._script = self._redis.register_script(_LUA_CAS_SCRIPT)

    @property
    def fsm(self) -> TempoFSM:
        return self._fsm

    async def advance_atomic(
        self, session_id: str, event_type: str
    ) -> str:
        """
        Atomically advance FSM state using Lua CAS.

        1. Compute expected current state (from transition table)
        2. Compute target state
        3. Execute Lua CAS: if current matches expected, set to target
        4. If conflict, raise ConflictError

        Returns the new state on success.
        """
        # First, figure out what state we expect and where we'd go
        redis_key = get_key(self._tenant_id, "session", session_id)

        # Read current state to compute transition
        raw_current = await self._redis.hget(redis_key, FSM_STATE_KEY)
        current_state = raw_current if raw_current else self._fsm.initial_state

        # Compute the new state using FSM rules (pure logic, no side effects)
        try:
            new_state = self._fsm.transition(current_state, event_type)
        except InvalidTransitionError:
            raise

        # Atomic CAS via Lua
        try:
            result = await self._script(
                keys=[redis_key],
                args=[FSM_STATE_KEY, current_state, new_state, self._fsm.initial_state],
            )
            logger.info(
                "Atomic FSM: %s -[%s]-> %s (session=%s)",
                current_state, event_type, result, session_id,
            )
            return result
        except aioredis.ResponseError as e:
            error_msg = str(e)
            if error_msg.startswith("CONFLICT:"):
                parts = error_msg.split(":")
                actual = parts[1] if len(parts) > 1 else "unknown"
                raise ConflictError(actual, current_state)
            raise

    async def get_current_state(self, session_id: str) -> str:
        """Read current FSM state from Redis."""
        redis_key = get_key(self._tenant_id, "session", session_id)
        raw = await self._redis.hget(redis_key, FSM_STATE_KEY)
        return raw if raw else self._fsm.initial_state

    async def set_state(self, session_id: str, new_state: str) -> None:
        """Force-set FSM state (admin/recovery use only)."""
        redis_key = get_key(self._tenant_id, "session", session_id)
        await self._redis.hset(redis_key, FSM_STATE_KEY, new_state)
