# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Redis Event Bus — The Blood Vessels.

Tenant-scoped Pub/Sub event bus for inter-component communication.
All channels are namespaced by tenant_id to ensure data isolation.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator, Callable, Coroutine, Any, Optional, List

import redis.asyncio as aioredis

from tempo_os.kernel.namespace import get_channel
from tempo_os.protocols.schema import TempoEvent

logger = logging.getLogger("tempo.bus")


class RedisBus:
    """
    Tenant-scoped Redis Pub/Sub event bus.

    Each RedisBus instance is bound to a single tenant_id.
    Publishing and subscribing happen on tenant-specific channels.
    """

    def __init__(self, redis: aioredis.Redis, tenant_id: str) -> None:
        self._redis = redis
        self._tenant_id = tenant_id
        self._channel = get_channel(tenant_id)
        self._subscribers: List[asyncio.Task] = []
        self._pubsub: Optional[aioredis.client.PubSub] = None

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    @property
    def channel(self) -> str:
        return self._channel

    # ── Publish ─────────────────────────────────────────────────

    async def publish(self, event: TempoEvent) -> int:
        """
        Publish a TempoEvent to the tenant-scoped channel.

        Returns the number of subscribers that received the message.
        """
        if event.tenant_id != self._tenant_id:
            raise ValueError(
                f"Event tenant_id '{event.tenant_id}' does not match "
                f"bus tenant_id '{self._tenant_id}'"
            )
        payload = event.to_json()
        count = await self._redis.publish(self._channel, payload)
        logger.debug(
            "Published %s to %s (%d receivers)",
            event.type, self._channel, count,
        )
        return count

    # ── Subscribe ───────────────────────────────────────────────

    async def subscribe(
        self,
        handler: Callable[[TempoEvent], Coroutine[Any, Any, None]],
        event_filter: Optional[str] = None,
    ) -> aioredis.client.PubSub:
        """
        Subscribe to the tenant-scoped channel and dispatch events to handler.

        Args:
            handler: Async callable invoked for each received TempoEvent.
            event_filter: If set, only events with this type are dispatched.

        Returns:
            The PubSub instance (for cleanup).
        """
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(self._channel)
        self._pubsub = pubsub

        async def _listener():
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    event = TempoEvent.from_json(message["data"])
                    if event_filter and event.type != event_filter:
                        continue
                    await handler(event)
                except Exception as exc:
                    logger.error("Bus handler error: %s", exc)

        task = asyncio.create_task(_listener())
        self._subscribers.append(task)
        return pubsub

    async def listen(self) -> AsyncIterator[TempoEvent]:
        """
        Async generator that yields TempoEvents from the channel.

        Usage:
            async for event in bus.listen():
                print(event.type)
        """
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(self._channel)
        self._pubsub = pubsub
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            try:
                yield TempoEvent.from_json(message["data"])
            except Exception as exc:
                logger.error("Bus listen parse error: %s", exc)

    # ── Stream (Redis Streams — durable ordered log) ────────────

    async def push_to_stream(self, event: TempoEvent) -> str:
        """
        Append event to a Redis Stream (for durable ordered log).

        Returns the stream entry ID.
        """
        stream_key = f"{self._channel}:stream"
        entry_id = await self._redis.xadd(
            stream_key,
            {"data": event.to_json()},
        )
        return entry_id

    async def read_stream(
        self,
        last_id: str = "0-0",
        count: int = 100,
    ) -> list[TempoEvent]:
        """Read events from the durable stream."""
        stream_key = f"{self._channel}:stream"
        entries = await self._redis.xrange(stream_key, min=last_id, count=count)
        events = []
        for _entry_id, fields in entries:
            events.append(TempoEvent.from_json(fields["data"]))
        return events

    # ── Cleanup ─────────────────────────────────────────────────

    async def close(self) -> None:
        """Unsubscribe and cancel all listener tasks."""
        for task in self._subscribers:
            task.cancel()
        self._subscribers.clear()
        if self._pubsub:
            await self._pubsub.unsubscribe(self._channel)
            await self._pubsub.aclose()
            self._pubsub = None
