# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Webhook Caller — HTTP client for external webhook nodes.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger("tempo.webhook")


class WebhookCaller:
    """Sends execution requests to external webhook endpoints."""

    def __init__(self, timeout: int = 30):
        self._timeout = timeout

    async def call(
        self,
        endpoint: str,
        session_id: str,
        step: str,
        params: Dict[str, Any],
        callback_url: str,
        tenant_id: str = "",
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Send POST to external webhook with execution context.

        The external service should process the request and POST back
        to callback_url when done.
        """
        payload = {
            "session_id": session_id,
            "step": step,
            "params": params,
            "callback_url": callback_url,
            "tenant_id": tenant_id,
            "trace_id": trace_id,
        }

        logger.info("Webhook call: %s (session=%s, step=%s)", endpoint, session_id, step)

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(endpoint, json=payload)
                return {
                    "status_code": resp.status_code,
                    "accepted": resp.status_code < 400,
                    "body": resp.text,
                }
        except Exception as e:
            logger.error("Webhook call failed: %s — %s", endpoint, e)
            return {
                "status_code": 0,
                "accepted": False,
                "error": str(e),
            }

    async def handle_callback(
        self,
        session_id: str,
        step: str,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Process a callback from an external webhook.

        Returns normalized result that the Dispatcher can use.
        """
        return {
            "session_id": session_id,
            "step": step,
            "status": result.get("status", "success"),
            "result": result.get("result", {}),
            "ui_schema": result.get("ui_schema"),
            "artifacts": result.get("artifacts", {}),
        }
