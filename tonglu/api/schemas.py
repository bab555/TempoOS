# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
API Request/Response Schemas — Pydantic models for Tonglu HTTP API.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── Ingest ────────────────────────────────────────────────────


class IngestTextRequest(BaseModel):
    """Request body for POST /api/ingest/text."""
    data: Any = Field(..., description="Text content or JSON data to ingest")
    tenant_id: str = Field(..., description="Tenant scope")
    schema_type: Optional[str] = Field(None, description="Data type (auto-detected if omitted)")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")


class IngestBatchItem(BaseModel):
    """Single item in a batch ingest request."""
    source_type: str = Field(default="text")
    content_ref: str = Field(...)
    file_name: Optional[str] = None
    tenant_id: str = Field(default="default")
    schema_type: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class IngestBatchRequest(BaseModel):
    """Request body for POST /api/ingest/batch."""
    items: List[IngestBatchItem] = Field(..., max_length=20)


# ── Query ─────────────────────────────────────────────────────


class QueryRequest(BaseModel):
    """Request body for POST /api/query."""
    query: str = Field(..., description="Natural language query or keyword")
    mode: str = Field(default="hybrid", description="sql / vector / hybrid")
    filters: Optional[Dict[str, Any]] = Field(None, description="Pre-structured filters")
    tenant_id: str = Field(default="default")
    limit: int = Field(default=20, ge=1, le=100)
