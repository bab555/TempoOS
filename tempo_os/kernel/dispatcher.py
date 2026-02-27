# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Kernel Dispatcher — Connects FSM to Event Bus.

Subscribes to worker results and drives state transitions.
The core "Pulse" loop: Event → Bus → FSM → Dispatch → Bus → Worker/Node.
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, Optional, Awaitable

from tempo_os.kernel.bus import RedisBus
from tempo_os.memory.blackboard import TenantBlackboard
from tempo_os.memory.fsm import TempoFSM
from tempo_os.protocols.schema import TempoEvent
from tempo_os.protocols.events import (
    EVENT_RESULT,
    EVENT_ERROR,
    CMD_EXECUTE,
    STATE_TRANSITION,
)

logger = logging.getLogger("tempo.dispatcher")

# Type alias for action handlers
ActionHandler = Callable[[str, str, TempoEvent], Awaitable[Optional[TempoEvent]]]


class KernelDispatcher:
    """
    Connects the FSM to the Event Bus.

    On receiving worker results:
      1. Load session state from Blackboard.
      2. Run FSM transition.
      3. Determine and dispatch next action.
    """

    def __init__(
        self,
        bus: RedisBus,
        blackboard: TenantBlackboard,
        fsm: TempoFSM,
    ) -> None:
        self._bus = bus
        self._blackboard = blackboard
        self._fsm = fsm
        self._action_map: Dict[str, ActionHandler] = {}

    def register_action(self, state: str, handler: ActionHandler) -> None:
        """
        Register an action handler for a given FSM state.

        When the FSM transitions INTO this state, the handler is called.
        The handler may return a TempoEvent to publish, or None.
        """
        self._action_map[state] = handler

    async def start(self) -> None:
        """Subscribe to result and error events."""
        await self._bus.subscribe(self._on_event, event_filter=EVENT_RESULT)
        # Also listen for errors to transition FSM
        await self._bus.subscribe(self._on_event, event_filter=EVENT_ERROR)
        logger.info("KernelDispatcher started")

    async def _on_event(self, event: TempoEvent) -> None:
        """Handle incoming worker result or error events."""
        session_id = event.session_id
        logger.debug(
            "Dispatcher received %s from %s (session=%s)",
            event.type, event.source, session_id,
        )

        try:
            # Advance FSM
            new_state = await self._fsm.advance(session_id, event.type)

            # Emit state transition event
            transition_event = TempoEvent.create(
                type=STATE_TRANSITION,
                source="kernel.dispatcher",
                tenant_id=event.tenant_id,
                session_id=session_id,
                tick=event.tick,
                payload={
                    "new_state": new_state,
                    "triggered_by": event.type,
                    "source_event_id": event.id,
                },
            )
            await self._bus.publish(transition_event)

            # Execute action for new state (if registered)
            if new_state in self._action_map:
                handler = self._action_map[new_state]
                action_event = await handler(new_state, session_id, event)
                if action_event:
                    await self._bus.publish(action_event)

        except Exception as exc:
            logger.error(
                "Dispatcher error for session %s: %s",
                session_id, exc,
            )

    async def dispatch_command(
        self,
        target: str,
        tenant_id: str,
        session_id: str,
        tick: int,
        payload: dict,
    ) -> None:
        """
        Manually dispatch a CMD_EXECUTE to a target worker.

        Also advances the FSM if a valid transition exists for CMD_EXECUTE.
        """
        # Advance FSM on command dispatch (idle -> working)
        try:
            await self._fsm.advance(session_id, CMD_EXECUTE)
        except Exception as exc:
            logger.warning("FSM advance on dispatch: %s", exc)

        event = TempoEvent.create(
            type=CMD_EXECUTE,
            source="kernel.dispatcher",
            target=target,
            tenant_id=tenant_id,
            session_id=session_id,
            tick=tick,
            payload=payload,
        )
        await self._bus.publish(event)
