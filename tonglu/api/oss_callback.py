# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
OSS Upload Callback Webhook — Primary trigger for file processing.

When Aliyun OSS is configured with an upload callback, OSS will POST to
this endpoint after a file is successfully uploaded. Tonglu then:
  1. Parses the file from OSS.
  2. Publishes a FILE_READY event to the TempoOS EventBus.

This is the "fast path" for file processing. The EventBus FILE_UPLOADED
event from Agent Controller serves as a fallback if OSS callback is not
configured or fails.

OSS callback configuration (in OSS console or via SDK):
  callbackUrl: https://<tonglu-host>/api/oss/callback
  callbackBody: bucket=${bucket}&object=${object}&size=${size}&mimeType=${mimeType}&etag=${etag}
  callbackBodyType: application/x-www-form-urlencoded

Custom variables (set in the upload policy):
  x:tenant_id, x:session_id, x:user_id, x:file_id
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Form, HTTPException, Request

logger = logging.getLogger("tonglu.api.oss_callback")

router = APIRouter(prefix="/api/oss", tags=["oss_callback"])

# Event type constants (same as tempo_os/protocols/events.py)
FILE_READY = "FILE_READY"


@router.post("/callback")
async def oss_upload_callback(
    request: Request,
    bucket: str = Form(""),
    object: str = Form("", alias="object"),
    size: int = Form(0),
    mimeType: str = Form(""),
    etag: str = Form(""),
) -> Dict[str, Any]:
    """
    Receive OSS upload completion callback.

    OSS sends this after a file is successfully uploaded.
    We parse custom variables from the form to identify tenant/session context.
    """
    # Extract custom variables (x:tenant_id, x:session_id, etc.)
    form_data = await request.form()
    tenant_id = form_data.get("x:tenant_id", "default")
    session_id = form_data.get("x:session_id", "")
    user_id = form_data.get("x:user_id", "")
    file_id = form_data.get("x:file_id", str(uuid.uuid4()))

    if not session_id:
        logger.warning("OSS callback missing session_id, skipping EventBus publish")
        return {"status": "ok", "message": "no session_id, skipped"}

    # Reconstruct the OSS URL
    # Determine endpoint from app config
    from tonglu.config import get_settings
    settings = get_settings()
    oss_endpoint = getattr(settings, "OSS_ENDPOINT", "")
    if oss_endpoint:
        file_url = f"https://{bucket}.{oss_endpoint}/{object}"
    else:
        file_url = f"https://{bucket}.oss.aliyuncs.com/{object}"

    # Infer filename from object key
    file_name = object.rsplit("/", 1)[-1] if "/" in object else object

    logger.info(
        "OSS callback received: bucket=%s object=%s size=%d session=%s",
        bucket, object, size, session_id,
    )

    # Process the file through IngestionPipeline
    text_content = ""
    record_id = None
    error_msg = ""

    try:
        pipeline = request.app.state.pipeline
        repo = request.app.state.repo

        result = await pipeline.process(
            source_type="url",
            content_ref=file_url,
            file_name=file_name,
            tenant_id=tenant_id,
            metadata={
                "session_id": session_id,
                "file_id": file_id,
                "source": "oss_callback",
                "user_id": user_id,
                "etag": etag,
                "size": size,
            },
        )

        if result.status == "ready" and result.record_id:
            record_id = str(result.record_id)
            record = await repo.get_record(result.record_id)
            if record:
                parts = []
                if record.summary:
                    parts.append(record.summary)
                if record.data:
                    parts.append(json.dumps(record.data, ensure_ascii=False, indent=2))
                text_content = "\n".join(parts) if parts else "(文件已解析但无文本内容)"
            else:
                text_content = "(文件已处理但记录读取失败)"
        else:
            error_msg = result.error or "unknown error"
            text_content = f"(文件处理失败: {error_msg})"

    except Exception as e:
        logger.error("OSS callback processing failed: %s", e, exc_info=True)
        text_content = f"(文件处理异常: {str(e)})"
        error_msg = str(e)

    # Publish FILE_READY to EventBus
    try:
        import redis.asyncio as aioredis
        redis_url = getattr(settings, "REDIS_URL", "redis://localhost:6379/0")
        redis_conn = aioredis.from_url(redis_url, decode_responses=True)

        ready_payload = {
            "file_id": file_id,
            "file_url": file_url,
            "file_name": file_name,
            "text_content": text_content,
        }
        if record_id:
            ready_payload["record_id"] = record_id
        if error_msg:
            ready_payload["error"] = error_msg

        ready_event = {
            "id": str(uuid.uuid4()),
            "type": FILE_READY,
            "source": "tonglu_oss_callback",
            "target": "*",
            "tick": 0,
            "payload": ready_payload,
            "tenant_id": tenant_id,
            "session_id": session_id,
            "priority": 8,
        }

        channel = f"tempo:{tenant_id}:events"
        await redis_conn.publish(channel, json.dumps(ready_event, ensure_ascii=False))
        await redis_conn.aclose()

        logger.info(
            "FILE_READY published via OSS callback: session=%s file=%s",
            session_id, file_name,
        )

    except Exception as e:
        logger.error("Failed to publish FILE_READY from OSS callback: %s", e, exc_info=True)

    return {"status": "ok", "file_id": file_id}
