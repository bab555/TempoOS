# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Repository Layer — CRUD operations for all platform tables.

Each repository takes an AsyncSession and provides typed access.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from tempo_os.storage.models import (
    WorkflowSession,
    WorkflowFlow,
    WorkflowEvent,
    IdempotencyLog,
    RegistryNode,
)
from tempo_os.protocols.schema import TempoEvent


# ── Session Repository ──────────────────────────────────────

class SessionRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        tenant_id: str,
        flow_id: Optional[str] = None,
        params: Optional[Dict] = None,
        ttl_seconds: int = 1800,
    ) -> uuid.UUID:
        """Create a new workflow session. Returns session_id."""
        session = WorkflowSession(
            tenant_id=tenant_id,
            flow_id=flow_id,
            params=params or {},
            ttl_seconds=ttl_seconds,
        )
        self.db.add(session)
        await self.db.flush()
        return session.session_id

    async def get(self, session_id: uuid.UUID) -> Optional[WorkflowSession]:
        """Get session by ID."""
        result = await self.db.execute(
            select(WorkflowSession).where(WorkflowSession.session_id == session_id)
        )
        return result.scalar_one_or_none()

    async def update_state(
        self,
        session_id: uuid.UUID,
        current_state: str,
        session_state: str,
    ) -> None:
        """Update FSM state and session lifecycle state."""
        await self.db.execute(
            update(WorkflowSession)
            .where(WorkflowSession.session_id == session_id)
            .values(
                current_state=current_state,
                session_state=session_state,
                updated_at=datetime.now(timezone.utc),
            )
        )

    async def mark_completed(self, session_id: uuid.UUID) -> None:
        """Mark session as completed."""
        await self.db.execute(
            update(WorkflowSession)
            .where(WorkflowSession.session_id == session_id)
            .values(
                session_state="completed",
                completed_at=datetime.now(timezone.utc),
            )
        )

    async def list_by_tenant(
        self, tenant_id: str, limit: int = 50, offset: int = 0
    ) -> List[WorkflowSession]:
        """List sessions for a tenant."""
        result = await self.db.execute(
            select(WorkflowSession)
            .where(WorkflowSession.tenant_id == tenant_id)
            .order_by(WorkflowSession.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())


# ── Flow Repository ─────────────────────────────────────────

class FlowRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        flow_id: str,
        name: str,
        yaml_content: str,
        description: str = "",
        param_schema: Optional[Dict] = None,
    ) -> str:
        """Create or update a flow definition."""
        existing = await self.get(flow_id)
        if existing:
            await self.db.execute(
                update(WorkflowFlow)
                .where(WorkflowFlow.flow_id == flow_id)
                .values(
                    name=name,
                    yaml_content=yaml_content,
                    description=description,
                    param_schema=param_schema,
                    updated_at=datetime.now(timezone.utc),
                )
            )
        else:
            flow = WorkflowFlow(
                flow_id=flow_id,
                name=name,
                yaml_content=yaml_content,
                description=description,
                param_schema=param_schema,
            )
            self.db.add(flow)
        await self.db.flush()
        return flow_id

    async def get(self, flow_id: str) -> Optional[WorkflowFlow]:
        result = await self.db.execute(
            select(WorkflowFlow).where(WorkflowFlow.flow_id == flow_id)
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> List[WorkflowFlow]:
        result = await self.db.execute(
            select(WorkflowFlow).order_by(WorkflowFlow.created_at.desc())
        )
        return list(result.scalars().all())


# ── Event Repository (Audit Log) ────────────────────────────

class EventRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def append(
        self,
        event: TempoEvent,
        from_state: Optional[str] = None,
        to_state: Optional[str] = None,
    ) -> uuid.UUID:
        """Append an event to the audit log."""
        record = WorkflowEvent(
            event_id=uuid.UUID(event.id),
            tenant_id=event.tenant_id,
            session_id=uuid.UUID(event.session_id),
            event_type=event.type,
            source=event.source,
            target=event.target,
            tick=event.tick,
            trace_id=event.trace_id,
            priority=event.priority,
            from_state=from_state,
            to_state=to_state,
            payload=event.payload,
        )
        self.db.add(record)
        await self.db.flush()
        return record.event_id

    async def list_by_session(
        self, session_id: uuid.UUID, limit: int = 100
    ) -> List[WorkflowEvent]:
        """List events for a session (most recent first)."""
        result = await self.db.execute(
            select(WorkflowEvent)
            .where(WorkflowEvent.session_id == session_id)
            .order_by(WorkflowEvent.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def replay(self, session_id: uuid.UUID) -> List[WorkflowEvent]:
        """Replay all events for a session in chronological order."""
        result = await self.db.execute(
            select(WorkflowEvent)
            .where(WorkflowEvent.session_id == session_id)
            .order_by(WorkflowEvent.created_at.asc())
        )
        return list(result.scalars().all())


# ── Idempotency Repository ──────────────────────────────────

class IdempotencyRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def check(
        self, session_id: uuid.UUID, step: str, attempt: int
    ) -> bool:
        """Check if this execution has already been recorded."""
        result = await self.db.execute(
            select(IdempotencyLog).where(
                IdempotencyLog.session_id == session_id,
                IdempotencyLog.step == step,
                IdempotencyLog.attempt == attempt,
            )
        )
        return result.scalar_one_or_none() is not None

    async def record(
        self,
        session_id: uuid.UUID,
        step: str,
        attempt: int,
        status: str,
        result_hash: Optional[str] = None,
    ) -> None:
        """Record an execution attempt."""
        log = IdempotencyLog(
            session_id=session_id,
            step=step,
            attempt=attempt,
            status=status,
            result_hash=result_hash,
        )
        self.db.add(log)
        await self.db.flush()

    async def get_max_attempt(self, session_id: uuid.UUID, step: str) -> int:
        """Get the highest attempt number for a step."""
        from sqlalchemy import func
        result = await self.db.execute(
            select(func.max(IdempotencyLog.attempt)).where(
                IdempotencyLog.session_id == session_id,
                IdempotencyLog.step == step,
            )
        )
        val = result.scalar()
        return val or 0


# ── Node Registry Repository ────────────────────────────────

class NodeRegistryRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def register(
        self,
        node_id: str,
        node_type: str,
        name: str,
        description: str = "",
        endpoint: Optional[str] = None,
        param_schema: Optional[Dict] = None,
    ) -> str:
        """Register or update a node."""
        existing = await self.get(node_id)
        if existing:
            await self.db.execute(
                update(RegistryNode)
                .where(RegistryNode.node_id == node_id)
                .values(
                    node_type=node_type,
                    name=name,
                    description=description,
                    endpoint=endpoint,
                    param_schema=param_schema,
                    status="active",
                )
            )
        else:
            node = RegistryNode(
                node_id=node_id,
                node_type=node_type,
                name=name,
                description=description,
                endpoint=endpoint,
                param_schema=param_schema,
            )
            self.db.add(node)
        await self.db.flush()
        return node_id

    async def get(self, node_id: str) -> Optional[RegistryNode]:
        result = await self.db.execute(
            select(RegistryNode).where(RegistryNode.node_id == node_id)
        )
        return result.scalar_one_or_none()

    async def list_all(self, node_type: Optional[str] = None) -> List[RegistryNode]:
        query = select(RegistryNode)
        if node_type:
            query = query.where(RegistryNode.node_type == node_type)
        result = await self.db.execute(query.order_by(RegistryNode.node_id))
        return list(result.scalars().all())
