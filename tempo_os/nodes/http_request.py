# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""HTTP Request Node — Make HTTP calls to external services."""

import httpx
from tempo_os.nodes.base import BaseNode, NodeResult
from typing import Any, Dict


class HTTPRequestNode(BaseNode):
    node_id = "http_request"
    name = "HTTP Request"
    description = "Send HTTP request to an external URL and return the response"
    param_schema = {
        "url": "Target URL",
        "method": "GET|POST|PUT|DELETE (default: GET)",
        "headers": "Optional headers dict",
        "body": "Optional request body (for POST/PUT)",
        "timeout": "Timeout in seconds (default: 30)",
    }

    async def execute(self, session_id, tenant_id, params, blackboard):
        url = params.get("url", "")
        method = params.get("method", "GET").upper()
        headers = params.get("headers", {})
        body = params.get("body")
        timeout = params.get("timeout", 30)

        if not url:
            return NodeResult(status="error", error_message="URL is required")

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                if method == "GET":
                    resp = await client.get(url, headers=headers)
                elif method == "POST":
                    resp = await client.post(url, headers=headers, json=body)
                elif method == "PUT":
                    resp = await client.put(url, headers=headers, json=body)
                elif method == "DELETE":
                    resp = await client.delete(url, headers=headers)
                else:
                    return NodeResult(status="error", error_message=f"Unsupported method: {method}")

            result = {
                "status_code": resp.status_code,
                "headers": dict(resp.headers),
                "body": resp.text,
            }

            # Try to parse JSON body
            try:
                result["json"] = resp.json()
            except Exception:
                pass

            return NodeResult(
                status="success" if resp.status_code < 400 else "error",
                result=result,
                artifacts={"http_response": result},
            )

        except Exception as e:
            return NodeResult(status="error", error_message=str(e))
