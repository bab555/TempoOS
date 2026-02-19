# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Platform Context — Singleton that holds all core component references.

Initialized at startup, injected into API routes via FastAPI Depends.
"""

from __future__ import annotations

from typing import Optional

import redis.asyncio as aioredis

from tempo_os.kernel.bus import RedisBus
from tempo_os.kernel.node_registry import NodeRegistry
from tempo_os.kernel.session_manager import SessionManager
from tempo_os.memory.blackboard import TenantBlackboard
from tempo_os.memory.fsm import TempoFSM
from tempo_os.memory.fsm_atomic import AtomicFSM
from tempo_os.kernel.flow_loader import FlowDefinition, load_flow_from_string, validate_flow
from tempo_os.resilience.idempotency import IdempotencyGuard
from tempo_os.resilience.stopper import HardStopper
from tempo_os.resilience.retry import RetryManager
from tempo_os.core.metrics import platform_metrics
from tempo_os.protocols.schema import TempoEvent
from tempo_os.protocols.events import STEP_DONE, NEED_USER_INPUT, EVENT_ERROR
from tempo_os.nodes.base import BaseNode, NodeResult


class PlatformContext:
    """
    Holds all runtime references for the platform.
    Created once at startup, used by all API handlers.
    """

    def __init__(self, redis: aioredis.Redis) -> None:
        self.redis = redis
        self.node_registry = NodeRegistry()
        self.idempotency = IdempotencyGuard()
        self.retry_manager = RetryManager()
        self._flows: dict[str, FlowDefinition] = {}

    def get_session_manager(self, tenant_id: str) -> SessionManager:
        """Create a tenant-scoped SessionManager."""
        return SessionManager(self.redis, tenant_id)

    def get_blackboard(self, tenant_id: str) -> TenantBlackboard:
        """Create a tenant-scoped Blackboard."""
        return TenantBlackboard(self.redis, tenant_id)

    def get_bus(self, tenant_id: str) -> RedisBus:
        """Create a tenant-scoped Bus."""
        return RedisBus(self.redis, tenant_id)

    def get_stopper(self, tenant_id: str) -> HardStopper:
        """Create a tenant-scoped HardStopper."""
        bb = self.get_blackboard(tenant_id)
        bus = self.get_bus(tenant_id)
        return HardStopper(self.redis, bus, bb)

    # ── Flow Management ─────────────────────────────────────────

    def register_flow(self, flow_id: str, flow_def: FlowDefinition) -> list[str]:
        """Register a flow definition. Returns validation errors (empty = ok)."""
        errors = validate_flow(flow_def, self.node_registry.list_builtin_ids())
        if not errors:
            self._flows[flow_id] = flow_def
        return errors

    def get_flow(self, flow_id: str) -> Optional[FlowDefinition]:
        return self._flows.get(flow_id)

    def list_flows(self) -> list[dict]:
        return [
            {"flow_id": fid, "name": f.name, "description": f.description}
            for fid, f in self._flows.items()
        ]

    # ── Node Execution ──────────────────────────────────────────

    async def execute_node(
        self,
        node_ref: str,
        session_id: str,
        tenant_id: str,
        params: dict,
    ) -> NodeResult:
        """
        Execute a node (builtin or webhook) and return the result.
        Includes idempotency check and metrics tracking.
        """
        import time

        node_or_wh = self.node_registry.resolve_ref(node_ref)
        if node_or_wh is None:
            return NodeResult(status="error", error_message=f"Node not found: {node_ref}")

        if isinstance(node_or_wh, BaseNode):
            # Builtin execution
            node_id = node_or_wh.node_id
            platform_metrics.inc(f"node_exec:{node_id}")

            bb = self.get_blackboard(tenant_id)
            start = time.time()

            try:
                result = await node_or_wh.execute(session_id, tenant_id, params, bb)
                elapsed = (time.time() - start) * 1000
                platform_metrics.observe(f"node_latency:{node_id}", elapsed)

                # Write artifacts to Blackboard
                for key, value in result.artifacts.items():
                    if isinstance(value, dict):
                        await bb.push_artifact(session_id, key, value)
                    else:
                        await bb.push_artifact(session_id, key, {"value": value})

                return result

            except Exception as e:
                platform_metrics.inc(f"node_error:{node_id}")
                return NodeResult(status="error", error_message=str(e))
        else:
            # Webhook — placeholder, will use WebhookCaller
            return NodeResult(
                status="success",
                result={"webhook": "pending", "endpoint": node_or_wh.endpoint},
            )

    # ── Full Dispatch Cycle ─────────────────────────────────────

    async def dispatch_step(
        self,
        session_id: str,
        tenant_id: str,
        flow_def: FlowDefinition,
        current_state: str,
    ) -> tuple[str, Optional[NodeResult]]:
        """
        Execute the node mapped to current_state and advance FSM.

        Returns (event_type_to_emit, node_result).
        """
        node_ref = flow_def.get_node_ref(current_state)
        if not node_ref:
            # No node for this state (e.g. user_input_state) — just wait
            return "WAITING", None

        bb = self.get_blackboard(tenant_id)
        params = await bb.get_state(session_id, "_params") or {}

        result = await self.execute_node(node_ref, session_id, tenant_id, params)

        if result.is_success:
            return STEP_DONE, result
        elif result.needs_user_input:
            return NEED_USER_INPUT, result
        else:
            return EVENT_ERROR, result


# ── Global singleton ────────────────────────────────────────

_ctx: Optional[PlatformContext] = None


def init_platform_context(redis: aioredis.Redis) -> PlatformContext:
    global _ctx
    _ctx = PlatformContext(redis)
    return _ctx


def get_platform_context() -> PlatformContext:
    if _ctx is None:
        raise RuntimeError("PlatformContext not initialized. Call init_platform_context() first.")
    return _ctx
