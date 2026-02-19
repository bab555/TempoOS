# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""Conditional Node — Branch based on Blackboard data."""

from tempo_os.nodes.base import BaseNode, NodeResult
from typing import Any, Dict


class ConditionalNode(BaseNode):
    node_id = "conditional"
    name = "Conditional Branch"
    description = "Evaluates a condition from Blackboard and returns different events"
    param_schema = {
        "key": "Blackboard key to check",
        "operator": "eq|ne|gt|lt|exists",
        "value": "Value to compare against",
        "true_event": "Event to emit if condition is true",
        "false_event": "Event to emit if condition is false",
    }

    async def execute(self, session_id, tenant_id, params, blackboard):
        key = params.get("key", "")
        operator = params.get("operator", "exists")
        expected = params.get("value")
        true_event = params.get("true_event", "CONDITION_TRUE")
        false_event = params.get("false_event", "CONDITION_FALSE")

        actual = await blackboard.get_state(session_id, key)

        condition_met = False
        if operator == "exists":
            condition_met = actual is not None
        elif operator == "eq":
            condition_met = actual == expected
        elif operator == "ne":
            condition_met = actual != expected
        elif operator == "gt":
            condition_met = actual is not None and actual > expected
        elif operator == "lt":
            condition_met = actual is not None and actual < expected

        chosen_event = true_event if condition_met else false_event

        return NodeResult(
            status="success",
            result={"condition_met": condition_met, "chosen_event": chosen_event},
            next_events=[chosen_event],
        )
