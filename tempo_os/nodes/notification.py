# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""Notification Node — Send notification events via Bus (for WS push)."""

from tempo_os.nodes.base import BaseNode, NodeResult
from typing import Any, Dict


class NotificationNode(BaseNode):
    node_id = "notification"
    name = "Notification"
    description = "Send a notification to the frontend via WebSocket"

    async def execute(self, session_id, tenant_id, params, blackboard):
        message = params.get("message", "")
        level = params.get("level", "info")  # info|warning|error|success

        return NodeResult(
            status="success",
            result={"notified": True},
            ui_schema={"components": [
                {
                    "type": "chat_message",
                    "props": {
                        "role": "system",
                        "content": message,
                        "level": level,
                    },
                }
            ]},
        )
