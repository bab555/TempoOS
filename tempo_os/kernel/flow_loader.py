# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Flow Loader — Load and validate YAML workflow definitions.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import yaml

logger = logging.getLogger("tempo.flow_loader")


class FlowValidationError(Exception):
    """Raised when a YAML flow definition is invalid."""
    pass


class FlowDefinition:
    """Parsed and validated workflow definition."""

    def __init__(self, config: Dict[str, Any]) -> None:
        self.name: str = config.get("name", "unnamed")
        self.description: str = config.get("description", "")
        self.states: List[str] = config.get("states", [])
        self.initial_state: str = config.get("initial_state", self.states[0] if self.states else "idle")
        self.transitions: List[Dict[str, str]] = config.get("transitions", [])
        self.state_node_map: Dict[str, str] = config.get("state_node_map", {})
        self.user_input_states: List[str] = config.get("user_input_states", [])
        self.raw_config = config

    def get_node_ref(self, state: str) -> Optional[str]:
        """Get the node reference (builtin:// or http://) for a state."""
        return self.state_node_map.get(state)

    def is_user_input_state(self, state: str) -> bool:
        """Check if a state requires user input before proceeding."""
        return state in self.user_input_states

    def to_fsm_config(self) -> Dict[str, Any]:
        """Convert to TempoFSM-compatible config dict."""
        return {
            "states": self.states,
            "initial_state": self.initial_state,
            "transitions": self.transitions,
        }


def load_flow_from_yaml(path: str | Path) -> FlowDefinition:
    """Load a flow definition from a YAML file."""
    with open(path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return FlowDefinition(config)


def load_flow_from_string(yaml_content: str) -> FlowDefinition:
    """Load a flow definition from a YAML string."""
    config = yaml.safe_load(yaml_content)
    return FlowDefinition(config)


def validate_flow(
    flow: FlowDefinition,
    registered_nodes: Optional[Set[str]] = None,
) -> List[str]:
    """
    Validate a flow definition. Returns list of error messages (empty = valid).

    Checks:
      1. Must have at least 2 states
      2. initial_state must be in states
      3. All transition from/to must reference valid states
      4. All state_node_map values must be valid node references
      5. All user_input_states must be in states
      6. If registered_nodes provided, all builtin:// refs must exist
    """
    errors = []

    if len(flow.states) < 2:
        errors.append("Flow must have at least 2 states")

    if flow.initial_state not in flow.states:
        errors.append(f"initial_state '{flow.initial_state}' not in states")

    state_set = set(flow.states)

    for t in flow.transitions:
        if t.get("from") not in state_set:
            errors.append(f"Transition from unknown state: '{t.get('from')}'")
        if t.get("to") not in state_set:
            errors.append(f"Transition to unknown state: '{t.get('to')}'")

    for state, node_ref in flow.state_node_map.items():
        if state not in state_set:
            errors.append(f"state_node_map references unknown state: '{state}'")
        if not (node_ref.startswith("builtin://") or node_ref.startswith("http://") or node_ref.startswith("https://")):
            errors.append(f"Invalid node_ref '{node_ref}' for state '{state}'. Must start with builtin:// or http(s)://")

        # Check if builtin node is registered
        if registered_nodes and node_ref.startswith("builtin://"):
            node_id = node_ref.replace("builtin://", "")
            if node_id not in registered_nodes:
                errors.append(f"Node '{node_id}' not registered (referenced by state '{state}')")

    for state in flow.user_input_states:
        if state not in state_set:
            errors.append(f"user_input_states references unknown state: '{state}'")

    return errors
