# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Node Registry — Unified registry for built-in nodes and external webhooks.

Resolves `builtin://xxx` → BaseNode instance
        `http://xxx`     → WebhookInfo
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

from tempo_os.nodes.base import BaseNode

logger = logging.getLogger("tempo.node_registry")


@dataclass
class WebhookInfo:
    """Metadata for an external webhook node."""
    node_id: str
    name: str
    endpoint: str
    description: str = ""
    param_schema: Dict[str, Any] = None


class NodeRegistry:
    """
    Unified management of built-in nodes and external webhook nodes.
    """

    def __init__(self) -> None:
        self._builtin: Dict[str, BaseNode] = {}
        self._webhooks: Dict[str, WebhookInfo] = {}

    # ── Registration ────────────────────────────────────────────

    def register_builtin(self, node_id: str, node: BaseNode) -> None:
        """Register an in-process built-in node."""
        self._builtin[node_id] = node
        logger.info("Registered builtin node: %s (%s)", node_id, node.name)

    def register_webhook(
        self,
        node_id: str,
        endpoint: str,
        name: str = "",
        description: str = "",
        param_schema: Optional[Dict] = None,
    ) -> None:
        """Register an external webhook node."""
        self._webhooks[node_id] = WebhookInfo(
            node_id=node_id,
            name=name or node_id,
            endpoint=endpoint,
            description=description,
            param_schema=param_schema,
        )
        logger.info("Registered webhook node: %s -> %s", node_id, endpoint)

    # ── Lookup ──────────────────────────────────────────────────

    def get(self, node_id: str) -> Optional[Union[BaseNode, WebhookInfo]]:
        """Get a node by ID (builtin or webhook)."""
        if node_id in self._builtin:
            return self._builtin[node_id]
        if node_id in self._webhooks:
            return self._webhooks[node_id]
        return None

    def resolve_ref(self, node_ref: str) -> Optional[Union[BaseNode, WebhookInfo]]:
        """
        Resolve a node reference string.

        'builtin://echo' → BaseNode instance
        'http://example.com/webhook' → WebhookInfo
        """
        if node_ref.startswith("builtin://"):
            node_id = node_ref.replace("builtin://", "")
            return self._builtin.get(node_id)
        elif node_ref.startswith("http://") or node_ref.startswith("https://"):
            # Find webhook by endpoint match
            for wh in self._webhooks.values():
                if wh.endpoint == node_ref:
                    return wh
            # Or create an ad-hoc WebhookInfo
            return WebhookInfo(node_id="adhoc", name="adhoc", endpoint=node_ref)
        return None

    def is_builtin(self, node_ref: str) -> bool:
        """Check if a node_ref is a builtin node."""
        return node_ref.startswith("builtin://")

    # ── Listing ─────────────────────────────────────────────────

    def list_all(self) -> List[Dict[str, Any]]:
        """List all registered nodes (builtin + webhook)."""
        result = []
        for nid, node in self._builtin.items():
            result.append({
                "node_id": nid,
                "node_type": "builtin",
                "name": node.name,
                "description": node.description,
            })
        for nid, wh in self._webhooks.items():
            result.append({
                "node_id": nid,
                "node_type": "webhook",
                "name": wh.name,
                "endpoint": wh.endpoint,
                "description": wh.description,
            })
        return result

    def list_builtin_ids(self) -> set[str]:
        """Return set of all registered builtin node IDs."""
        return set(self._builtin.keys())

    def __len__(self) -> int:
        return len(self._builtin) + len(self._webhooks)

    def __contains__(self, node_id: str) -> bool:
        return node_id in self._builtin or node_id in self._webhooks
