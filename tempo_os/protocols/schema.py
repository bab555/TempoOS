# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
TempoOS Protocol Schema — The Nervous System.

Defines the canonical TempoEvent model used across the entire system.
Every message flowing through the Event Bus MUST conform to this schema.

Design decisions:
  - Mandatory `tenant_id` for multi-tenancy data isolation.
  - Mandatory `session_id` for end-to-end task tracing.
  - `type` field enforced UPPERCASE to prevent silent misrouting.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, field_validator


class TempoEvent(BaseModel):
    """
    Core event schema for TempoOS event bus.

    Every event emitted or consumed by any component (Kernel, Worker, Node)
    is an instance of TempoEvent. Serialization target is JSON (Redis / HTTP).
    """

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Globally unique event identifier (UUID v4)",
    )
    type: str = Field(
        ...,
        min_length=1,
        description="Event type constant — MUST be UPPERCASE",
    )
    source: str = Field(
        ...,
        min_length=1,
        description="Name of the component that emitted this event",
    )
    target: str = Field(
        default="*",
        description="Intended receiver (* = broadcast)",
    )
    tick: int = Field(
        default=0,
        ge=0,
        description="Logical clock tick at creation time",
    )
    payload: Dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary business data",
    )
    created_at: float = Field(
        default_factory=time.time,
        description="Unix timestamp of event creation",
    )
    tenant_id: str = Field(
        ...,
        min_length=1,
        description="[CRITICAL] Tenant ID for multi-tenancy isolation",
    )
    session_id: str = Field(
        ...,
        min_length=1,
        description="[CRITICAL] Session ID for task tracing",
    )
    trace_id: Optional[str] = Field(
        default=None,
        description="Optional distributed-trace ID",
    )
    priority: int = Field(
        default=5,
        ge=0,
        le=10,
        description="Event priority (0=lowest, 10=highest)",
    )

    # ── Validators ──────────────────────────────────────────────

    @field_validator("type")
    @classmethod
    def type_must_be_uppercase(cls, v: str) -> str:
        """Ensure event type is UPPERCASE to prevent silent misrouting."""
        if v != v.upper():
            raise ValueError(
                f"Event type must be UPPERCASE, got '{v}'. "
                f"Did you mean '{v.upper()}'?"
            )
        return v

    @field_validator("id")
    @classmethod
    def id_must_be_valid_uuid(cls, v: str) -> str:
        """Validate that id is a proper UUID string."""
        try:
            uuid.UUID(v)
        except ValueError:
            raise ValueError(f"Event id must be a valid UUID, got '{v}'")
        return v

    # ── Serialization Helpers ───────────────────────────────────

    def to_json(self) -> str:
        """Serialize to JSON string (for Redis / HTTP transport)."""
        return self.model_dump_json()

    @classmethod
    def from_json(cls, data: str) -> TempoEvent:
        """Deserialize from JSON string."""
        return cls.model_validate_json(data)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to plain dict (for Redis HSET)."""
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TempoEvent:
        """Reconstruct from plain dict."""
        return cls.model_validate(data)

    # ── Factory Methods ─────────────────────────────────────────

    @classmethod
    def create(
        cls,
        *,
        type: str,
        source: str,
        tenant_id: str,
        session_id: str,
        target: str = "*",
        tick: int = 0,
        payload: Optional[Dict[str, Any]] = None,
        priority: int = 5,
        trace_id: Optional[str] = None,
    ) -> TempoEvent:
        """Convenience factory with keyword-only arguments."""
        return cls(
            type=type,
            source=source,
            target=target,
            tick=tick,
            payload=payload or {},
            tenant_id=tenant_id,
            session_id=session_id,
            priority=priority,
            trace_id=trace_id,
        )

    # ── Display ─────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"TempoEvent(type={self.type!r}, source={self.source!r}, "
            f"target={self.target!r}, tick={self.tick}, "
            f"tenant={self.tenant_id!r})"
        )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "id": "550e8400-e29b-41d4-a716-446655440000",
                    "type": "CMD_EXECUTE",
                    "source": "kernel",
                    "target": "worker_sourcing",
                    "tick": 1024,
                    "payload": {"cmd": "find_suppliers", "query": "steel pipe"},
                    "created_at": 1738800000.0,
                    "tenant_id": "tenant_001",
                    "session_id": "session_abc",
                    "priority": 5,
                }
            ]
        }
    }
