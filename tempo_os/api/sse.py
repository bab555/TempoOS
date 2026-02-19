# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
SSE (Server-Sent Events) utilities for streaming responses.

Provides helper functions to format SSE event data for the Agent chat endpoint.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional


def sse_event(event: str, data: Any) -> str:
    """
    Format a single SSE event string.

    Args:
        event: Event type (e.g. "message", "ui_render", "thinking", "done").
        data: Payload — will be JSON-serialized if not already a string.

    Returns:
        Formatted SSE string ready to be yielded from a StreamingResponse.
    """
    if isinstance(data, str):
        serialized = data
    else:
        serialized = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {serialized}\n\n"


def sse_message(content: str) -> str:
    """Shortcut: emit a chat text chunk to the left-side conversation bubble."""
    return sse_event("message", {"content": content})


def sse_thinking(content: str) -> str:
    """Shortcut: emit a thinking/status update (loading indicator)."""
    return sse_event("thinking", {"content": content})


def sse_ui_render(component: str, title: str, data: Dict[str, Any],
                  actions: Optional[list] = None) -> str:
    """Shortcut: emit a UI render command for the right-side panel."""
    payload: Dict[str, Any] = {
        "component": component,
        "title": title,
        "data": data,
    }
    if actions:
        payload["actions"] = actions
    return sse_event("ui_render", payload)


def sse_error(message: str) -> str:
    """Shortcut: emit an error event."""
    return sse_event("error", {"message": message})


def sse_done(session_id: str, usage: Optional[Dict[str, int]] = None) -> str:
    """Shortcut: emit the done signal to close the stream."""
    payload: Dict[str, Any] = {"session_id": session_id}
    if usage:
        payload["usage"] = usage
    return sse_event("done", payload)
