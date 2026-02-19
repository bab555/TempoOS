# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Session Manager — Workflow session lifecycle management.

Handles:
  - Starting explicit flows (YAML-defined multi-step)
  - Starting implicit sessions (single-node, auto-created FSM)
  - Session inheritance (carry over Blackboard from previous session)
  - Pushing user events to advance flows
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

from tempo_os.memory.blackboard import TenantBlackboard
from tempo_os.memory.fsm import TempoFSM
from tempo_os.memory.fsm_atomic import AtomicFSM
from tempo_os.kernel.bus import RedisBus
from tempo_os.kernel.flow_loader import FlowDefinition, load_flow_from_string
from tempo_os.protocols.schema import TempoEvent
from tempo_os.protocols.events import (
    SESSION_START, STEP_DONE, SESSION_COMPLETE,
    CMD_EXECUTE, USER_CONFIRM, USER_SKIP, USER_MODIFY,
)

import redis.asyncio as aioredis

logger = logging.getLogger("tempo.session_manager")


# Minimal FSM for single-node implicit sessions
IMPLICIT_SESSION_CONFIG = {
    "states": ["execute", "done"],
    "initial_state": "execute",
    "transitions": [
        {"from": "execute", "event": "STEP_DONE", "to": "done"},
    ],
}


class SessionManager:
    """
    Manages the lifecycle of workflow sessions.

    All sessions go through the workflow engine — even single-step operations
    create an "implicit session" with a minimal FSM.
    """

    def __init__(
        self,
        redis: aioredis.Redis,
        tenant_id: str,
    ) -> None:
        self._redis = redis
        self._tenant_id = tenant_id
        self._blackboard = TenantBlackboard(redis, tenant_id)
        self._bus = RedisBus(redis, tenant_id)

    @property
    def blackboard(self) -> TenantBlackboard:
        return self._blackboard

    @property
    def bus(self) -> RedisBus:
        return self._bus

    # ── Start Flow (Explicit) ───────────────────────────────────

    async def start_flow(
        self,
        flow_def: FlowDefinition,
        params: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Start an explicit multi-step flow.

        Creates a session, initializes FSM, stores params in Blackboard.
        Returns session_id.
        """
        session_id = str(uuid.uuid4())

        # Store session metadata
        await self._blackboard.set_state(session_id, "_flow_id", flow_def.name)
        await self._blackboard.set_state(session_id, "_session_state", "running")
        if params:
            await self._blackboard.set_state(session_id, "_params", params)

        # Initialize FSM at initial state (AtomicFSM will read initial_state)
        fsm = TempoFSM(flow_def.to_fsm_config(), blackboard=self._blackboard)
        # State is implicitly initial_state until first advance

        # Publish session start event
        await self._bus.publish(TempoEvent.create(
            type=SESSION_START,
            source="session_manager",
            tenant_id=self._tenant_id,
            session_id=session_id,
            payload={
                "flow_id": flow_def.name,
                "initial_state": flow_def.initial_state,
                "params": params or {},
            },
        ))

        logger.info(
            "Started flow '%s' → session %s (initial=%s)",
            flow_def.name, session_id, flow_def.initial_state,
        )
        return session_id

    # ── Start Single Node (Implicit Session) ────────────────────

    async def start_single_node(
        self,
        node_id: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Start an implicit session for a single-node execution.

        Creates a minimal FSM: [execute] → STEP_DONE → [done]
        The session stays alive (TTL-based) so subsequent operations
        can inherit its Blackboard.
        """
        session_id = str(uuid.uuid4())

        await self._blackboard.set_state(session_id, "_node_id", node_id)
        await self._blackboard.set_state(session_id, "_session_state", "running")
        await self._blackboard.set_state(session_id, "_implicit", True)
        if params:
            await self._blackboard.set_state(session_id, "_params", params)

        await self._bus.publish(TempoEvent.create(
            type=SESSION_START,
            source="session_manager",
            tenant_id=self._tenant_id,
            session_id=session_id,
            payload={
                "node_id": node_id,
                "implicit": True,
                "params": params or {},
            },
        ))

        logger.info("Started implicit session %s for node '%s'", session_id, node_id)
        return session_id

    # ── Inherit Session ─────────────────────────────────────────

    async def inherit_session(
        self,
        flow_def: FlowDefinition,
        from_session_id: str,
        from_step: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Start a new flow that inherits Blackboard data from a previous session.

        This enables the "implicit → explicit" upgrade pattern:
        user does a quick single-step query, then wants to continue
        into a full procurement flow without losing context.
        """
        new_session_id = await self.start_flow(flow_def, params)

        # Copy artifacts from previous session
        artifacts = await self._blackboard.list_session_artifacts(from_session_id)
        for art_id in artifacts:
            data = await self._blackboard.get_artifact(art_id)
            if data:
                await self._blackboard.push_artifact(new_session_id, art_id, data)

        logger.info(
            "Session %s inherits %d artifacts from %s",
            new_session_id, len(artifacts), from_session_id,
        )
        return new_session_id

    # ── Push Event ──────────────────────────────────────────────

    async def push_event(
        self,
        session_id: str,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Push a user event (USER_CONFIRM, USER_SKIP, etc.) to advance the flow.
        """
        await self._bus.publish(TempoEvent.create(
            type=event_type,
            source="user",
            tenant_id=self._tenant_id,
            session_id=session_id,
            payload=payload or {},
        ))
        logger.info("Pushed %s to session %s", event_type, session_id)

    # ── Session State Queries ───────────────────────────────────

    async def get_session_state(self, session_id: str) -> Dict[str, Any]:
        """Get the full Blackboard state for a session."""
        return await self._blackboard.get_state(session_id)

    async def get_session_status(self, session_id: str) -> str:
        """Get the session lifecycle status (running/waiting/completed/error)."""
        status = await self._blackboard.get_state(session_id, "_session_state")
        return status or "unknown"
