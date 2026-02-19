# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
ORM Models — PostgreSQL table definitions for TempoOS platform.

Tables:
  - workflow_sessions: Active/completed workflow sessions
  - workflow_flows: Registered YAML flow definitions
  - workflow_events: Audit log (append-only, replayable)
  - idempotency_log: At-least-once + idempotent execution tracking
  - registry_nodes: Registered nodes (builtin + webhook)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, String, Text, Integer, BigInteger, Boolean,
    DateTime, ForeignKey, Index, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB

from tempo_os.storage.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


def _genuuid():
    return uuid.uuid4()


# ── Workflow Sessions ───────────────────────────────────────

class WorkflowSession(Base):
    __tablename__ = "workflow_sessions"

    session_id = Column(UUID(as_uuid=True), primary_key=True, default=_genuuid)
    tenant_id = Column(String(64), nullable=False, index=True)
    flow_id = Column(String(128), nullable=True)
    current_state = Column(String(64), nullable=False, default="idle")
    session_state = Column(String(32), nullable=False, default="idle")  # idle/running/waiting_user/paused/completed/error
    params = Column(JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    ttl_seconds = Column(Integer, default=1800)

    def __repr__(self):
        return f"<Session {self.session_id} state={self.current_state}>"


# ── Workflow Flows ──────────────────────────────────────────

class WorkflowFlow(Base):
    __tablename__ = "workflow_flows"

    flow_id = Column(String(128), primary_key=True)
    name = Column(String(256), nullable=False)
    description = Column(Text, nullable=True)
    yaml_content = Column(Text, nullable=False)
    param_schema = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)

    def __repr__(self):
        return f"<Flow {self.flow_id}>"


# ── Workflow Events (Audit Log) ─────────────────────────────

class WorkflowEvent(Base):
    __tablename__ = "workflow_events"

    event_id = Column(UUID(as_uuid=True), primary_key=True, default=_genuuid)
    tenant_id = Column(String(64), nullable=False)
    session_id = Column(UUID(as_uuid=True), ForeignKey("workflow_sessions.session_id"), nullable=False)
    event_type = Column(String(64), nullable=False)
    source = Column(String(64), nullable=False)
    target = Column(String(64), nullable=True)
    tick = Column(BigInteger, default=0)
    trace_id = Column(String(128), nullable=True)
    priority = Column(Integer, default=5)
    from_state = Column(String(64), nullable=True)
    to_state = Column(String(64), nullable=True)
    payload = Column(JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    __table_args__ = (
        Index("idx_events_tenant_session", "tenant_id", "session_id", "created_at"),
    )

    def __repr__(self):
        return f"<Event {self.event_type} {self.from_state}→{self.to_state}>"


# ── Idempotency Log ────────────────────────────────────────

class IdempotencyLog(Base):
    __tablename__ = "idempotency_log"

    session_id = Column(UUID(as_uuid=True), nullable=False, primary_key=True)
    step = Column(String(64), nullable=False, primary_key=True)
    attempt = Column(Integer, nullable=False, default=1, primary_key=True)
    status = Column(String(32), nullable=False)  # pending/success/error
    result_hash = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    def __repr__(self):
        return f"<Idempotency {self.session_id}:{self.step}#{self.attempt}>"


# ── Registry Nodes ──────────────────────────────────────────

class RegistryNode(Base):
    __tablename__ = "registry_nodes"

    node_id = Column(String(128), primary_key=True)
    node_type = Column(String(32), nullable=False)  # 'builtin' | 'webhook'
    name = Column(String(256), nullable=False)
    description = Column(Text, nullable=True)
    endpoint = Column(String(512), nullable=True)  # webhook URL (webhook type only)
    param_schema = Column(JSONB, nullable=True)
    status = Column(String(32), default="active")
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    def __repr__(self):
        return f"<Node {self.node_id} ({self.node_type})>"
