# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Registry API — Node and flow registration/listing. WIRED to real NodeRegistry.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from tempo_os.api.deps import get_current_tenant
from tempo_os.api.errors import APIError, FlowValidationAPIError
from tempo_os.core.tenant import TenantContext
from tempo_os.core.context import get_platform_context
from tempo_os.kernel.flow_loader import load_flow_from_string

router = APIRouter(prefix="/registry", tags=["registry"])


class NodeRegistrationRequest(BaseModel):
    node_id: str
    endpoint: str
    name: str = ""
    description: str = ""
    param_schema: Optional[Dict[str, Any]] = None

class FlowRegistrationRequest(BaseModel):
    flow_id: str
    name: str
    yaml_content: str
    description: str = ""

class NodeInfo(BaseModel):
    node_id: str
    node_type: str
    name: str
    description: str = ""
    endpoint: Optional[str] = None

class FlowInfo(BaseModel):
    flow_id: str
    name: str
    description: str = ""


@router.get("/nodes", response_model=List[NodeInfo])
async def list_nodes(tenant: TenantContext = Depends(get_current_tenant)):
    """List all registered nodes (builtin + webhook)."""
    ctx = get_platform_context()
    nodes = ctx.node_registry.list_all()
    return [NodeInfo(**n) for n in nodes]


@router.post("/nodes", response_model=NodeInfo)
async def register_node(
    req: NodeRegistrationRequest,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Register an external webhook node."""
    ctx = get_platform_context()
    ctx.node_registry.register_webhook(
        node_id=req.node_id,
        endpoint=req.endpoint,
        name=req.name or req.node_id,
        description=req.description,
        param_schema=req.param_schema,
    )
    return NodeInfo(
        node_id=req.node_id,
        node_type="webhook",
        name=req.name or req.node_id,
        endpoint=req.endpoint,
        description=req.description,
    )


@router.get("/flows", response_model=List[FlowInfo])
async def list_flows(tenant: TenantContext = Depends(get_current_tenant)):
    """List all registered flows."""
    ctx = get_platform_context()
    return [FlowInfo(**f) for f in ctx.list_flows()]


@router.post("/flows", response_model=FlowInfo)
async def register_flow(
    req: FlowRegistrationRequest,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Register or update a flow YAML definition."""
    ctx = get_platform_context()
    flow_def = load_flow_from_string(req.yaml_content)
    flow_def.name = req.name
    flow_def.description = req.description

    errors = ctx.register_flow(req.flow_id, flow_def)
    if errors:
        raise FlowValidationAPIError(errors)

    return FlowInfo(
        flow_id=req.flow_id,
        name=req.name,
        description=req.description,
    )


@router.get("/flows/{flow_id}")
async def get_flow(flow_id: str, tenant: TenantContext = Depends(get_current_tenant)):
    """Get flow details."""
    ctx = get_platform_context()
    flow = ctx.get_flow(flow_id)
    if not flow:
        raise APIError(code="FLOW_NOT_FOUND", message=f"Flow '{flow_id}' not found", status_code=404)
    return {
        "flow_id": flow_id,
        "name": flow.name,
        "description": flow.description,
        "states": flow.states,
        "initial_state": flow.initial_state,
        "state_node_map": flow.state_node_map,
        "user_input_states": flow.user_input_states,
        "transitions": flow.transitions,
    }
