# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Ingest API — File upload, text ingestion, and batch processing.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from dataclasses import asdict
from typing import Any, Dict

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from tonglu.api.schemas import IngestBatchRequest, IngestTextRequest

logger = logging.getLogger("tonglu.api.ingest")

router = APIRouter(prefix="/api", tags=["ingest"])

# ── Task Store (Phase 1: in-memory) ──────────────────────────
# Phase 2: move to Redis for multi-process consistency.
_task_store: Dict[str, Dict[str, Any]] = {}


def get_task_store() -> Dict[str, Dict[str, Any]]:
    """Expose task store for the tasks API."""
    return _task_store


# ── Endpoints ─────────────────────────────────────────────────


@router.post("/ingest/file")
async def ingest_file(
    request: Request,
    file: UploadFile = File(...),
    tenant_id: str = Form(...),
    schema_type: str = Form(None),
):
    """
    Upload a file for async processing.

    Returns a task_id that can be polled via GET /api/tasks/{task_id}.
    """
    pipeline = request.app.state.pipeline

    # Save uploaded file to temp directory
    file_path = await _save_upload(file)

    # Generate task ID
    task_id = str(uuid.uuid4())
    _task_store[task_id] = {
        "task_id": task_id,
        "status": "processing",
        "file_name": file.filename,
        "record_id": None,
        "error": None,
    }

    # Process in background (FastAPI BackgroundTasks alternative: use asyncio.create_task)
    import asyncio

    async def _bg_process():
        try:
            result = await pipeline.process(
                source_type="file",
                content_ref=file_path,
                file_name=file.filename,
                tenant_id=tenant_id,
                schema_type=schema_type if schema_type else None,
            )
            _task_store[task_id]["status"] = result.status
            _task_store[task_id]["record_id"] = str(result.record_id) if result.record_id else None
            _task_store[task_id]["source_id"] = str(result.source_id) if result.source_id else None
            if result.error:
                _task_store[task_id]["error"] = result.error
        except Exception as e:
            logger.error("Background file processing failed: %s", e, exc_info=True)
            _task_store[task_id]["status"] = "error"
            _task_store[task_id]["error"] = str(e)
        finally:
            # Clean up temp file
            try:
                os.unlink(file_path)
            except OSError:
                pass

    asyncio.create_task(_bg_process())

    return {
        "task_id": task_id,
        "status": "processing",
        "message": "文件已接收，正在处理",
    }


@router.post("/ingest/text")
async def ingest_text(body: IngestTextRequest, request: Request):
    """Ingest text or JSON data synchronously."""
    pipeline = request.app.state.pipeline

    content = json.dumps(body.data) if not isinstance(body.data, str) else body.data

    result = await pipeline.process(
        source_type="text",
        content_ref=content,
        tenant_id=body.tenant_id,
        schema_type=body.schema_type,
        metadata=body.metadata,
    )

    if result.status == "error":
        raise HTTPException(status_code=500, detail=result.error)

    return {
        "record_id": str(result.record_id),
        "source_id": str(result.source_id),
        "status": result.status,
    }


@router.post("/ingest/batch")
async def ingest_batch(body: IngestBatchRequest, request: Request):
    """
    Batch ingest (max 20 items).

    All items share the pipeline's Semaphore for concurrency control.
    """
    pipeline = request.app.state.pipeline

    if len(body.items) > 20:
        raise HTTPException(400, "单次批量最多 20 条")

    items = [item.model_dump() for item in body.items]
    results = await pipeline.process_batch(items)

    return {
        "results": [
            {
                "source_id": str(r.source_id) if r.source_id else None,
                "record_id": str(r.record_id) if r.record_id else None,
                "status": r.status,
                "error": r.error,
            }
            for r in results
        ],
        "total": len(results),
        "success": sum(1 for r in results if r.status == "ready"),
        "failed": sum(1 for r in results if r.status == "error"),
    }


# ── Helpers ───────────────────────────────────────────────────


async def _save_upload(file: UploadFile) -> str:
    """Save an uploaded file to a temp directory, return the path."""
    suffix = os.path.splitext(file.filename)[1] if file.filename else ""
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="tonglu_")
    try:
        content = await file.read()
        with os.fdopen(fd, "wb") as f:
            f.write(content)
    except Exception:
        os.close(fd)
        raise
    logger.debug("Saved upload to %s (%d bytes)", path, len(content))
    return path
