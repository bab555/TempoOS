# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
OSS Direct Upload Signing API (POST Policy).

Frontend uploads files directly to Aliyun OSS. Backend provides a short-lived
signed policy so the browser can do a secure form POST upload without
exposing AccessKeySecret.

This is a minimal implementation intended for Phase 1.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from tempo_os.api.deps import get_current_tenant
from tempo_os.core.config import settings
from tempo_os.core.tenant import TenantContext

router = APIRouter(prefix="/oss", tags=["oss"])


class PostSignatureRequest(BaseModel):
    """
    Request a signed POST policy for direct OSS upload.

    Note: This endpoint does NOT upload the file. It only returns signature fields.
    """

    filename: str = Field(..., description="Original filename, used to infer extension")
    content_type: Optional[str] = Field(None, description="MIME type (optional)")
    dir: Optional[str] = Field(
        None,
        description="Optional subdir under the generated prefix (e.g. templates/)",
    )
    expire_seconds: int = Field(default=600, ge=30, le=3600, description="Policy expiration seconds")


@router.post("/post-signature")
async def oss_post_signature(
    body: PostSignatureRequest,
    tenant: TenantContext = Depends(get_current_tenant),
) -> Dict[str, Any]:
    """
    Return OSS POST policy signature fields for browser direct upload.

    Frontend should upload using multipart/form-data to the returned `upload.url`
    with the returned `upload.fields`, plus the actual file as form field `file`.
    """
    if not (settings.OSS_ENDPOINT and settings.OSS_BUCKET and settings.OSS_ACCESS_KEY_ID and settings.OSS_ACCESS_KEY_SECRET):
        raise HTTPException(
            status_code=501,
            detail="OSS signing is not configured. Please set OSS_ENDPOINT/OSS_BUCKET/OSS_ACCESS_KEY_ID/OSS_ACCESS_KEY_SECRET.",
        )

    # Upload host: https://<bucket>.<endpoint>
    host = f"https://{settings.OSS_BUCKET}.{settings.OSS_ENDPOINT}".rstrip("/")

    # Generate object key prefix for isolation.
    # Example: tempoos/tenant/default/user/<uuid>/2026/02/17/
    user_part = tenant.user_id or "anonymous"
    now = dt.datetime.utcnow()
    date_prefix = now.strftime("%Y/%m/%d")

    safe_dir = (body.dir or "").lstrip("/").strip()
    if safe_dir and not safe_dir.endswith("/"):
        safe_dir += "/"

    key_prefix = f"{settings.OSS_UPLOAD_PREFIX}/tenant/{tenant.tenant_id}/user/{user_part}/{date_prefix}/{safe_dir}"
    object_key = f"{key_prefix}{uuid.uuid4().hex}_{body.filename}"

    # Expiration (ISO8601 UTC)
    expire_at = now + dt.timedelta(seconds=body.expire_seconds)
    expire_iso = expire_at.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    policy_dict = {
        "expiration": expire_iso,
        "conditions": [
            ["starts-with", "$key", key_prefix],
            {"bucket": settings.OSS_BUCKET},
            ["content-length-range", 1, int(settings.OSS_MAX_UPLOAD_SIZE)],
            {"success_action_status": "200"},
        ],
    }
    policy_json = _json_dumps(policy_dict).encode("utf-8")
    policy_b64 = base64.b64encode(policy_json).decode("utf-8")

    signature = base64.b64encode(
        hmac.new(
            settings.OSS_ACCESS_KEY_SECRET.encode("utf-8"),
            policy_b64.encode("utf-8"),
            hashlib.sha1,
        ).digest()
    ).decode("utf-8")

    fields = {
        "key": object_key,
        "policy": policy_b64,
        "OSSAccessKeyId": settings.OSS_ACCESS_KEY_ID,
        "success_action_status": "200",
        "signature": signature,
    }

    return {
        "upload": {
            "method": "POST",
            "url": host,
            "fields": fields,
            "expire_at": int(expire_at.timestamp()),
        },
        "object": {
            "bucket": settings.OSS_BUCKET,
            "endpoint": settings.OSS_ENDPOINT,
            "key": object_key,
            "url": f"{host}/{object_key}",
        },
    }


def _json_dumps(obj: Any) -> str:
    # Keep ensure_ascii=False for Chinese directory names if any.
    import json

    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))

