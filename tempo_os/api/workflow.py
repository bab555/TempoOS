# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Workflow API — Start, advance, query, and terminate workflow sessions.

NOW WIRED to real SessionManager, FSM, NodeRegistry, and Dispatcher.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from tempo_os.api.deps import get_current_tenant
from tempo_os.api.errors import APIError, SessionNotFoundError, InvalidTransitionAPIError
from tempo_os.core.tenant import TenantContext
from tempo_os.core.context import get_platform_context
from tempo_os.core.metrics import platform_metrics
from tempo_os.memory.fsm import TempoFSM, InvalidTransitionError
from tempo_os.memory.fsm_atomic import AtomicFSM
from tempo_os.kernel.flow_loader import load_flow_from_string
from tempo_os.protocols.events import STEP_DONE, NEED_USER_INPUT, EVENT_ERROR

logger = logging.getLogger("tempo.api.workflow")

router = APIRouter(prefix="/workflow", tags=["workflow"])


# ── Request / Response Models ───────────────────────────────

class StartRequest(BaseModel):
    flow_id: Optional[str] = None
    node_id: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    inherit_session: Optional[str] = None

class StartResponse(BaseModel):
    session_id: str
    state: str
    flow_id: Optional[str] = None
    ui_schema: Optional[Dict[str, Any]] = None

class EventRequest(BaseModel):
    event_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)

class EventResponse(BaseModel):
    new_state: str
    session_state: str
    ui_schema: Optional[Dict[str, Any]] = None
    result: Optional[Dict[str, Any]] = None

class SessionStateResponse(BaseModel):
    session_id: str
    current_state: str
    session_state: str
    flow_id: Optional[str] = None
    valid_events: List[str] = []


# ── Endpoints ───────────────────────────────────────────────

@router.post("/start", response_model=StartResponse)
async def start_workflow(
    req: StartRequest,
    request: Request,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Start a new workflow session (explicit flow or implicit single-node)."""
    ctx = get_platform_context()
    sm = ctx.get_session_manager(tenant.tenant_id)
    trace_id = getattr(request.state, "trace_id", None)

    platform_metrics.inc("sessions_total")

    if req.flow_id:
        # Explicit flow
        flow_def = ctx.get_flow(req.flow_id)
        if not flow_def:
            raise APIError(code="FLOW_NOT_FOUND", message=f"Flow '{req.flow_id}' not found", status_code=404)

        if req.inherit_session:
            session_id = await sm.inherit_session(flow_def, req.inherit_session, params=req.params)
        else:
            session_id = await sm.start_flow(flow_def, params=req.params)

        # Auto-dispatch first step
        initial_state = flow_def.initial_state
        event_type, node_result = await ctx.dispatch_step(
            session_id, tenant.tenant_id, flow_def, initial_state
        )

        # Advance FSM after node execution
        bb = ctx.get_blackboard(tenant.tenant_id)
        fsm = TempoFSM(flow_def.to_fsm_config(), blackboard=bb)

        if event_type == STEP_DONE:
            new_state = await fsm.advance(session_id, STEP_DONE)
            await bb.set_state(session_id, "_current_state", new_state)
            session_state = "waiting_user" if flow_def.is_user_input_state(new_state) else "running"
            await bb.set_state(session_id, "_session_state", session_state)
        else:
            new_state = initial_state
            session_state = "running"

        return StartResponse(
            session_id=session_id,
            state=new_state,
            flow_id=req.flow_id,
            ui_schema=node_result.ui_schema if node_result else None,
        )

    elif req.node_id:
        # Implicit single-node session
        session_id = await sm.start_single_node(req.node_id, params=req.params)

        # Execute the node immediately
        node_ref = f"builtin://{req.node_id}"
        node_result = await ctx.execute_node(node_ref, session_id, tenant.tenant_id, req.params)

        bb = ctx.get_blackboard(tenant.tenant_id)
        await bb.set_state(session_id, "_session_state", "completed" if node_result.is_success else "error")

        return StartResponse(
            session_id=session_id,
            state="done" if node_result.is_success else "error",
            ui_schema=node_result.ui_schema if node_result else None,
        )

    else:
        raise APIError(code="INVALID_REQUEST", message="Either flow_id or node_id must be provided")


@router.post("/{session_id}/event", response_model=EventResponse)
async def push_event(
    session_id: str,
    req: EventRequest,
    request: Request,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Push an event (USER_CONFIRM, etc.) to advance the workflow."""
    ctx = get_platform_context()
    bb = ctx.get_blackboard(tenant.tenant_id)

    platform_metrics.inc("events_processed")

    # Get the flow definition
    flow_id = await bb.get_state(session_id, "_flow_id")
    if not flow_id:
        raise SessionNotFoundError(session_id)

    flow_def = ctx.get_flow(flow_id)
    if not flow_def:
        raise SessionNotFoundError(session_id)

    # Get current state and advance FSM
    fsm = TempoFSM(flow_def.to_fsm_config(), blackboard=bb)

    try:
        new_state = await fsm.advance(session_id, req.event_type)
    except InvalidTransitionError as e:
        raise InvalidTransitionAPIError(str(e))

    await bb.set_state(session_id, "_current_state", new_state)

    # Store user payload in Blackboard
    if req.payload:
        await bb.set_state(session_id, "_user_payload", req.payload)

    # Check if new state has a node to execute
    node_result = None
    if flow_def.get_node_ref(new_state):
        event_type, node_result = await ctx.dispatch_step(
            session_id, tenant.tenant_id, flow_def, new_state
        )
        # Auto-advance after node execution
        if event_type == STEP_DONE:
            try:
                new_state = await fsm.advance(session_id, STEP_DONE)
                await bb.set_state(session_id, "_current_state", new_state)
            except InvalidTransitionError:
                pass

    # Determine session state
    if new_state in (flow_def.states[-1] if flow_def.states else []):
        session_state = "completed"
    elif flow_def.is_user_input_state(new_state):
        session_state = "waiting_user"
    else:
        session_state = "running"

    await bb.set_state(session_id, "_session_state", session_state)

    return EventResponse(
        new_state=new_state,
        session_state=session_state,
        ui_schema=node_result.ui_schema if node_result else None,
        result=node_result.result if node_result else None,
    )


@router.get("/{session_id}/state", response_model=SessionStateResponse)
async def get_state(
    session_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Get current session state and valid events."""
    ctx = get_platform_context()
    bb = ctx.get_blackboard(tenant.tenant_id)

    current_state = await bb.get_state(session_id, "_current_state")
    session_state = await bb.get_state(session_id, "_session_state")
    flow_id = await bb.get_state(session_id, "_flow_id")

    if not session_state:
        raise SessionNotFoundError(session_id)

    # Compute valid events
    valid_events = []
    if flow_id:
        flow_def = ctx.get_flow(flow_id)
        if flow_def:
            fsm = TempoFSM(flow_def.to_fsm_config())
            valid_events = fsm.get_valid_events(current_state or flow_def.initial_state)

    return SessionStateResponse(
        session_id=session_id,
        current_state=current_state or "unknown",
        session_state=session_state,
        flow_id=flow_id,
        valid_events=valid_events,
    )


@router.delete("/{session_id}")
async def terminate_session(
    session_id: str,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Terminate (abort) a workflow session."""
    ctx = get_platform_context()
    stopper = ctx.get_stopper(tenant.tenant_id)
    await stopper.abort(session_id, "User requested termination")
    platform_metrics.inc("sessions_aborted")
    return {"status": "terminated", "session_id": session_id}


@router.post("/{session_id}/callback")
async def webhook_callback(
    session_id: str,
    body: Dict[str, Any],
):
    """External webhook callback endpoint."""
    # Placeholder — will integrate with WebhookCaller
    return {"status": "received", "session_id": session_id}
