# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Redis Connection Factory — Async connection pool for all TempoOS components.
"""

from __future__ import annotations

from typing import Optional

import redis.asyncio as aioredis
from redis.backoff import ExponentialBackoff
from redis.retry import Retry
from redis.exceptions import (
    ConnectionError,
    TimeoutError,
    BusyLoadingError,
)

from tempo_os.core.config import settings

_pool: Optional[aioredis.Redis] = None

_RETRY = Retry(ExponentialBackoff(cap=2, base=0.1), retries=3)
_RETRY_ERRORS = [ConnectionError, TimeoutError, BusyLoadingError, OSError]


async def get_redis_pool() -> aioredis.Redis:
    """
    Return a singleton async Redis connection pool.

    Reads connection URL from TempoSettings (env-driven).
    Uses retry-on-error so stale pool connections are transparently reconnected.
    """
    global _pool
    if _pool is None:
        _pool = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            max_connections=20,
            health_check_interval=15,
            retry_on_timeout=True,
            retry_on_error=_RETRY_ERRORS,
            retry=_RETRY,
            socket_connect_timeout=5,
            socket_timeout=10,
            socket_keepalive=True,
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
