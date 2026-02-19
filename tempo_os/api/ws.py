# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
WebSocket Event Push — Real-time event streaming to frontend.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from tempo_os.kernel.redis_client import get_redis_pool
from tempo_os.kernel.bus import RedisBus

router = APIRouter()
logger = logging.getLogger("tempo.ws")


@router.websocket("/ws/events/{session_id}")
async def websocket_events(websocket: WebSocket, session_id: str):
    """
    WebSocket endpoint for real-time event streaming.

    - Subscribes to the tenant's Redis Bus channel
    - Filters events for the given session_id
    - Pushes matching events as JSON to the client
    - Also accepts events FROM the client (bidirectional)
    """
    await websocket.accept()

    # Extract tenant_id from query params or first message
    tenant_id = websocket.query_params.get("tenant_id", "default")
    logger.info("WS connected: session=%s tenant=%s", session_id, tenant_id)

    redis = await get_redis_pool()
    bus = RedisBus(redis, tenant_id)

    received_events = []

    async def on_bus_event(event):
        """Forward matching events to WebSocket client."""
        if event.session_id == session_id or session_id == "*":
            try:
                await websocket.send_text(event.to_json())
            except Exception:
                pass  # Client disconnected

    # Subscribe to bus
    await bus.subscribe(on_bus_event)

    try:
        while True:
            # Listen for client messages (bidirectional)
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                received_events.append(msg)
                logger.debug("WS received from client: %s", msg.get("type", "?"))
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        logger.info("WS disconnected: session=%s", session_id)
    finally:
        await bus.close()
