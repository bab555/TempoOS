# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""Echo Node — Test node that returns its input."""

from tempo_os.nodes.base import BaseNode, NodeResult
from tempo_os.memory.blackboard import TenantBlackboard
from typing import Any, Dict


class EchoNode(BaseNode):
    node_id = "echo"
    name = "Echo"
    description = "Returns whatever it receives (for testing)"

    async def execute(self, session_id, tenant_id, params, blackboard):
        data = params.get("input", params)
        return NodeResult(
            status="success",
            result={"echo": data},
            ui_schema={"components": [
                {"type": "markdown", "props": {"content": f"Echo: {data}"}}
            ]},
            artifacts={"echo_result": data},
        )
