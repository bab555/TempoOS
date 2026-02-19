# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.
"""Unit tests for flow_loader."""

import pytest
from tempo_os.kernel.flow_loader import (
    FlowDefinition, load_flow_from_string, validate_flow,
)

VALID_YAML = """
name: test_flow
description: "A test flow"
states: [start, middle, end]
initial_state: start
state_node_map:
  start: builtin://echo
  middle: builtin://echo
transitions:
  - { from: start, event: STEP_DONE, to: middle }
  - { from: middle, event: USER_CONFIRM, to: end }
user_input_states: [middle]
"""


class TestFlowLoader:
    def test_load_valid_yaml(self):
        flow = load_flow_from_string(VALID_YAML)
        assert flow.name == "test_flow"
        assert flow.states == ["start", "middle", "end"]
        assert flow.initial_state == "start"

    def test_state_node_map(self):
        flow = load_flow_from_string(VALID_YAML)
        assert flow.get_node_ref("start") == "builtin://echo"
        assert flow.get_node_ref("end") is None

    def test_user_input_states(self):
        flow = load_flow_from_string(VALID_YAML)
        assert flow.is_user_input_state("middle") is True
        assert flow.is_user_input_state("start") is False

    def test_to_fsm_config(self):
        flow = load_flow_from_string(VALID_YAML)
        config = flow.to_fsm_config()
        assert config["states"] == ["start", "middle", "end"]
        assert config["initial_state"] == "start"
        assert len(config["transitions"]) == 2

    def test_validate_valid_flow(self):
        flow = load_flow_from_string(VALID_YAML)
        errors = validate_flow(flow, registered_nodes={"echo"})
        assert errors == []

    def test_validate_unknown_state_in_transition(self):
        bad_yaml = """
name: bad
states: [a, b]
initial_state: a
transitions:
  - { from: a, event: GO, to: c }
"""
        flow = load_flow_from_string(bad_yaml)
        errors = validate_flow(flow)
        assert any("unknown state" in e for e in errors)

    def test_validate_bad_node_ref(self):
        bad_yaml = """
name: bad
states: [a, b]
initial_state: a
state_node_map:
  a: ftp://something
transitions:
  - { from: a, event: GO, to: b }
"""
        flow = load_flow_from_string(bad_yaml)
        errors = validate_flow(flow)
        assert any("Invalid node_ref" in e for e in errors)

    def test_validate_unregistered_node(self):
        flow = load_flow_from_string(VALID_YAML)
        errors = validate_flow(flow, registered_nodes={"other_node"})
        assert any("not registered" in e for e in errors)

    def test_validate_min_states(self):
        bad_yaml = """
name: tiny
states: [only_one]
initial_state: only_one
transitions: []
"""
        flow = load_flow_from_string(bad_yaml)
        errors = validate_flow(flow)
        assert any("at least 2" in e for e in errors)
