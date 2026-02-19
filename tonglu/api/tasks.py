# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Task Status API — Query async processing progress.

Phase 1: In-memory task store (shared with ingest.py).
Phase 2: Move to Redis for multi-process consistency.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from tonglu.api.ingest import get_task_store

router = APIRouter(prefix="/api", tags=["tasks"])


@router.get("/tasks/{task_id}")
async def get_task(task_id: str):
    """
    Query the processing status of an async task.

    Returns task metadata including status, record_id (when done), and error (if any).
    """
    task_store = get_task_store()
    task = task_store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task
