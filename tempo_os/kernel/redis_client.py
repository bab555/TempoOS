# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Redis Connection Factory — Async connection pool for all TempoOS components.
"""

from __future__ import annotations

from typing import Optional

import redis.asyncio as aioredis

from tempo_os.core.config import settings

_pool: Optional[aioredis.Redis] = None


async def get_redis_pool() -> aioredis.Redis:
    """
    Return a singleton async Redis connection pool.

    Reads connection URL from TempoSettings (env-driven).
    """
    global _pool
    if _pool is None:
        _pool = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            max_connections=20,
        )
    return _pool


async def close_redis_pool() -> None:
    """Gracefully close the Redis connection pool."""
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


def inject_redis_for_test(redis_instance: aioredis.Redis) -> None:
    """Inject a fake/mock Redis instance (for testing only)."""
    global _pool
    _pool = redis_instance
