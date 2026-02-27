# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Blackboard — Redis-Centric Shared State Memory.

Implements the "Blackboard Pattern" with complete tenant isolation.
All keys are namespaced: tempo:{tenant_id}:{resource_type}:{resource_id}

Session keys are automatically refreshed with TTL on every write to
prevent stale data from accumulating in Redis.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import redis.asyncio as aioredis

from tempo_os.kernel.namespace import get_key, get_results_key

logger = logging.getLogger("tempo.blackboard")

DEFAULT_ARTIFACT_TTL = 7 * 24 * 3600  # 7 days
DEFAULT_SESSION_TTL = 1800  # 30 min, overridden by config


class TenantBlackboard:
    """
    Tenant-scoped shared state manager backed by Redis.

    Every operation is automatically scoped to the bound tenant_id.
    Session keys are refreshed with TTL on every write.
    """

    def __init__(
        self,
        redis: aioredis.Redis,
        tenant_id: str,
        session_ttl: int = DEFAULT_SESSION_TTL,
    ) -> None:
        self._redis = redis
        self._tenant_id = tenant_id
        self._session_ttl = session_ttl

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    # ── Session State ───────────────────────────────────────────

    async def set_state(
        self,
        session_id: str,
        key: str,
        value: Any,
    ) -> None:
        """
        Set a state variable for a session.

        Redis key: tempo:{tenant_id}:session:{session_id}
        Hash field: {key}
        TTL is refreshed on every write.
        """
        redis_key = get_key(self._tenant_id, "session", session_id)
        serialized = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
        await self._redis.hset(redis_key, key, serialized)
        await self._redis.expire(redis_key, self._session_ttl)

    async def get_state(
        self,
        session_id: str,
        key: Optional[str] = None,
    ) -> Any:
        """
        Get state for a session.

        If key is provided, returns that specific field.
        Otherwise returns all fields as a dict.
        """
        redis_key = get_key(self._tenant_id, "session", session_id)
        if key:
            raw = await self._redis.hget(redis_key, key)
            if raw is None:
                return None
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return raw
        else:
            raw_dict = await self._redis.hgetall(redis_key)
            result = {}
            for k, v in raw_dict.items():
                try:
                    result[k] = json.loads(v)
                except (json.JSONDecodeError, TypeError):
                    result[k] = v
            return result

    async def delete_state(self, session_id: str, key: str) -> None:
        """Remove a specific state key from a session."""
        redis_key = get_key(self._tenant_id, "session", session_id)
        await self._redis.hdel(redis_key, key)

    # ── Accumulated Results ──────────────────────────────────────

    async def append_result(
        self,
        session_id: str,
        tool_name: str,
        data: Any,
    ) -> int:
        """
        Append a tool result to an accumulated list (Redis List via RPUSH).

        Unlike set_state which overwrites, this accumulates results so
        multiple search/query calls within a ReAct loop are all preserved.

        Returns the new list length.
        """
        redis_key = get_results_key(self._tenant_id, session_id, tool_name)
        serialized = json.dumps(data, ensure_ascii=False)
        length = await self._redis.rpush(redis_key, serialized)
        await self._redis.expire(redis_key, self._session_ttl)
        return length

    async def get_results(
        self,
        session_id: str,
        tool_name: str,
        limit: int = 10,
    ) -> List[Any]:
        """Read accumulated tool results (most recent `limit` entries)."""
        redis_key = get_results_key(self._tenant_id, session_id, tool_name)
        raw_list = await self._redis.lrange(redis_key, -limit, -1)
        results = []
        for raw in raw_list:
            try:
                results.append(json.loads(raw))
            except (json.JSONDecodeError, TypeError):
                results.append(raw)
        return results

    # ── Artifacts ───────────────────────────────────────────────

    async def push_artifact(
        self,
        session_id: str,
        artifact_id: str,
        data: Dict[str, Any],
        ttl: int = DEFAULT_ARTIFACT_TTL,
    ) -> None:
        """
        Store an artifact (file metadata, generated doc, etc.).

        Redis key: tempo:{tenant_id}:artifact:{artifact_id}
        """
        redis_key = get_key(self._tenant_id, "artifact", artifact_id)
        data["_session_id"] = session_id
        await self._redis.set(
            redis_key,
            json.dumps(data, ensure_ascii=False),
            ex=ttl,
        )
        session_key = get_key(self._tenant_id, "session", f"{session_id}:artifacts")
        await self._redis.sadd(session_key, artifact_id)
        await self._redis.expire(session_key, self._session_ttl)

    async def get_artifact(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve an artifact by ID."""
        redis_key = get_key(self._tenant_id, "artifact", artifact_id)
        raw = await self._redis.get(redis_key)
        if raw is None:
            return None
        return json.loads(raw)

    async def set_artifact_ttl(self, artifact_id: str, seconds: int) -> bool:
        """Update the TTL of an artifact."""
        redis_key = get_key(self._tenant_id, "artifact", artifact_id)
        return await self._redis.expire(redis_key, seconds)

    async def list_session_artifacts(self, session_id: str) -> List[str]:
        """List all artifact IDs belonging to a session."""
        session_key = get_key(self._tenant_id, "session", f"{session_id}:artifacts")
        return list(await self._redis.smembers(session_key))

    # ── Session Management ──────────────────────────────────────

    async def list_sessions(self) -> List[str]:
        """List all active session IDs for this tenant."""
        pattern = get_key(self._tenant_id, "session", "*")
        sessions = set()
        async for key in self._redis.scan_iter(match=pattern):
            parts = key.split(":")
            if len(parts) >= 4:
                sess_id = parts[3]
                if ":" not in sess_id:
                    sessions.add(sess_id)
        return sorted(sessions)

    async def clear_session(self, session_id: str) -> None:
        """Delete all state for a session (including results and artifacts list)."""
        redis_key = get_key(self._tenant_id, "session", session_id)
        await self._redis.delete(redis_key)
        art_key = get_key(self._tenant_id, "session", f"{session_id}:artifacts")
        await self._redis.delete(art_key)
        # Clean up accumulated results
        for tool in ("search", "data_query"):
            rk = get_results_key(self._tenant_id, session_id, tool)
            await self._redis.delete(rk)

    # ── Signals ─────────────────────────────────────────────────

    async def set_signal(self, session_id: str, signal_name: str, value: bool = True) -> None:
        """Set a signal flag on the blackboard."""
        await self.set_state(session_id, f"signal:{signal_name}", value)

    async def get_signal(self, session_id: str, signal_name: str) -> bool:
        """Read a signal flag (defaults to False if not set)."""
        val = await self.get_state(session_id, f"signal:{signal_name}")
        return bool(val) if val is not None else False
