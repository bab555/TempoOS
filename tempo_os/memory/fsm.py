# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Generic FSM Engine — The Brain.

A config-driven finite state machine that reads transition rules
from YAML/dict configuration. Business developers define their own
state flows; the engine is completely generic.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from tempo_os.memory.blackboard import TenantBlackboard

logger = logging.getLogger("tempo.fsm")

FSM_STATE_KEY = "_fsm_state"


class InvalidTransitionError(Exception):
    """Raised when an FSM transition is not permitted."""
    pass


class TempoFSM:
    """
    Generic finite state machine driven by configuration.

    Transition rules are loaded from a dict or YAML file:
        states: [idle, step_1, step_2, done]
        transitions:
          - from: idle
            event: CMD_START
            to: step_1
          - from: step_1
            event: WORKER_RESULT
            to: step_2
    """

    def __init__(
        self,
        config: Dict[str, Any],
        blackboard: Optional[TenantBlackboard] = None,
    ) -> None:
        self._states: List[str] = config.get("states", [])
        self._initial_state: str = config.get("initial_state", self._states[0] if self._states else "idle")
        self._transitions: List[Dict[str, str]] = config.get("transitions", [])
        self._blackboard = blackboard

        # Build lookup: (from_state, event_type) -> to_state
        self._lookup: Dict[tuple, str] = {}
        for t in self._transitions:
            key = (t["from"], t["event"])
            self._lookup[key] = t["to"]

    @classmethod
    def from_yaml(
        cls,
        path: str | Path,
        blackboard: Optional[TenantBlackboard] = None,
    ) -> TempoFSM:
        """Load FSM config from a YAML file."""
        with open(path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        return cls(config, blackboard)

    @property
    def states(self) -> List[str]:
        return list(self._states)

    @property
    def initial_state(self) -> str:
        return self._initial_state

    # ── Core Transition Logic ───────────────────────────────────

    def transition(self, current_state: str, event_type: str) -> str:
        """
        Compute the next state given current state and event type.

        Raises InvalidTransitionError if no matching rule exists.
        """
        key = (current_state, event_type)
        if key not in self._lookup:
            raise InvalidTransitionError(
                f"No transition from state '{current_state}' "
                f"on event '{event_type}'"
            )
        next_state = self._lookup[key]
        logger.info(
            "FSM transition: %s -[%s]-> %s",
            current_state, event_type, next_state,
        )
        return next_state

    def get_valid_events(self, current_state: str) -> List[str]:
        """Return all event types valid from the given state."""
        return [
            event for (state, event) in self._lookup.keys()
            if state == current_state
        ]

    # ── Blackboard Integration ──────────────────────────────────

    async def get_current_state(self, session_id: str) -> str:
        """Read current FSM state from Blackboard."""
        if not self._blackboard:
            raise RuntimeError("FSM has no blackboard attached")
        state = await self._blackboard.get_state(session_id, FSM_STATE_KEY)
        return state if state else self._initial_state

    async def set_state(self, session_id: str, new_state: str) -> None:
        """Write FSM state to Blackboard."""
        if not self._blackboard:
            raise RuntimeError("FSM has no blackboard attached")
        if new_state not in self._states:
            raise ValueError(f"Unknown state: '{new_state}'")
        await self._blackboard.set_state(session_id, FSM_STATE_KEY, new_state)

    async def advance(self, session_id: str, event_type: str) -> str:
        """
        Read current state, compute transition, write new state.

        Returns the new state.
        """
        current = await self.get_current_state(session_id)
        new_state = self.transition(current, event_type)
        await self.set_state(session_id, new_state)
        return new_state
