# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Query API — Unified search endpoint for SQL, vector, and hybrid queries.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from tonglu.api.schemas import QueryRequest

logger = logging.getLogger("tonglu.api.query")

router = APIRouter(prefix="/api", tags=["query"])


@router.post("/query")
async def query_data(body: QueryRequest, request: Request):
    """
    Unified query interface.

    Supports three modes:
    - sql:    JSONB field-level exact matching
    - vector: Semantic similarity search
    - hybrid: Merge SQL + Vector results (default)
    """
    engine = request.app.state.query_engine

    results = await engine.query(
        intent=body.query,
        mode=body.mode,
        filters=body.filters,
        tenant_id=body.tenant_id,
        limit=body.limit,
    )

    return {
        "results": results,
        "count": len(results),
        "mode": body.mode,
    }


@router.get("/records/{record_id}")
async def get_record(record_id: str, request: Request):
    """Get a single record by ID."""
    repo = request.app.state.repo

    try:
        uid = UUID(record_id)
    except ValueError:
        raise HTTPException(400, "Invalid record_id format")

    record = await repo.get_record(uid)
    if not record:
        raise HTTPException(404, "Record not found")

    return _serialize_record(record)


@router.get("/records")
async def list_records(
    request: Request,
    tenant_id: str = Query(...),
    schema_type: Optional[str] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    """List records with pagination and optional schema_type filter."""
    repo = request.app.state.repo

    records = await repo.list_records(
        tenant_id=tenant_id,
        schema_type=schema_type,
        offset=offset,
        limit=limit,
    )

    return {
        "records": [_serialize_record(r) for r in records],
        "offset": offset,
        "limit": limit,
    }


# ── Helpers ───────────────────────────────────────────────────


def _serialize_record(record: Any) -> Dict[str, Any]:
    """Convert a DataRecord ORM object to a JSON-serializable dict."""
    return {
        "id": str(record.id),
        "tenant_id": record.tenant_id,
        "source_id": str(record.source_id) if record.source_id else None,
        "schema_type": record.schema_type,
        "data": record.data,
        "summary": record.summary,
        "status": record.status,
        "processing_log": record.processing_log,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }
